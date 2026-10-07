"""CPU の着手選択。"""

from __future__ import annotations

import random
import time
from enum import Enum

from othello_bot.engine import FULL, Board, flip_mask, iter_squares, legal_mask


class Level(Enum):
    WEAK = "weak"
    NORMAL = "normal"
    STRONG = "strong"

    @property
    def label(self) -> str:
        return {Level.WEAK: "弱い", Level.NORMAL: "普通", Level.STRONG: "強い"}[self]


# 角を高く、角の隣（C 打ち・X 打ち）を低く評価する一般的な重み。
WEIGHTS = (
    100, -20, 10, 5, 5, 10, -20, 100,
    -20, -50, -2, -2, -2, -2, -50, -20,
    10, -2, -1, -1, -1, -1, -2, 10,
    5, -2, -1, -1, -1, -1, -2, 5,
    5, -2, -1, -1, -1, -1, -2, 5,
    10, -2, -1, -1, -1, -1, -2, 10,
    -20, -50, -2, -2, -2, -2, -50, -20,
    100, -20, 10, 5, 5, 10, -20, 100,
)  # fmt: skip

# 行ごとに 1 バイト分の重みの合計を前計算しておく。
_ROW_TABLES = tuple(
    tuple(sum(WEIGHTS[row * 8 + col] for col in range(8) if value >> col & 1) for value in range(256))
    for row in range(8)
)

_MOBILITY_WEIGHT = 8
_WIN_SCORE = 10_000
_NORMAL_DEPTH = 2
_STRONG_TIME_LIMIT = 2.0


def _positional(player: int, opponent: int) -> int:
    score = 0
    for row, table in enumerate(_ROW_TABLES):
        shift = row * 8
        score += table[player >> shift & 0xFF] - table[opponent >> shift & 0xFF]
    return score


def _with_mobility(player: int, opponent: int) -> int:
    mobility = legal_mask(player, opponent).bit_count() - legal_mask(opponent, player).bit_count()
    return _positional(player, opponent) + _MOBILITY_WEIGHT * mobility


def _final(player: int, opponent: int) -> int:
    return (player.bit_count() - opponent.bit_count()) * _WIN_SCORE


def _ordered(moves: int) -> list[int]:
    return sorted(iter_squares(moves), key=lambda square: -WEIGHTS[square])


class _Timeout(Exception):
    pass


class _Search:
    def __init__(self, evaluate, deadline: float) -> None:
        self.evaluate = evaluate
        self.deadline = deadline
        self.nodes = 0

    def negamax(self, player: int, opponent: int, depth: int, alpha: int, beta: int) -> int:
        self.nodes += 1
        if self.nodes & 1023 == 0 and time.monotonic() > self.deadline:
            raise _Timeout
        if depth == 0:
            if player | opponent == FULL:
                return _final(player, opponent)
            return self.evaluate(player, opponent)

        moves = legal_mask(player, opponent)
        if not moves:
            if not legal_mask(opponent, player):
                return _final(player, opponent)
            return -self.negamax(opponent, player, depth, -beta, -alpha)

        best = -_WIN_SCORE * 100
        for square in _ordered(moves):
            bit = 1 << square
            flipped = flip_mask(bit, player, opponent)
            score = -self.negamax(opponent & ~flipped, player | flipped | bit, depth - 1, -beta, -alpha)
            if score > best:
                best = score
                if score > alpha:
                    alpha = score
                    if alpha >= beta:
                        break
        return best

    def root(self, player: int, opponent: int, moves: list[int], depth: int) -> tuple[int, int]:
        best_move, best_score = moves[0], -_WIN_SCORE * 100
        alpha, beta = -_WIN_SCORE * 100, _WIN_SCORE * 100
        for square in moves:
            bit = 1 << square
            flipped = flip_mask(bit, player, opponent)
            score = -self.negamax(opponent & ~flipped, player | flipped | bit, depth - 1, -beta, -alpha)
            if score > best_score:
                best_move, best_score = square, score
                alpha = max(alpha, score)
        return best_move, best_score


def choose_move(board: Board, level: Level, rng: random.Random | None = None) -> int:
    """手番側の着手を選ぶ。打てる手がない盤面では呼ばないこと。"""
    rng = rng or random.Random()
    moves = board.legal_moves()
    if not moves:
        raise ValueError("no legal moves")
    if len(moves) == 1 or level is Level.WEAK:
        return rng.choice(moves)

    player, opponent = board.stones(board.turn), board.stones(board.turn.opponent)
    moves = _ordered(board.legal_mask())

    if level is Level.NORMAL:
        search = _Search(_positional, deadline=float("inf"))
        return search.root(player, opponent, moves, _NORMAL_DEPTH)[0]

    # 強い: 時間内で反復深化する。残りマス数まで読めればそのまま終盤の読み切りになる。
    search = _Search(_with_mobility, deadline=time.monotonic() + _STRONG_TIME_LIMIT)
    empties = 64 - (player | opponent).bit_count()
    best = moves[0]
    for depth in range(1, empties + 1):
        try:
            best, _ = search.root(player, opponent, moves, depth)
        except _Timeout:
            break
        # 前の深さの最善手を先に調べると枝刈りが効きやすい。
        moves.remove(best)
        moves.insert(0, best)
    return best
