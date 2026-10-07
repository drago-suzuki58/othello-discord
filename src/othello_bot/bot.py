"""Discord クライアントとスラッシュコマンド。"""

from __future__ import annotations

import os
import sys

import discord
from discord import app_commands
from discord.ext import tasks

from othello_bot.ai import Level
from othello_bot.controller import Controller
from othello_bot.engine import Color
from othello_bot.game import Mode
from othello_bot.store import Store
from othello_bot.text_board import Tiles, sync_tiles
from othello_bot.views import HELP_TEXT, GameButton, notice


class OthelloBot(discord.Client):
    tiles: Tiles

    def __init__(self, db_path: str) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True  # 対局メッセージの削除を検知する
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none(), max_messages=None)
        self.tree = app_commands.CommandTree(self)
        self.controller = Controller(self, Store(db_path))

    async def setup_hook(self) -> None:
        self.controller.warm_up()
        self.tiles = await sync_tiles(self)
        self.add_dynamic_items(GameButton)
        self.tree.add_command(othello)
        await self.tree.sync()
        await self.controller.resume()
        self.expire_idle.start()

    async def close(self) -> None:
        await super().close()
        self.controller.close()

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        self.controller.forget(payload.message_id)

    @tasks.loop(minutes=5)
    async def expire_idle(self) -> None:
        await self.controller.expire_idle()


DISPLAY_CHOICES = [
    app_commands.Choice(name="画像", value=Mode.IMAGE.value),
    app_commands.Choice(name="文字（絵文字）", value=Mode.TEXT.value),
]


def _mode(choice: app_commands.Choice[str] | None) -> Mode:
    return Mode(choice.value) if choice else Mode.IMAGE


othello = app_commands.Group(name="othello", description="オセロで対局します", guild_only=True)


@othello.command(name="play", description="ほかのメンバーと対局します。実行した人が先手（黒）です")
@app_commands.describe(
    opponent="対局を申し込む相手。省略すると誰でも参加できる募集になります",
    display="盤面の表示方法（省略時は画像）",
)
@app_commands.choices(display=DISPLAY_CHOICES)
async def play(
    interaction: discord.Interaction[OthelloBot],
    opponent: discord.Member | None = None,
    display: app_commands.Choice[str] | None = None,
) -> None:
    await interaction.client.controller.start_vs_player(interaction, opponent, _mode(display))


@othello.command(name="cpu", description="CPU と対局します")
@app_commands.describe(
    level="CPU の強さ（省略時は普通）",
    color="自分の手番（省略時は先手・黒）",
    display="盤面の表示方法（省略時は画像）",
)
@app_commands.choices(
    level=[app_commands.Choice(name=level.label, value=level.value) for level in Level],
    color=[
        app_commands.Choice(name="先手（黒）", value=Color.BLACK.value),
        app_commands.Choice(name="後手（白）", value=Color.WHITE.value),
    ],
    display=DISPLAY_CHOICES,
)
async def cpu(
    interaction: discord.Interaction[OthelloBot],
    level: app_commands.Choice[str] | None = None,
    color: app_commands.Choice[str] | None = None,
    display: app_commands.Choice[str] | None = None,
) -> None:
    await interaction.client.controller.start_vs_cpu(
        interaction,
        Level(level.value) if level else Level.NORMAL,
        Color(color.value) if color else Color.BLACK,
        _mode(display),
    )


@othello.command(name="place", description="座標を指定して石を打ちます")
@app_commands.describe(position="打つ場所（例: d3）")
async def place(interaction: discord.Interaction[OthelloBot], position: str) -> None:
    await interaction.client.controller.place(interaction, position)


@place.autocomplete("position")
async def place_autocomplete(
    interaction: discord.Interaction[OthelloBot], current: str
) -> list[app_commands.Choice[str]]:
    return interaction.client.controller.place_choices(interaction, current)


@othello.command(name="help", description="遊び方を表示します")
async def help_(interaction: discord.Interaction[OthelloBot]) -> None:
    await interaction.response.send_message(view=notice(HELP_TEXT), ephemeral=True)


def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        sys.exit("環境変数 DISCORD_TOKEN に Bot のトークンを設定してください。")
    OthelloBot(os.environ.get("OTHELLO_DB", "othello.db")).run(token)
