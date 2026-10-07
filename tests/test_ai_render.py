import io
import pickle
import random

import pytest
from PIL import Image

from othello_bot.ai import Level, choose_move
from othello_bot.engine import Board, Color, parse_square, replay
from othello_bot.render import (
    TILE_KINDS,
    TILE_SIZE,
    ImageRequest,
    board_png,
    render_images,
    render_tile,
    replay_gif,
)


@pytest.mark.parametrize("level", list(Level))
def test_cpu_returns_legal_move(level: Level) -> None:
    board = Board()
    rng = random.Random(0)
    for _ in range(6):
        move = choose_move(board, level, rng)
        assert board.is_legal(move)
        board = board.play(move)


def exact_score(board: Board, color: Color) -> int:
    """終局まで全探索したときの ``color`` から見た石数差。"""
    if board.is_over:
        return board.count(color) - board.count(color.opponent)
    scores = [exact_score(board.play(m), color) for m in board.legal_moves()]
    return max(scores) if board.turn is color else min(scores)


def test_strong_cpu_solves_endgame() -> None:
    rng = random.Random(3)
    board = Board()
    while 64 - board.count(Color.BLACK) - board.count(Color.WHITE) > 8 and not board.is_over:
        board = board.play(rng.choice(board.legal_moves()))
    assert not board.is_over
    color = board.turn
    best = max(exact_score(board.play(m), color) for m in board.legal_moves())
    assert exact_score(board.play(choose_move(board, Level.STRONG)), color) == best


def test_cpu_plays_full_game() -> None:
    board = Board()
    levels = {Color.BLACK: Level.NORMAL, Color.WHITE: Level.WEAK}
    while not board.is_over:
        board = board.play(choose_move(board, levels[board.turn], random.Random(1)))
    assert board.count(Color.BLACK) + board.count(Color.WHITE) <= 64


def test_board_png_and_gif() -> None:
    moves = [parse_square(s) for s in ["f5", "d6", "c3", "d3", "c4"]]
    board = replay(moves)[-1]
    image = Image.open(io.BytesIO(board_png(board, last_move=moves[-1], selected_col=2)))
    assert image.format == "PNG"
    cell = 40
    gif = Image.open(io.BytesIO(replay_gif(moves, cell=cell)))
    assert gif.format == "GIF"
    assert gif.n_frames == len(moves) + 1
    # 減色で直前の手の印の赤が消えていないこと。盤の左上は枠から半マス分ずれている。
    gif.seek(gif.n_frames - 1)
    row, col = divmod(moves[-1], 8)
    red, green, _ = gif.convert("RGB").getpixel((cell + col * cell, cell + row * cell))
    assert red > 200 and green < 100


def test_work_for_worker_processes_is_picklable() -> None:
    moves = [parse_square(s) for s in ["f5", "d6", "c3"]]
    board = replay(moves)[-1]
    assert pickle.loads(pickle.dumps((board, Level.STRONG))) == (board, Level.STRONG)
    request = ImageRequest(board, last_move=moves[-1], replay=tuple(moves))
    assert pickle.loads(pickle.dumps(request)) == request
    images = render_images(request)
    assert pickle.loads(pickle.dumps(images)) == images
    assert Image.open(io.BytesIO(images.board)).format == "PNG"
    assert Image.open(io.BytesIO(images.replay)).n_frames == len(moves) + 1


def test_tiles_are_square_and_small() -> None:
    for kind in TILE_KINDS:
        data = render_tile(kind)
        assert len(data) < 256 * 1024  # アプリ絵文字の上限
        assert Image.open(io.BytesIO(data)).size == (TILE_SIZE, TILE_SIZE)
