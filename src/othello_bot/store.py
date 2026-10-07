"""対局の保存先（SQLite）。1 局を 1 行とし、対局メッセージの ID で引く。"""

from __future__ import annotations

import json
import sqlite3

from othello_bot.game import Game


class Store:
    def __init__(self, path: str) -> None:
        self._db = sqlite3.connect(path, autocommit=True)
        # 着手のたびに書くので、書き込みのたびの同期を減らす。電源断で直近の数手を失うことはあるが、壊れはしない。
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = NORMAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS games"
            " (message_id INTEGER PRIMARY KEY, active INTEGER NOT NULL, data TEXT NOT NULL)"
        )

    def save(self, game: Game) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO games (message_id, active, data) VALUES (?, ?, ?)",
            (game.message_id, game.is_active, json.dumps(game.to_dict())),
        )

    def get(self, message_id: int) -> Game | None:
        row = self._db.execute("SELECT data FROM games WHERE message_id = ?", (message_id,)).fetchone()
        return Game.from_dict(json.loads(row[0])) if row else None

    def delete(self, message_id: int) -> None:
        self._db.execute("DELETE FROM games WHERE message_id = ?", (message_id,))

    def active_games(self) -> list[Game]:
        rows = self._db.execute("SELECT data FROM games WHERE active").fetchall()
        return [Game.from_dict(json.loads(data)) for (data,) in rows]

    def close(self) -> None:
        self._db.close()
