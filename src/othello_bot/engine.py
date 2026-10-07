"""オセロの盤面ロジック。

盤面はビットボードで持つ。マス番号は ``row * 8 + col`` で、a1 が 0、h8 が 63。
外部とは :class:`Board` とマス番号・座標表記（``"d3"`` など）でやり取りする。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum

FULL = (1 << 64) - 1
_NOT_A = 0xFEFEFEFEFEFEFEFE  # a 列を除く
_NOT_H = 0x7F7F7F7F7F7F7F7F  # h 列を除く

# (シフト量, シフト後に適用するマスク)。列をまたぐ回り込みをマスクで防ぐ。
_DIRECTIONS = (
    (1, _NOT_A),
    (-1, _NOT_H),
    (8, FULL),
    (-8, FULL),
    (9, _NOT_A),
    (7, _NOT_H),
    (-7, _NOT_A),
    (-9, _NOT_H),
)

COLUMNS = "abcdefgh"


def _shift(bits: int, amount: int, mask: int) -> int:
    if amount > 0:
        return (bits << amount) & mask & FULL
    return (bits >> -amount) & mask


def legal_mask(player: int, opponent: int) -> int:
    """手番側 ``player`` が打てるマスのビット集合を返す。"""
    empty = ~(player | opponent) & FULL
    moves = 0
    for amount, mask in _DIRECTIONS:
        line = _shift(player, amount, mask) & opponent
        for _ in range(5):
            line |= _shift(line, amount, mask) & opponent
        moves |= _shift(line, amount, mask) & empty
    return moves


def flip_mask(move: int, player: int, opponent: int) -> int:
    """``move``（1 ビット）に打ったときに返る石のビット集合を返す。"""
    flipped = 0
    for amount, mask in _DIRECTIONS:
        line = 0
        cursor = _shift(move, amount, mask)
        while cursor & opponent:
            line |= cursor
            cursor = _shift(cursor, amount, mask)
        if cursor & player:
            flipped |= line
    return flipped


def iter_squares(bits: int) -> Iterator[int]:
    while bits:
        lowest = bits & -bits
        yield lowest.bit_length() - 1
        bits ^= lowest


def square_name(square: int) -> str:
    return f"{COLUMNS[square % 8]}{square // 8 + 1}"


def parse_square(text: str) -> int | None:
    """``"d3"`` のような座標をマス番号にする。全角や大文字も受け付ける。"""
    normalized = unicodedata.normalize("NFKC", text).strip().lower()
    if len(normalized) != 2:
        return None
    col = COLUMNS.find(normalized[0])
    if col < 0 or normalized[1] not in "12345678":
        return None
    return (int(normalized[1]) - 1) * 8 + col


class Color(Enum):
    BLACK = "black"
    WHITE = "white"

    @property
    def opponent(self) -> Color:
        return Color.WHITE if self is Color.BLACK else Color.BLACK

    @property
    def label(self) -> str:
        return "黒" if self is Color.BLACK else "白"


_INITIAL_BLACK = (1 << 28) | (1 << 35)  # e4, d5
_INITIAL_WHITE = (1 << 27) | (1 << 36)  # d4, e5


@dataclass(frozen=True, slots=True)
class Board:
    """不変の盤面。:meth:`play` はパスを自動で処理した新しい盤面を返す。"""

    black: int = _INITIAL_BLACK
    white: int = _INITIAL_WHITE
    turn: Color = Color.BLACK

    def stones(self, color: Color) -> int:
        return self.black if color is Color.BLACK else self.white

    def at(self, square: int) -> Color | None:
        bit = 1 << square
        if self.black & bit:
            return Color.BLACK
        if self.white & bit:
            return Color.WHITE
        return None

    def count(self, color: Color) -> int:
        return self.stones(color).bit_count()

    def legal_mask(self) -> int:
        return legal_mask(self.stones(self.turn), self.stones(self.turn.opponent))

    def legal_moves(self) -> list[int]:
        return list(iter_squares(self.legal_mask()))

    def is_legal(self, square: int) -> bool:
        return 0 <= square < 64 and bool(self.legal_mask() >> square & 1)

    def flip_count(self, square: int) -> int:
        player, opponent = self.stones(self.turn), self.stones(self.turn.opponent)
        return flip_mask(1 << square, player, opponent).bit_count()

    @property
    def is_over(self) -> bool:
        # play() が自動でパスするため、手番側が打てなければ両者とも打てない。
        return self.legal_mask() == 0

    @property
    def winner(self) -> Color | None:
        black, white = self.count(Color.BLACK), self.count(Color.WHITE)
        if black == white:
            return None
        return Color.BLACK if black > white else Color.WHITE

    def play(self, square: int) -> Board:
        if not self.is_legal(square):
            raise ValueError(f"illegal move: {square_name(square)}")
        move = 1 << square
        player, opponent = self.stones(self.turn), self.stones(self.turn.opponent)
        flipped = flip_mask(move, player, opponent)
        player |= move | flipped
        opponent &= ~flipped
        if self.turn is Color.BLACK:
            black, white = player, opponent
        else:
            black, white = opponent, player

        next_turn = self.turn.opponent
        # 相手が打てず自分が打てるなら、相手はパスになる。
        if not legal_mask(opponent, player) and legal_mask(player, opponent):
            next_turn = self.turn
        return Board(black, white, next_turn)


def replay(moves: list[int]) -> list[Board]:
    """初期局面から棋譜をたどり、各手を打った後の局面を初期局面込みで返す。"""
    boards = [Board()]
    for move in moves:
        boards.append(boards[-1].play(move))
    return boards
