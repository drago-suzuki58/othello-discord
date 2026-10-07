"""盤面画像・リプレイ GIF・文字モード用の絵文字タイルを描く。"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from othello_bot.engine import COLUMNS, Board, Color, replay

FRAME = (24, 52, 36)
FRAME_HIGHLIGHT = (196, 160, 62)
LABEL = (226, 236, 228)
LABEL_SELECTED = (24, 40, 30)
BOARD = (46, 139, 87)
BOARD_SELECTED = (72, 168, 112)
GRID = (24, 84, 50)
BLACK_STONE = (28, 28, 30)
BLACK_EDGE = (6, 6, 8)
WHITE_STONE = (244, 244, 240)
WHITE_EDGE = (150, 150, 146)
LAST_MOVE = (230, 64, 64)

_SCALE = 2  # 拡大して描いてから縮小し、輪郭を滑らかにする


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _stone(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float], color: Color) -> None:
    size = box[2] - box[0]
    fill, edge = (BLACK_STONE, BLACK_EDGE) if color is Color.BLACK else (WHITE_STONE, WHITE_EDGE)
    draw.ellipse(box, fill=fill, outline=edge, width=max(1, round(size * 0.04)))


def _dot(draw: ImageDraw.ImageDraw, cx: float, cy: float, radius: float, fill) -> None:
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fill)


def _legal_fill(color: Color) -> tuple[int, int, int]:
    # 盤の緑に手番の石の色を混ぜた控えめな色。
    base = BLACK_STONE if color is Color.BLACK else WHITE_STONE
    return tuple(round(b * 0.55 + g * 0.45) for b, g in zip(base, BOARD, strict=True))


def render_board(
    board: Board,
    *,
    cell: int = 64,
    last_move: int | None = None,
    show_legal: bool = True,
    selected_col: int | None = None,
) -> Image.Image:
    c = cell * _SCALE
    margin = round(c * 0.5)
    pad = round(c * 0.2)
    size = margin + c * 8 + pad
    image = Image.new("RGB", (size, size), FRAME)
    draw = ImageDraw.Draw(image)
    font = _font(round(c * 0.36))

    legal = board.legal_mask() if show_legal else 0

    for col in range(8):
        selected = col == selected_col
        x0 = margin + col * c
        if selected:
            draw.rounded_rectangle(
                (x0 + c * 0.12, margin * 0.12, x0 + c * 0.88, margin * 0.88),
                radius=c * 0.12,
                fill=FRAME_HIGHLIGHT,
            )
        draw.text(
            (x0 + c / 2, margin / 2),
            COLUMNS[col],
            font=font,
            fill=LABEL_SELECTED if selected else LABEL,
            anchor="mm",
        )
    for row in range(8):
        draw.text((margin / 2, margin + row * c + c / 2), str(row + 1), font=font, fill=LABEL, anchor="mm")

    draw.rectangle((margin, margin, margin + c * 8, margin + c * 8), fill=BOARD)
    if selected_col is not None:
        x0 = margin + selected_col * c
        draw.rectangle((x0, margin, x0 + c, margin + c * 8), fill=BOARD_SELECTED)

    line = max(2, round(c * 0.03))
    for i in range(9):
        offset = margin + i * c
        draw.line((offset, margin, offset, margin + c * 8), fill=GRID, width=line)
        draw.line((margin, offset, margin + c * 8, offset), fill=GRID, width=line)
    for gx, gy in ((2, 2), (6, 2), (2, 6), (6, 6)):
        _dot(draw, margin + gx * c, margin + gy * c, c * 0.07, GRID)

    for square in range(64):
        row, col = divmod(square, 8)
        x0, y0 = margin + col * c, margin + row * c
        cx, cy = x0 + c / 2, y0 + c / 2
        stone = board.at(square)
        if stone is not None:
            inset = c * 0.1
            _stone(draw, (x0 + inset, y0 + inset, x0 + c - inset, y0 + c - inset), stone)
            if square == last_move:
                _dot(draw, cx, cy, c * 0.1, LAST_MOVE)
        elif legal >> square & 1:
            _dot(draw, cx, cy, c * 0.12, _legal_fill(board.turn))

    return image.resize((size // _SCALE, size // _SCALE), Image.Resampling.LANCZOS)


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def board_png(board: Board, **options) -> bytes:
    return _png(render_board(board, **options))


def replay_gif(moves: list[int], *, cell: int = 40, frame_ms: int = 600, last_frame_ms: int = 3000) -> bytes:
    """棋譜を初期局面から 1 手ずつたどる GIF を作る。"""
    boards = replay(moves)
    frames = [
        render_board(board, cell=cell, last_move=moves[i - 1] if i else None, show_legal=False).convert(
            "P", palette=Image.Palette.ADAPTIVE, colors=64
        )
        for i, board in enumerate(boards)
    ]
    durations = [frame_ms] * (len(frames) - 1) + [last_frame_ms]
    buffer = io.BytesIO()
    frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )
    return buffer.getvalue()


# --- 文字モード用タイル ---------------------------------------------------------
#
# 文字モードでは 1 マスを 1 つの絵文字で表す。マス・ラベルとも背景を塗りつぶした
# 正方形にして、Discord のテーマや端末によらず同じ見た目になるようにする。

TILE_SIZE = 128

BOARD_TILES = ("empty", "black", "white", "legal_black", "legal_white", "last_black", "last_white")
LABEL_TILES = (
    ("corner",)
    + tuple(f"col_{c}" for c in COLUMNS)
    + tuple(f"col_{c}_sel" for c in COLUMNS)
    + tuple(f"row_{r}" for r in range(1, 9))
)
TILE_KINDS = BOARD_TILES + LABEL_TILES


def render_tile(kind: str) -> bytes:
    if kind not in TILE_KINDS:
        raise ValueError(f"unknown tile: {kind}")
    s = TILE_SIZE * _SCALE
    image = Image.new("RGB", (s, s), FRAME)
    draw = ImageDraw.Draw(image)

    if kind in BOARD_TILES:
        draw.rectangle((0, 0, s, s), fill=BOARD)
        # 隣り合うタイルの縁が合わさって格子線になる。
        draw.rectangle((0, 0, s - 1, s - 1), outline=GRID, width=round(s * 0.03))
        inset = s * 0.1
        box = (inset, inset, s - inset, s - inset)
        match kind:
            case "black" | "last_black":
                _stone(draw, box, Color.BLACK)
            case "white" | "last_white":
                _stone(draw, box, Color.WHITE)
            case "legal_black":
                _dot(draw, s / 2, s / 2, s * 0.13, _legal_fill(Color.BLACK))
            case "legal_white":
                _dot(draw, s / 2, s / 2, s * 0.13, _legal_fill(Color.WHITE))
        if kind.startswith("last_"):
            _dot(draw, s / 2, s / 2, s * 0.11, LAST_MOVE)
    elif kind != "corner":  # 角は枠の色だけ
        text = kind.split("_")[1]
        fill = LABEL
        if kind.endswith("_sel"):
            draw.rounded_rectangle((s * 0.12, s * 0.12, s * 0.88, s * 0.88), radius=s * 0.14, fill=FRAME_HIGHLIGHT)
            fill = LABEL_SELECTED
        draw.text((s / 2, s / 2), text, font=_font(round(s * 0.55)), fill=fill, anchor="mm")

    return _png(image.resize((TILE_SIZE, TILE_SIZE), Image.Resampling.LANCZOS))
