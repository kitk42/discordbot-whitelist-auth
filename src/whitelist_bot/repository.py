"""SQLite の読み書き。

時刻はすべて UTC の UNIX 秒（整数）で保存する。
Minecraft ID の比較は大文字と小文字を区別しない（lower() で比較する）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import aiosqlite


class Status(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REMOVED = "removed"


ACTIVE_STATUSES = (Status.PENDING, Status.APPROVED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id  INTEGER NOT NULL,
    minecraft_id     TEXT    NOT NULL,
    status           TEXT    NOT NULL
                     CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'removed')),
    created_at       INTEGER NOT NULL,
    admin_message_id INTEGER,
    processed_by     INTEGER,
    processed_at     INTEGER,
    reason           TEXT
);
CREATE INDEX IF NOT EXISTS idx_applications_user ON applications (discord_user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications (status, created_at);

-- 処理の履歴（FR-6）。Bot を経由しない ID の削除も記録するため application_id は NULL を許す
CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER,
    minecraft_id   TEXT    NOT NULL,
    action         TEXT    NOT NULL,
    actor_id       INTEGER,
    reason         TEXT,
    created_at     INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class Application:
    id: int
    discord_user_id: int
    minecraft_id: str
    status: Status
    created_at: int
    admin_message_id: int | None
    processed_by: int | None
    processed_at: int | None
    reason: str | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Application:
        return cls(
            id=row["id"],
            discord_user_id=row["discord_user_id"],
            minecraft_id=row["minecraft_id"],
            status=Status(row["status"]),
            created_at=row["created_at"],
            admin_message_id=row["admin_message_id"],
            processed_by=row["processed_by"],
            processed_at=row["processed_at"],
            reason=row["reason"],
        )


class Repository:
    def __init__(self, path: Path | str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if str(self._path) != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Repository.open() が呼ばれていません")
        return self._db

    # --- applications ---

    async def insert_application(self, discord_user_id: int, minecraft_id: str, now: int) -> int:
        cursor = await self.db.execute(
            "INSERT INTO applications (discord_user_id, minecraft_id, status, created_at)"
            " VALUES (?, ?, ?, ?)",
            (discord_user_id, minecraft_id, Status.PENDING, now),
        )
        await self.db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def delete_application(self, application_id: int) -> None:
        await self.db.execute("DELETE FROM events WHERE application_id = ?", (application_id,))
        await self.db.execute("DELETE FROM applications WHERE id = ?", (application_id,))
        await self.db.commit()

    async def get(self, application_id: int) -> Application | None:
        async with self.db.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return Application.from_row(row) if row else None

    async def set_admin_message(self, application_id: int, message_id: int) -> None:
        await self.db.execute(
            "UPDATE applications SET admin_message_id = ? WHERE id = ?",
            (message_id, application_id),
        )
        await self.db.commit()

    async def find_active_by_user(self, discord_user_id: int) -> Application | None:
        return await self._find_one(
            "SELECT * FROM applications WHERE discord_user_id = ? AND status IN (?, ?)"
            " ORDER BY id DESC LIMIT 1",
            (discord_user_id, *ACTIVE_STATUSES),
        )

    async def find_by_minecraft_id(
        self, minecraft_id: str, statuses: tuple[Status, ...]
    ) -> Application | None:
        placeholders = ", ".join("?" for _ in statuses)
        return await self._find_one(
            "SELECT * FROM applications WHERE lower(minecraft_id) = lower(?)"
            f" AND status IN ({placeholders}) ORDER BY id DESC LIMIT 1",
            (minecraft_id, *statuses),
        )

    async def created_at_since(self, discord_user_id: int, since: int) -> list[int]:
        """since より後に作られた申請の作成時刻（古い順）。"""
        async with self.db.execute(
            "SELECT created_at FROM applications WHERE discord_user_id = ? AND created_at > ?"
            " ORDER BY created_at",
            (discord_user_id, since),
        ) as cursor:
            return [row["created_at"] for row in await cursor.fetchall()]

    async def list_pending_created_before(self, before: int) -> list[Application]:
        async with self.db.execute(
            "SELECT * FROM applications WHERE status = ? AND created_at <= ? ORDER BY id",
            (Status.PENDING, before),
        ) as cursor:
            return [Application.from_row(row) for row in await cursor.fetchall()]

    async def transition(
        self,
        application_id: int,
        from_status: Status,
        to_status: Status,
        *,
        actor_id: int | None,
        now: int,
        reason: str | None = None,
        minecraft_id: str | None = None,
    ) -> bool:
        """状態を from_status → to_status に変える。既に別の状態になっていれば False。"""
        cursor = await self.db.execute(
            "UPDATE applications SET status = ?, processed_by = ?, processed_at = ?, reason = ?,"
            " minecraft_id = coalesce(?, minecraft_id)"
            " WHERE id = ? AND status = ?",
            (to_status, actor_id, now, reason, minecraft_id, application_id, from_status),
        )
        await self.db.commit()
        return cursor.rowcount == 1

    # --- events ---

    async def add_event(
        self,
        *,
        application_id: int | None,
        minecraft_id: str,
        action: str,
        actor_id: int | None,
        now: int,
        reason: str | None = None,
    ) -> None:
        await self.db.execute(
            "INSERT INTO events"
            " (application_id, minecraft_id, action, actor_id, reason, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (application_id, minecraft_id, action, actor_id, reason, now),
        )
        await self.db.commit()

    async def list_events(self) -> list[aiosqlite.Row]:
        async with self.db.execute("SELECT * FROM events ORDER BY id") as cursor:
            return list(await cursor.fetchall())

    async def _find_one(self, sql: str, params: tuple[object, ...]) -> Application | None:
        async with self.db.execute(sql, params) as cursor:
            row = await cursor.fetchone()
        return Application.from_row(row) if row else None
