import pytest
from discord import ui
from PIL import Image

from othello_bot.engine import Color, parse_square, square_name
from othello_bot.game import EndReason, Game, Mode, Phase, Seat
from othello_bot.render import TILE_KINDS, Images, render_images
from othello_bot.text_board import Tiles, board_text
from othello_bot.views import BOARD_FILE, REPLAY_FILE, build, image_request

HOST, GUEST = 1, 2


def render(game: Game, tiles: Tiles) -> tuple[ui.LayoutView, list]:
    request = image_request(game)
    return build(game, tiles, render_images(request) if request else Images())


@pytest.fixture
def tiles() -> Tiles:
    return Tiles({kind: f"<:oth1_{kind}:123456789012345678>" for kind in TILE_KINDS})


def started(mode: Mode) -> Game:
    game = Game(10, 20, host=Seat(HOST), host_color=Color.BLACK, mode=mode)
    game.accept(GUEST)
    return game


@pytest.mark.parametrize("mode", list(Mode))
@pytest.mark.parametrize("reason", [EndReason.NORMAL, EndReason.RESIGN, EndReason.DRAW_AGREED, EndReason.ABORTED])
def test_finished_game_shows_replay(mode: Mode, reason: EndReason, tiles: Tiles) -> None:
    game = started(mode)
    if reason is EndReason.NORMAL:
        for i, name in enumerate(["e6", "f4", "e3", "f6", "g5", "d6", "e7", "f5", "c5"]):
            game.play(HOST if i % 2 == 0 else GUEST, parse_square(name))
    else:
        game.play(HOST, parse_square("f5"))
        match reason:
            case EndReason.RESIGN:
                game.resign(GUEST)
            case EndReason.DRAW_AGREED:
                game.offer_draw(HOST)
                game.answer_draw(GUEST, accept=True)
            case EndReason.ABORTED:
                game.abort()

    view, files = render(game, tiles)
    try:
        assert game.end_reason is reason
        expected_files = [REPLAY_FILE] if mode is Mode.TEXT else [BOARD_FILE, REPLAY_FILE]
        assert [file.filename for file in files] == expected_files
        items = view.children[0].children
        if mode is Mode.TEXT:
            assert isinstance(items[2], ui.TextDisplay)
            assert items[2].content == board_text(game.board, tiles, last_move=game.last_move, show_legal=False)
        else:
            assert isinstance(items[2], ui.MediaGallery)
        assert isinstance(items[3], ui.TextDisplay)
        assert items[3].content == "### リプレイ"
        assert isinstance(items[4], ui.MediaGallery)
        assert [item.media.url for item in items[4].items] == [f"attachment://{REPLAY_FILE}"]
        galleries = [item for item in view.walk_children() if isinstance(item, ui.MediaGallery)]
        assert [[item.media.url for item in gallery.items] for gallery in galleries] == [
            [f"attachment://{name}"] for name in expected_files
        ]
        gif = Image.open(files[-1].fp)
        assert gif.format == "GIF"
        assert gif.n_frames == len(game.moves) + 1
        assert len(list(view.walk_children())) <= 40
        assert sum(len(item.content) for item in view.walk_children() if isinstance(item, ui.TextDisplay)) <= 4000
    finally:
        for file in files:
            file.close()


@pytest.mark.parametrize("mode", list(Mode))
def test_kifu_shown_while_playing(mode: Mode, tiles: Tiles) -> None:
    # 終局の 1 手前、棋譜が最も長くなる状態で上限に収まることも確かめる。
    full = started(mode)
    while full.phase is Phase.PLAYING:
        full.play(HOST if full.board.turn is Color.BLACK else GUEST, full.board.legal_moves()[0])
    game = started(mode)
    for move in full.moves[:-1]:
        game.play(HOST if game.board.turn is Color.BLACK else GUEST, move)
    assert game.phase is Phase.PLAYING
    view, files = render(game, tiles)
    for file in files:
        file.close()
    kifu = "".join(square_name(m) for m in game.moves)
    texts = [item.content for item in view.walk_children() if isinstance(item, ui.TextDisplay)]
    assert f"-# 棋譜（{len(game.moves)} 手）: `{kifu}`" in texts
    assert len(list(view.walk_children())) <= 40
    assert sum(len(text) for text in texts) <= 4000


def test_no_images_requested_when_none_shown() -> None:
    assert image_request(Game(10, 20, host=Seat(HOST), host_color=Color.BLACK, mode=Mode.IMAGE)) is None
    game = started(Mode.TEXT)
    game.play(HOST, parse_square("f5"))
    assert image_request(game) is None


@pytest.mark.parametrize("mode", list(Mode))
@pytest.mark.parametrize("finished", [False, True])
def test_no_replay_while_playing_or_without_moves(mode: Mode, finished: bool, tiles: Tiles) -> None:
    game = started(mode)
    if finished:
        game.resign(GUEST)
    else:
        game.play(HOST, parse_square("f5"))

    view, files = render(game, tiles)
    try:
        expected_files = [] if mode is Mode.TEXT else [BOARD_FILE]
        assert [file.filename for file in files] == expected_files
        assert not any(
            isinstance(item, ui.TextDisplay) and item.content == "### リプレイ" for item in view.walk_children()
        )
        galleries = [item for item in view.walk_children() if isinstance(item, ui.MediaGallery)]
        if mode is Mode.TEXT:
            assert not galleries
            assert isinstance(view.children[0].children[2], ui.TextDisplay)
        else:
            assert len(galleries) == 1
            assert [item.media.url for item in galleries[0].items] == [f"attachment://{BOARD_FILE}"]
    finally:
        for file in files:
            file.close()
