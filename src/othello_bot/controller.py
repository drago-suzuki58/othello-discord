"""コマンドとボタン操作を対局の状態に反映し、対局メッセージを更新する。"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import time
import unicodedata
from concurrent.futures import ProcessPoolExecutor
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from othello_bot.ai import Level, choose_move
from othello_bot.engine import Color, parse_square, square_name
from othello_bot.game import Game, GameError, Mode, Phase, Registry, Seat
from othello_bot.render import Images, render_images
from othello_bot.store import Store
from othello_bot.views import build, image_request, notice, resign_confirm

if TYPE_CHECKING:
    from othello_bot.bot import OthelloBot

log = logging.getLogger(__name__)

IDLE_TIMEOUT = 24 * 60 * 60
CPU_MIN_DELAY = 0.8  # CPU が一瞬で打つと何が起きたか追いにくいので、最低限待つ
POOL_SIZE = 2


async def _error(interaction: discord.Interaction, text: str) -> None:
    await interaction.response.send_message(text, ephemeral=True)


def _jump_url(game: Game) -> str:
    return f"https://discord.com/channels/{game.guild_id}/{game.channel_id}/{game.message_id}"


class Controller:
    def __init__(self, bot: OthelloBot, store: Store) -> None:
        self.bot = bot
        self.registry = Registry()  # 進行中の対局。終局したものも含めてすべて store に保存する。
        self.store = store
        # CPU の探索と画像の生成は重いので、GIL を取り合ってイベントループを止めないよう別プロセスで行う。
        # 探索の 2 秒間に描画が待たされないよう、プールを分ける。
        context = multiprocessing.get_context("spawn")
        self._cpu_pool = ProcessPoolExecutor(POOL_SIZE, mp_context=context)
        self._render_pool = ProcessPoolExecutor(POOL_SIZE, mp_context=context)

    def warm_up(self) -> None:
        """最初の操作でプロセスの起動を待たないよう、先に起動しておく。"""
        for pool in (self._cpu_pool, self._render_pool):
            pool.submit(int)

    def close(self) -> None:
        for pool in (self._cpu_pool, self._render_pool):
            pool.shutdown(wait=False, cancel_futures=True)
        self.store.close()

    async def resume(self) -> None:
        """保存してある進行中の対局を読み込み、メッセージを今の状態で描き直す。CPU の手番なら続きを打つ。"""
        games = self.store.active_games()
        for game in games:
            self.registry.add(game)
        for game in games:
            try:
                await self.push(game)
            except discord.HTTPException:
                log.exception("failed to redraw resumed game %s", game.message_id)

    # --- 対局の開始 ---

    async def start_vs_player(self, interaction: discord.Interaction, opponent: discord.Member | None, mode: Mode):
        user = interaction.user
        if opponent is not None:
            if opponent.bot:
                return await _error(
                    interaction, "Bot とは対局できません。CPU と対局するには `/othello cpu` を使ってください。"
                )
            if opponent.id == user.id:
                return await _error(interaction, "自分自身には申し込めません。")
            if busy := self.registry.active_for(interaction.channel_id, opponent.id):
                return await _error(interaction, f"{opponent.mention} はこのチャンネルで対局中です: {_jump_url(busy)}")
        game = Game(
            interaction.guild_id,
            interaction.channel_id,
            host=Seat(user.id),
            host_color=Color.BLACK,
            mode=mode,
            invited_id=opponent.id if opponent else None,
        )
        mentions = discord.AllowedMentions(users=[opponent]) if opponent else None
        await self._start(interaction, game, mentions)

    async def start_vs_cpu(self, interaction: discord.Interaction, level: Level, color: Color, mode: Mode):
        game = Game.against_cpu(interaction.guild_id, interaction.channel_id, interaction.user.id, color, level, mode)
        await self._start(interaction, game)

    async def _start(
        self, interaction: discord.Interaction, game: Game, mentions: discord.AllowedMentions | None = None
    ) -> None:
        if busy := self.registry.active_for(game.channel_id, game.host.user_id):
            return await _error(interaction, f"このチャンネルで参加中の対局があります: {_jump_url(busy)}")
        # 送信を待つ間に同じ人がもう一度コマンドを実行しても弾けるよう、先に登録する。
        self.registry.add(game)
        try:
            view, files = await self._build(game)
            await interaction.response.send_message(
                view=view, files=files, allowed_mentions=mentions or discord.AllowedMentions.none()
            )
            message = await interaction.original_response()
        except BaseException:
            self.registry.remove(game)
            raise
        game.message_id = message.id
        self.store.save(game)
        self._after_update(game)

    # --- 着手コマンド ---

    async def place(self, interaction: discord.Interaction, position: str) -> None:
        game = self.registry.active_for(interaction.channel_id, interaction.user.id)
        if game is None or game.phase is not Phase.PLAYING:
            return await _error(interaction, "このチャンネルで進行中の対局がありません。")
        square = parse_square(position)
        if square is None:
            return await _error(interaction, "座標は `d3` のように、列（a〜h）と行（1〜8）で指定してください。")
        try:
            game.play(interaction.user.id, square)
        except GameError as e:
            return await _error(interaction, str(e))
        # 応答を残さないよう、保留してから対局メッセージを更新し、応答を消す。
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.push(game)
        await interaction.delete_original_response()

    def place_choices(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        game = self.registry.active_for(interaction.channel_id, interaction.user.id)
        if game is None or game.phase is not Phase.PLAYING:
            return []
        if game.color_of(interaction.user.id) is not game.board.turn:
            return []
        prefix = unicodedata.normalize("NFKC", current).strip().lower()
        choices = []
        for square in game.board.legal_moves():
            name = square_name(square)
            if name.startswith(prefix):
                choices.append(
                    app_commands.Choice(name=f"{name}（{game.board.flip_count(square)} 枚返す）", value=name)
                )
        return choices[:25]

    # --- ボタン ---

    async def on_button(self, interaction: discord.Interaction, action: str, arg: int | None) -> None:
        if action == "resign_yes":
            return await self._resign(interaction, arg)

        game = self.registry.by_message(interaction.message.id)
        if game is None:
            return await _error(interaction, "この対局は終了しています。")
        user_id = interaction.user.id
        try:
            match action:
                case "accept":
                    if (busy := self.registry.active_for(game.channel_id, user_id)) and busy is not game:
                        raise GameError(f"このチャンネルで参加中の対局があります: {_jump_url(busy)}")
                    game.accept(user_id)
                case "cancel":
                    game.cancel(user_id)
                case "col":
                    game.select_column(user_id, arg)
                case "row":
                    game.play_row(user_id, arg)
                case "back":
                    game.clear_selection(user_id)
                case "draw":
                    game.offer_draw(user_id)
                case "draw_yes" | "draw_no":
                    game.answer_draw(user_id, accept=action == "draw_yes")
                case "resign":
                    if game.color_of(user_id) is None:
                        raise GameError("この対局の対局者ではありません。")
                    return await interaction.response.send_message(view=resign_confirm(game), ephemeral=True)
                case _:
                    raise GameError("不明な操作です。")
        except GameError as e:
            return await _error(interaction, str(e))

        async with game.lock:
            self.store.save(game)
            if game.phase is Phase.FINISHED:
                # リプレイ GIF の生成と送信は応答の期限（3 秒）を超えうるので、先に応答を保留する。
                await interaction.response.defer()
            view, files = await self._build(game)
            if interaction.response.is_done():
                await interaction.edit_original_response(view=view, attachments=files)
            else:
                await interaction.response.edit_message(view=view, attachments=files)
        self._after_update(game)

    async def _resign(self, interaction: discord.Interaction, message_id: int | None) -> None:
        game = self.registry.by_message(message_id) if message_id else None
        try:
            if game is None:
                raise GameError("この対局は既に終わっています。")
            game.resign(interaction.user.id)
        except GameError as e:
            return await interaction.response.edit_message(view=notice(str(e)))
        await interaction.response.edit_message(view=notice("投了しました。"))
        await self.push(game)

    # --- 更新 ---

    async def _build(self, game: Game) -> tuple[discord.ui.LayoutView, list[discord.File]]:
        loop = asyncio.get_running_loop()
        while True:
            request = image_request(game)
            images = Images()
            if request is not None:
                images = await loop.run_in_executor(self._render_pool, render_images, request)
            # 描いている間にほかの操作で状態が変わっていたら、画像と表示が食い違わないよう描き直す。
            if image_request(game) == request:
                return build(game, self.bot.tiles, images)

    async def push(self, game: Game) -> None:
        """インタラクションの応答とは別に、対局メッセージを最新の状態にする。"""
        message = self.bot.get_partial_messageable(game.channel_id).get_partial_message(game.message_id)
        async with game.lock:
            self.store.save(game)
            view, files = await self._build(game)
            try:
                await message.edit(view=view, attachments=files)
            except (discord.NotFound, discord.Forbidden):
                log.warning("game message %s is no longer editable; dropping the game", game.message_id)
                self.forget(game.message_id)
                return
        self._after_update(game)

    def _after_update(self, game: Game) -> None:
        if not game.is_active:
            self.registry.remove(game)
        elif game.cpu_to_move and (game.cpu_task is None or game.cpu_task.done()):
            game.cpu_task = asyncio.create_task(self._run_cpu(game))

    async def _run_cpu(self, game: Game) -> None:
        try:
            while seat := game.cpu_to_move:
                started = time.monotonic()
                board = game.board
                move = await asyncio.get_running_loop().run_in_executor(self._cpu_pool, choose_move, board, seat.cpu)
                await asyncio.sleep(max(0.0, CPU_MIN_DELAY - (time.monotonic() - started)))
                # 考えている間に投了などで局面が変わっていたら打たない。
                if game.board is not board or game.cpu_to_move is None:
                    return
                game.play_cpu(move)
                await self.push(game)
        except Exception:
            log.exception("CPU turn failed in game %s", game.message_id)

    def forget(self, message_id: int) -> None:
        """対局メッセージが消えた対局を、進行中かどうかにかかわらず忘れる。"""
        if game := self.registry.by_message(message_id):
            self.registry.remove(game)
        self.store.delete(message_id)

    async def expire_idle(self) -> None:
        for game in self.registry.idle_since(time.time() - IDLE_TIMEOUT):
            game.abort()
            try:
                await self.push(game)
            except discord.HTTPException:
                log.exception("failed to update expired game %s", game.message_id)
                self.registry.remove(game)
