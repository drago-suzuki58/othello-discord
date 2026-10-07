import pytest

from othello_bot.ai import Level
from othello_bot.engine import Color, parse_square
from othello_bot.game import EndReason, Game, GameError, Mode, Phase, Registry, Seat

HOST, GUEST, OTHER = 1, 2, 3


def make_game(invited: int | None = None) -> Game:
    return Game(10, 20, host=Seat(HOST), host_color=Color.BLACK, mode=Mode.IMAGE, invited_id=invited)


def started() -> Game:
    game = make_game()
    game.accept(GUEST)
    return game


def test_accept_open_recruitment() -> None:
    game = make_game()
    with pytest.raises(GameError):
        game.accept(HOST)
    game.accept(OTHER)
    assert game.phase is Phase.PLAYING
    assert game.color_of(HOST) is Color.BLACK
    assert game.color_of(OTHER) is Color.WHITE


def test_invitation_only_for_invited_user() -> None:
    game = make_game(invited=GUEST)
    with pytest.raises(GameError):
        game.accept(OTHER)
    game.cancel(GUEST)
    assert game.phase is Phase.CLOSED
    assert game.end_reason is EndReason.DECLINED


def test_two_step_move() -> None:
    game = started()
    with pytest.raises(GameError, match="相手の手番"):
        game.select_column(GUEST, 3)
    with pytest.raises(GameError, match="打てる場所がありません"):
        game.select_column(HOST, 0)
    game.select_column(HOST, 5)  # f 列
    assert game.legal_rows(5) == {4}
    game.play_row(HOST, 4)  # f5
    assert game.moves == [parse_square("f5")]
    assert game.selected_col is None
    assert game.board.turn is Color.WHITE


def test_illegal_square_is_rejected() -> None:
    game = started()
    with pytest.raises(GameError):
        game.play(HOST, parse_square("a1"))


def test_draw_offer_flow() -> None:
    game = started()
    game.offer_draw(HOST)
    with pytest.raises(GameError):
        game.answer_draw(HOST, accept=True)
    game.answer_draw(GUEST, accept=True)
    assert game.phase is Phase.FINISHED
    assert game.end_reason is EndReason.DRAW_AGREED
    assert game.winner is None


def test_draw_offer_cleared_by_move() -> None:
    game = started()
    game.offer_draw(GUEST)
    game.play(HOST, parse_square("f5"))
    assert game.draw_offer is None


def test_resign() -> None:
    game = started()
    game.resign(GUEST)
    assert game.winner is Color.BLACK
    with pytest.raises(GameError):
        game.play(HOST, parse_square("f5"))


def test_cpu_game_disallows_draw_and_tracks_cpu_turn() -> None:
    game = Game.against_cpu(10, 20, HOST, Color.WHITE, Level.WEAK, Mode.TEXT)
    assert game.cpu_to_move is not None
    with pytest.raises(GameError):
        game.offer_draw(HOST)
    game.play_cpu(parse_square("f5"))
    assert game.cpu_to_move is None


def test_normal_finish() -> None:
    game = started()
    for i, name in enumerate(["e6", "f4", "e3", "f6", "g5", "d6", "e7", "f5", "c5"]):
        game.play(HOST if i % 2 == 0 else GUEST, parse_square(name))
    assert game.phase is Phase.FINISHED
    assert game.winner is Color.BLACK


def test_registry_limits_by_channel_and_user() -> None:
    registry = Registry()
    game = make_game(invited=GUEST)
    game.message_id = 99
    registry.add(game)
    assert registry.active_for(20, HOST) is game
    assert registry.active_for(20, GUEST) is game
    assert registry.active_for(20, OTHER) is None
    assert registry.active_for(21, HOST) is None
    assert registry.by_message(99) is game
    game.cancel(HOST)
    assert registry.active_for(20, HOST) is None


@pytest.mark.parametrize("swap", [True, False])
def test_rematch_between_players(swap: bool) -> None:
    game = started()
    with pytest.raises(GameError, match="終局"):
        game.rematch(GUEST, swap=swap)
    game.resign(HOST)
    with pytest.raises(GameError, match="対局者"):
        game.rematch(OTHER, swap=swap)

    # 白だった GUEST が申し込み、HOST の承諾を待つ。
    rematch = game.rematch(GUEST, swap=swap)
    assert rematch.phase is Phase.WAITING
    assert rematch.invited_id == HOST
    assert (rematch.channel_id, rematch.mode) == (game.channel_id, game.mode)
    rematch.accept(HOST)
    expected = Color.BLACK if swap else Color.WHITE
    assert rematch.color_of(GUEST) is expected
    assert rematch.color_of(HOST) is expected.opponent
    assert rematch.moves == []


@pytest.mark.parametrize("swap", [True, False])
def test_rematch_against_cpu_starts_immediately(swap: bool) -> None:
    game = Game.against_cpu(10, 20, HOST, Color.WHITE, Level.STRONG, Mode.TEXT)
    game.abort()
    rematch = game.rematch(HOST, swap=swap)
    assert rematch.phase is Phase.PLAYING
    assert rematch.color_of(HOST) is (Color.BLACK if swap else Color.WHITE)
    assert rematch.guest.cpu is Level.STRONG
    assert rematch.mode is Mode.TEXT
