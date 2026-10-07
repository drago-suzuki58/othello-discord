import pytest

from othello_bot.engine import Board, Color, parse_square, replay, square_name


def perft(board: Board, depth: int) -> int:
    if depth == 0:
        return 1
    return sum(perft(board.play(move), depth - 1) for move in board.legal_moves())


@pytest.mark.parametrize(("depth", "expected"), [(1, 4), (2, 12), (3, 56), (4, 244), (5, 1396), (6, 8200)])
def test_perft(depth: int, expected: int) -> None:
    assert perft(Board(), depth) == expected


def test_initial_position() -> None:
    board = Board()
    assert board.at(parse_square("d4")) is Color.WHITE
    assert board.at(parse_square("e4")) is Color.BLACK
    assert board.at(parse_square("d5")) is Color.BLACK
    assert board.at(parse_square("e5")) is Color.WHITE
    assert sorted(square_name(m) for m in board.legal_moves()) == ["c4", "d3", "e6", "f5"]


def test_play_flips_and_switches_turn() -> None:
    board = Board().play(parse_square("f5"))
    assert board.turn is Color.WHITE
    assert board.at(parse_square("e5")) is Color.BLACK
    assert board.count(Color.BLACK) == 4
    assert board.count(Color.WHITE) == 1


def test_illegal_move_raises() -> None:
    with pytest.raises(ValueError):
        Board().play(parse_square("a1"))


def test_parse_square() -> None:
    assert parse_square("a1") == 0
    assert parse_square("H8") == 63
    assert parse_square("ｄ３") == parse_square("d3") == 19
    assert parse_square("i1") is None
    assert parse_square("a9") is None
    assert parse_square("") is None


def test_shortest_game_ends_with_wipeout() -> None:
    # 9 手で白が全滅する最短の終局。
    moves = [parse_square(s) for s in ["e6", "f4", "e3", "f6", "g5", "d6", "e7", "f5", "c5"]]
    final = replay(moves)[-1]
    assert final.is_over
    assert final.count(Color.WHITE) == 0
    assert final.winner is Color.BLACK


def test_game_over_when_nobody_can_move() -> None:
    # 黒 a1・白 b1 で黒が c1 に打つと白石がなくなり、両者とも打てなくなる。
    board = Board(black=1 << 0, white=1 << 1, turn=Color.BLACK)
    assert board.play(2).is_over


def test_auto_pass_keeps_turn() -> None:
    # 黒 a1・白 b1, b2 で黒が c1 に打つと白は b2 だけになる。
    # 白は打てず、黒は b3・c3 に打てるので、白がパスして黒の手番が続く。
    board = Board(black=1 << 0, white=(1 << 1) | (1 << 9), turn=Color.BLACK)
    after = board.play(2)
    assert not after.is_over
    assert after.turn is Color.BLACK
