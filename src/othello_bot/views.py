"""対局メッセージの Components V2 レイアウト。

ボタンはすべて :class:`GameButton`（DynamicItem）で作る。メッセージごとにビューを
保持せず、custom_id から操作を判別して :mod:`othello_bot.controller` に渡す。
"""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING

import discord
from discord import ui

from othello_bot.engine import COLUMNS, Color, square_name
from othello_bot.game import EndReason, Game, Mode, Phase
from othello_bot.render import ImageRequest, Images
from othello_bot.text_board import Tiles, board_text

if TYPE_CHECKING:
    from othello_bot.bot import OthelloBot

ACCENT = discord.Colour.from_rgb(46, 139, 87)
BOARD_FILE = "board.png"
REPLAY_FILE = "replay.gif"


class GameButton(ui.DynamicItem[ui.Button], template=r"oth:(?P<action>[a-z_]+):(?P<arg>\d*)"):
    def __init__(
        self,
        action: str,
        arg: int | None = None,
        *,
        label: str = "",
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            ui.Button(
                custom_id=f"oth:{action}:{'' if arg is None else arg}",
                label=label,
                style=style,
                disabled=disabled,
            )
        )
        self.action = action
        self.arg = arg

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Item, match: re.Match[str], /):
        return cls(match["action"], int(match["arg"]) if match["arg"] else None)

    async def callback(self, interaction: discord.Interaction[OthelloBot]) -> None:
        await interaction.client.controller.on_button(interaction, self.action, self.arg)


def _row(*buttons: GameButton) -> ui.ActionRow:
    return ui.ActionRow(*buttons)


def _players(game: Game, tiles: Tiles) -> str:
    lines = []
    for color in Color:
        seat = game.seat(color)
        if seat is not None:
            name = seat.label
        elif game.invited_id is not None:
            name = f"<@{game.invited_id}>（承諾待ち）"
        else:
            name = "（募集中）"
        count = f"　**{game.board.count(color)}**" if game.phase is not Phase.WAITING else ""
        lines.append(f"{tiles.stone(color)} {color.label} {name}{count}")
    return "\n".join(lines)


def _status(game: Game) -> str:
    turn = game.board.turn
    seat = game.seat(turn)
    lines = []
    if game.passed is not None:
        lines.append(f"{game.passed.label}は打てる場所がないためパスしました。")
    if game.draw_offer is not None:
        lines.append(f"{game.draw_offer.label}が引き分けを提案しています。")
    if seat is not None and seat.is_cpu:
        lines.append(f"**{seat.label}が考えています…**")
    elif game.selected_col is not None:
        lines.append(
            f"**{turn.label} {seat.label} の手番です。** {COLUMNS[game.selected_col]} 列の行を選んでください。"
        )
    else:
        lines.append(f"**{turn.label} {seat.label} の手番です。**")
    if game.last_move is not None:
        # 直前に打たれた石はまだ返されていないので、その色が打った側になる。
        mover = game.board.at(game.last_move)
        lines.append(f"-# {len(game.moves)} 手目: {mover.label} {square_name(game.last_move)}")
    return "\n".join(lines)


def _result(game: Game) -> str:
    black, white = game.board.count(Color.BLACK), game.board.count(Color.WHITE)
    score = f"黒 {black} - {white} 白"
    winner = game.winner
    win = f"**{winner.label} {game.seat(winner).label} の勝ち**" if winner else ""
    match game.end_reason:
        case EndReason.NORMAL:
            return f"{win}（{score}）" if winner else f"**引き分け**（{score}）"
        case EndReason.RESIGN:
            return f"{game.resigned.label}が投了しました。{win}"
        case EndReason.DRAW_AGREED:
            return f"合意により**引き分け**になりました。（{score}）"
        case EndReason.ABORTED:
            return "長時間操作がなかったため、対局を中断しました。"
    return ""


def _kifu(game: Game) -> str:
    if not game.moves:
        return "-# 棋譜: なし"
    # ほかのオセロのツールにそのまま貼れるよう、一般的な続け書きの形式にする。
    return f"-# 棋譜（{len(game.moves)} 手）: `{''.join(square_name(m) for m in game.moves)}`"


def _board_options(game: Game) -> dict:
    show_legal = game.phase is Phase.PLAYING and game.cpu_to_move is None
    return {"last_move": game.last_move, "show_legal": show_legal, "selected_col": game.selected_col}


def _shows_replay(game: Game) -> bool:
    return game.phase is Phase.FINISHED and bool(game.moves)


def image_request(game: Game) -> ImageRequest | None:
    """:func:`build` に渡す画像の指定。画像が要らなければ None。"""
    shows_board = game.phase in (Phase.PLAYING, Phase.FINISHED) and game.mode is Mode.IMAGE
    replay = tuple(game.moves) if _shows_replay(game) else ()
    if not shows_board and not replay:
        return None
    return ImageRequest(game.board if shows_board else None, replay=replay, **_board_options(game))


def _board_items(game: Game, tiles: Tiles, images: Images) -> tuple[list[ui.Item], list[discord.File]]:
    items: list[ui.Item] = []
    files: list[discord.File] = []
    if game.mode is Mode.TEXT:
        items.append(ui.TextDisplay(board_text(game.board, tiles, **_board_options(game))))
    else:
        files.append(discord.File(io.BytesIO(images.board), filename=BOARD_FILE))
        items.append(ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{BOARD_FILE}", description="盤面")))
    if _shows_replay(game):
        files.append(discord.File(io.BytesIO(images.replay), filename=REPLAY_FILE))
        items += [
            ui.TextDisplay("### リプレイ"),
            ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{REPLAY_FILE}", description="リプレイ")),
        ]
    return items, files


def _waiting_items(game: Game) -> list[ui.Item]:
    if game.invited_id is not None:
        text = f"{game.host.label} が <@{game.invited_id}> に対局を申し込みました。"
        buttons = _row(
            GameButton("accept", label="受ける", style=discord.ButtonStyle.success),
            GameButton("cancel", label="断る / 取り消す", style=discord.ButtonStyle.secondary),
        )
    else:
        text = f"{game.host.label} が対局相手を募集しています。"
        buttons = _row(
            GameButton("accept", label="参加する", style=discord.ButtonStyle.success),
            GameButton("cancel", label="取り消す", style=discord.ButtonStyle.secondary),
        )
    mode = "画像" if game.mode is Mode.IMAGE else "文字"
    return [
        ui.TextDisplay(f"{text}\n-# 表示: {mode}"),
        ui.Separator(),
        buttons,
    ]


def _closed_text(game: Game) -> str:
    match game.end_reason:
        case EndReason.CANCELLED:
            return f"{game.host.label} が申し込みを取り消しました。"
        case EndReason.DECLINED:
            return f"<@{game.invited_id}> が申し込みを断りました。"
    return "参加者がいなかったため、申し込みを締め切りました。"


def _move_buttons(game: Game) -> list[ui.ActionRow]:
    can_move = game.cpu_to_move is None
    if game.selected_col is None:
        legal = game.legal_columns() if can_move else set()
        buttons = [GameButton("col", i, label=c, disabled=i not in legal) for i, c in enumerate(COLUMNS)]
    else:
        legal = game.legal_rows(game.selected_col)
        buttons = [
            GameButton("row", r, label=str(r + 1), style=discord.ButtonStyle.primary, disabled=r not in legal)
            for r in range(8)
        ]
        buttons.append(GameButton("back", label="戻る"))
    return [_row(*buttons[:5]), _row(*buttons[5:])]


def _game_buttons(game: Game) -> ui.ActionRow:
    if game.draw_offer is not None:
        buttons = [
            GameButton("draw_yes", label="引き分けを承諾", style=discord.ButtonStyle.success),
            GameButton("draw_no", label="拒否 / 取り下げ"),
        ]
    elif not game.is_cpu_game:
        buttons = [GameButton("draw", label="引き分けを提案")]
    else:
        buttons = []
    buttons.append(GameButton("resign", label="投了", style=discord.ButtonStyle.danger))
    return _row(*buttons)


def build(game: Game, tiles: Tiles, images: Images) -> tuple[ui.LayoutView, list[discord.File]]:
    """対局の現在の状態と、:func:`image_request` に従って描いた画像からメッセージの中身を作る。"""
    items: list[ui.Item] = []
    files: list[discord.File] = []

    match game.phase:
        case Phase.WAITING:
            items += [ui.TextDisplay(f"### オセロ\n{_players(game, tiles)}"), *_waiting_items(game)]
        case Phase.CLOSED:
            items += [ui.TextDisplay(f"### オセロ\n{_closed_text(game)}")]
        case Phase.PLAYING:
            board, files = _board_items(game, tiles, images)
            items += [
                ui.TextDisplay(f"### オセロ\n{_players(game, tiles)}"),
                ui.TextDisplay(_status(game)),
                *board,
                ui.TextDisplay(_kifu(game)),
                ui.Separator(),
                *_move_buttons(game),
                _game_buttons(game),
            ]
        case Phase.FINISHED:
            board, files = _board_items(game, tiles, images)
            items += [
                ui.TextDisplay(f"### オセロ 終局\n{_players(game, tiles)}"),
                ui.TextDisplay(_result(game)),
                *board,
                ui.TextDisplay(_kifu(game)),
                ui.Separator(),
                _row(
                    GameButton("rematch_swap", label="色を入れ替えて再戦", style=discord.ButtonStyle.primary),
                    GameButton("rematch_same", label="同じ色で再戦"),
                ),
            ]

    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(*items, accent_colour=ACCENT))
    return view, files


def resign_confirm(game: Game) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    view.add_item(
        ui.Container(
            ui.TextDisplay("本当に投了しますか？"),
            _row(GameButton("resign_yes", game.message_id, label="投了する", style=discord.ButtonStyle.danger)),
        )
    )
    return view


def notice(text: str) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(ui.TextDisplay(text)))
    return view


HELP_TEXT = """\
### オセロ Bot の使い方
**対局を始める**
`/othello play` 誰でも参加できる対局相手を募集します。
`/othello play opponent:@相手` 指定した相手に対局を申し込みます。
`/othello cpu` CPU と対局します。強さ（弱い・普通・強い）と手番を選べます。
コマンドを実行した人が先手（黒）です。CPU 戦では後手（白）も選べます。
`display` で盤面の表示を「画像」か「文字」から選べます。
終局したメッセージの再戦ボタンで、同じ相手ともう一度対局できます。色を入れ替えるか、同じ色のままかを選べます。対人戦では相手の承諾を待ちます。

**石を打つ**
盤面の下の列ボタン（a〜h）を押してから、行ボタン（1〜8）を押します。打てない列・行のボタンは押せません。
`/othello place 座標`（例: `d3`）でも打てます。入力欄には打てる場所が候補として出ます。

**ルール**
相手の石を縦・横・斜めに挟める場所にだけ打てます。挟んだ石は自分の色になります。
打てる場所がないときは自動でパスになり、両者とも打てなくなったら終局です。石の多い方が勝ちです。

-# 1 人が同じチャンネルで同時に参加できる対局は 1 つまでです。24 時間操作がない対局は中断されます。"""
