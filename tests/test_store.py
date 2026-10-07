import json

import pytest

from othello_bot.ai import Level
from othello_bot.engine import Color, parse_square
from othello_bot.game import Game, Mode, Seat
from othello_bot.store import Store

HOST, GUEST = 1, 2


def waiting() -> Game:
    return Game(10, 20, host=Seat(HOST), host_color=Color.BLACK, mode=Mode.TEXT, invited_id=GUEST, message_id=100)


def playing() -> Game:
    game = waiting()
    game.accept(GUEST)
    for i, name in enumerate(["f5", "d6", "c3"]):
        game.play(HOST if i % 2 == 0 else GUEST, parse_square(name))
    game.offer_draw(GUEST)
    game.select_column(GUEST, 3)
    return game


def cpu_turn() -> Game:
    game = Game.against_cpu(10, 20, HOST, Color.WHITE, Level.STRONG, Mode.IMAGE)
    game.message_id = 101
    return game


def resigned() -> Game:
    game = playing()
    game.resign(HOST)
    return game


@pytest.mark.parametrize("make", [waiting, playing, cpu_turn, resigned])
def test_game_round_trip(make) -> None:
    game = make()
    restored = Game.from_dict(json.loads(json.dumps(game.to_dict())))
    assert restored.to_dict() == game.to_dict()
    assert restored.board == game.board
    assert restored.selected_col is None
    assert restored.winner == game.winner
    assert (restored.cpu_to_move is None) == (game.cpu_to_move is None)


def test_store_loads_only_active_games(tmp_path) -> None:
    path = str(tmp_path / "games.db")
    store = Store(path)
    game, cpu = playing(), cpu_turn()
    store.save(game)
    store.save(cpu)
    game.play(GUEST, game.board.legal_moves()[0])
    store.save(game)
    store.close()

    store = Store(path)
    loaded = {g.message_id: g for g in store.active_games()}
    assert loaded.keys() == {game.message_id, cpu.message_id}
    assert loaded[game.message_id].moves == game.moves

    game.resign(GUEST)
    store.save(game)
    assert [g.message_id for g in store.active_games()] == [cpu.message_id]
    store.delete(cpu.message_id)
    assert store.active_games() == []
    store.close()
