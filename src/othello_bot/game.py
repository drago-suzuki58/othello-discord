"""対局の状態と進行。Discord には依存しない。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum, auto

from othello_bot.ai import Level
from othello_bot.engine import Board, Color, iter_squares, replay


class GameError(Exception):
    """利用者に伝える操作エラー。メッセージはそのまま表示する。"""


class Mode(Enum):
    IMAGE = "image"
    TEXT = "text"


class Phase(Enum):
    WAITING = auto()  # 相手の承諾・参加待ち
    PLAYING = auto()
    FINISHED = auto()  # 対局が成立して終わった
    CLOSED = auto()  # 対局が成立しないまま閉じた


class EndReason(Enum):
    NORMAL = auto()
    RESIGN = auto()
    DRAW_AGREED = auto()
    ABORTED = auto()  # 長時間操作がなかった
    CANCELLED = auto()  # 申し込んだ側が取り消した
    DECLINED = auto()  # 申し込まれた側が断った


@dataclass(frozen=True)
class Seat:
    user_id: int | None = None
    cpu: Level | None = None

    @property
    def is_cpu(self) -> bool:
        return self.cpu is not None

    @property
    def label(self) -> str:
        if self.cpu is not None:
            return f"CPU（{self.cpu.label}）"
        return f"<@{self.user_id}>"


@dataclass(eq=False)
class Game:
    guild_id: int
    channel_id: int
    host: Seat
    host_color: Color
    mode: Mode
    invited_id: int | None = None
    guest: Seat | None = None
    message_id: int | None = None
    phase: Phase = Phase.WAITING
    board: Board = field(default_factory=Board)
    moves: list[int] = field(default_factory=list)
    selected_col: int | None = None
    draw_offer: Color | None = None
    passed: Color | None = None  # 直前の手のあとでパスになった側
    end_reason: EndReason | None = None
    resigned: Color | None = None
    updated_at: float = field(default_factory=time.time)  # 再起動をまたいで比べるので実時刻
    # 状態の変更からメッセージの更新までを直列にする。
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cpu_task: asyncio.Task | None = None

    @classmethod
    def against_cpu(cls, guild_id: int, channel_id: int, user_id: int, color: Color, level: Level, mode: Mode) -> Game:
        return cls(
            guild_id,
            channel_id,
            host=Seat(user_id),
            host_color=color,
            mode=mode,
            guest=Seat(cpu=level),
            phase=Phase.PLAYING,
        )

    # --- 参照 ---

    def seat(self, color: Color) -> Seat | None:
        return self.host if color is self.host_color else self.guest

    def color_of(self, user_id: int) -> Color | None:
        if self.host.user_id == user_id:
            return self.host_color
        if self.guest is not None and self.guest.user_id == user_id:
            return self.host_color.opponent
        return None

    def involves(self, user_id: int) -> bool:
        return self.color_of(user_id) is not None or (self.phase is Phase.WAITING and self.invited_id == user_id)

    @property
    def is_active(self) -> bool:
        return self.phase in (Phase.WAITING, Phase.PLAYING)

    @property
    def is_cpu_game(self) -> bool:
        return self.guest is not None and self.guest.is_cpu

    @property
    def cpu_to_move(self) -> Seat | None:
        seat = self.seat(self.board.turn)
        if self.phase is Phase.PLAYING and seat is not None and seat.is_cpu:
            return seat
        return None

    @property
    def last_move(self) -> int | None:
        return self.moves[-1] if self.moves else None

    @property
    def winner(self) -> Color | None:
        match self.end_reason:
            case EndReason.NORMAL:
                return self.board.winner
            case EndReason.RESIGN:
                return self.resigned.opponent if self.resigned else None
        return None

    def legal_columns(self) -> set[int]:
        return {square % 8 for square in iter_squares(self.board.legal_mask())}

    def legal_rows(self, col: int) -> set[int]:
        return {square // 8 for square in iter_squares(self.board.legal_mask()) if square % 8 == col}

    # --- 対局の成立 ---

    def accept(self, user_id: int) -> None:
        if self.phase is not Phase.WAITING:
            raise GameError("この対局は既に始まっているか、終わっています。")
        if user_id == self.host.user_id:
            raise GameError("自分の申し込みには参加できません。")
        if self.invited_id is not None and user_id != self.invited_id:
            raise GameError("この申し込みはほかの人宛てです。")
        self.guest = Seat(user_id)
        self.phase = Phase.PLAYING
        self._touch()

    def cancel(self, user_id: int) -> None:
        if self.phase is not Phase.WAITING:
            raise GameError("この対局は既に始まっているか、終わっています。")
        if user_id == self.host.user_id:
            self._close(EndReason.CANCELLED)
        elif self.invited_id is not None and user_id == self.invited_id:
            self._close(EndReason.DECLINED)
        else:
            raise GameError("申し込んだ人か、申し込まれた人だけが操作できます。")

    # --- 対局中の操作 ---

    def _require_player(self, user_id: int) -> Color:
        if self.phase is not Phase.PLAYING:
            raise GameError("この対局は進行中ではありません。")
        color = self.color_of(user_id)
        if color is None:
            raise GameError("この対局の対局者ではありません。")
        return color

    def _require_turn(self, user_id: int) -> None:
        if self._require_player(user_id) is not self.board.turn:
            raise GameError("相手の手番です。")

    def select_column(self, user_id: int, col: int) -> None:
        self._require_turn(user_id)
        if col not in self.legal_columns():
            raise GameError("その列には打てる場所がありません。")
        self.selected_col = col
        self._touch()

    def clear_selection(self, user_id: int) -> None:
        self._require_turn(user_id)
        self.selected_col = None
        self._touch()

    def play_row(self, user_id: int, row: int) -> None:
        self._require_turn(user_id)
        if self.selected_col is None:
            raise GameError("先に列を選んでください。")
        self.play(user_id, row * 8 + self.selected_col)

    def play(self, user_id: int, square: int) -> None:
        self._require_turn(user_id)
        if not self.board.is_legal(square):
            raise GameError("そこには打てません。")
        self._apply(square)

    def play_cpu(self, square: int) -> None:
        if self.cpu_to_move is None:
            raise RuntimeError("not CPU's turn")
        self._apply(square)

    def _apply(self, square: int) -> None:
        mover = self.board.turn
        self.board = self.board.play(square)
        self.moves.append(square)
        self.selected_col = None
        self.draw_offer = None
        if self.board.is_over:
            self.passed = None
            self._finish(EndReason.NORMAL)
        else:
            self.passed = mover.opponent if self.board.turn is mover else None
            self._touch()

    def offer_draw(self, user_id: int) -> None:
        color = self._require_player(user_id)
        if self.is_cpu_game:
            raise GameError("CPU 戦では引き分けを提案できません。")
        if self.draw_offer is not None:
            raise GameError("既に引き分けが提案されています。")
        self.draw_offer = color
        self._touch()

    def answer_draw(self, user_id: int, accept: bool) -> None:
        color = self._require_player(user_id)
        if self.draw_offer is None:
            raise GameError("引き分けは提案されていません。")
        if accept:
            if color is self.draw_offer:
                raise GameError("相手の承諾を待っています。")
            self._finish(EndReason.DRAW_AGREED)
        else:
            # 提案した側が押した場合は取り下げになる。
            self.draw_offer = None
            self._touch()

    def resign(self, user_id: int) -> None:
        self.resigned = self._require_player(user_id)
        self._finish(EndReason.RESIGN)

    def abort(self) -> None:
        if self.phase is Phase.PLAYING:
            self._finish(EndReason.ABORTED)
        elif self.phase is Phase.WAITING:
            self._close(EndReason.ABORTED)

    # --- 再戦 ---

    def rematch(self, user_id: int, *, swap: bool) -> Game:
        """終局した対局と同じ相手・表示で次の対局を作る。対人戦では相手の承諾待ちになる。"""
        if self.phase is not Phase.FINISHED:
            raise GameError("終局した対局でだけ再戦できます。")
        color = self.color_of(user_id)
        if color is None:
            raise GameError("この対局の対局者だけが再戦を申し込めます。")
        opponent = self.seat(color.opponent)
        new_color = color.opponent if swap else color
        if opponent.is_cpu:
            return Game.against_cpu(self.guild_id, self.channel_id, user_id, new_color, opponent.cpu, self.mode)
        return Game(
            self.guild_id,
            self.channel_id,
            host=Seat(user_id),
            host_color=new_color,
            mode=self.mode,
            invited_id=opponent.user_id,
        )

    def _finish(self, reason: EndReason) -> None:
        self.phase = Phase.FINISHED
        self.end_reason = reason
        self.selected_col = None
        self.draw_offer = None
        self._touch()

    def _close(self, reason: EndReason) -> None:
        self.phase = Phase.CLOSED
        self.end_reason = reason
        self._touch()

    def _touch(self) -> None:
        self.updated_at = time.time()

    # --- 保存 ---

    def to_dict(self) -> dict:
        """保存用の値。列の選択途中の状態は保存しない。"""
        return {
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "host": _seat_to_dict(self.host),
            "host_color": self.host_color.name,
            "mode": self.mode.name,
            "invited_id": self.invited_id,
            "guest": _seat_to_dict(self.guest) if self.guest else None,
            "phase": self.phase.name,
            "moves": self.moves,
            "draw_offer": _name(self.draw_offer),
            "passed": _name(self.passed),
            "end_reason": _name(self.end_reason),
            "resigned": _name(self.resigned),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Game:
        return cls(
            data["guild_id"],
            data["channel_id"],
            host=_seat_from_dict(data["host"]),
            host_color=Color[data["host_color"]],
            mode=Mode[data["mode"]],
            invited_id=data["invited_id"],
            guest=_seat_from_dict(data["guest"]) if data["guest"] else None,
            message_id=data["message_id"],
            phase=Phase[data["phase"]],
            board=replay(data["moves"])[-1],
            moves=list(data["moves"]),
            draw_offer=_member(Color, data["draw_offer"]),
            passed=_member(Color, data["passed"]),
            end_reason=_member(EndReason, data["end_reason"]),
            resigned=_member(Color, data["resigned"]),
            updated_at=data["updated_at"],
        )


def _name(member: Enum | None) -> str | None:
    return None if member is None else member.name


def _member[E: Enum](enum: type[E], name: str | None) -> E | None:
    return None if name is None else enum[name]


def _seat_to_dict(seat: Seat) -> dict:
    return {"user_id": seat.user_id, "cpu": _name(seat.cpu)}


def _seat_from_dict(data: dict) -> Seat:
    return Seat(data["user_id"], _member(Level, data["cpu"]))


class Registry:
    """進行中の対局。1 人が同じチャンネルで同時に持てる対局は 1 つまで。"""

    def __init__(self) -> None:
        self._games: list[Game] = []

    def add(self, game: Game) -> None:
        self._games.append(game)

    def remove(self, game: Game) -> None:
        if game in self._games:
            self._games.remove(game)

    def by_message(self, message_id: int) -> Game | None:
        return next((g for g in self._games if g.message_id == message_id), None)

    def active_for(self, channel_id: int, user_id: int) -> Game | None:
        return next(
            (g for g in self._games if g.channel_id == channel_id and g.is_active and g.involves(user_id)),
            None,
        )

    def idle_since(self, threshold: float) -> list[Game]:
        return [g for g in self._games if g.is_active and g.updated_at < threshold]
