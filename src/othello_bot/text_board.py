"""文字モードの盤面。マスとラベルを Bot のアプリケーション絵文字で表す。"""

from __future__ import annotations

import logging
import re

import discord

from othello_bot.engine import COLUMNS, Board, Color
from othello_bot.render import TILE_KINDS, render_tile

log = logging.getLogger(__name__)

# タイルの見た目を変えたら版を上げる。起動時に新しい版を登録し、古い版を削除する。
TILE_VERSION = 1
_NAME_PATTERN = re.compile(r"^oth(\d+)_(\w+)$")


def _emoji_name(kind: str) -> str:
    return f"oth{TILE_VERSION}_{kind}"


class Tiles:
    """タイル種別から、メッセージに埋め込む絵文字の文字列を引く。"""

    def __init__(self, emojis: dict[str, str]) -> None:
        self._emojis = emojis

    def __getitem__(self, kind: str) -> str:
        return self._emojis[kind]

    def stone(self, color: Color) -> str:
        return self[color.value]


async def sync_tiles(client: discord.Client) -> Tiles:
    """足りないタイルをアプリケーション絵文字として登録し、古い版を削除する。"""
    existing = {emoji.name: emoji for emoji in await client.fetch_application_emojis()}

    for name, emoji in existing.items():
        match = _NAME_PATTERN.match(name)
        if match and int(match[1]) != TILE_VERSION:
            log.info("deleting outdated tile emoji %s", name)
            await emoji.delete()

    emojis: dict[str, str] = {}
    for kind in TILE_KINDS:
        name = _emoji_name(kind)
        emoji = existing.get(name)
        if emoji is None:
            log.info("creating tile emoji %s", name)
            emoji = await client.create_application_emoji(name=name, image=render_tile(kind))
        emojis[kind] = str(emoji)
    return Tiles(emojis)


def board_text(
    board: Board,
    tiles: Tiles,
    *,
    last_move: int | None = None,
    show_legal: bool = True,
    selected_col: int | None = None,
) -> str:
    header = [tiles["corner"]] + [
        tiles[f"col_{c}_sel" if i == selected_col else f"col_{c}"] for i, c in enumerate(COLUMNS)
    ]
    lines = ["".join(header)]
    legal = board.legal_mask() if show_legal else 0
    legal_tile = f"legal_{board.turn.value}"
    for row in range(8):
        cells = [tiles[f"row_{row + 1}"]]
        for col in range(8):
            square = row * 8 + col
            stone = board.at(square)
            if stone is not None:
                cells.append(tiles[f"last_{stone.value}" if square == last_move else stone.value])
            elif legal >> square & 1:
                cells.append(tiles[legal_tile])
            else:
                cells.append(tiles["empty"])
        lines.append("".join(cells))
    return "\n".join(lines)
