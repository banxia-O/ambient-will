from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from ambientwill.models import Decision, OutboxEvent, Urge, WakeEvent


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS urges (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    reason TEXT NOT NULL,
    urgency REAL NOT NULL,
    confidence REAL NOT NULL,
    interruption_cost REAL NOT NULL,
    cooldown_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wake_events (
    id TEXT PRIMARY KEY,
    trigger TEXT NOT NULL,
    evaluated_at TEXT NOT NULL,
    selected_urge_id TEXT,
    decision TEXT NOT NULL,
    reasons TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    event_id TEXT PRIMARY KEY,
    wake_event_id TEXT NOT NULL REFERENCES wake_events(id),
    message_preview TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    planned_at TEXT NOT NULL,
    delayed_until TEXT NOT NULL,
    state TEXT NOT NULL,
    cooldown_key TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_urges_status_created
ON urges(status, created_at);

CREATE INDEX IF NOT EXISTS idx_outbox_planned
ON outbox(planned_at);
"""


class AlreadyRunningError(RuntimeError):
    pass


class TickLock:
    def __init__(self, path: Path):
        self.path = path
        self._handle = None

    def __enter__(self) -> "TickLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            self._handle = None
            raise AlreadyRunningError("another tick holds the project lock") from exc
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


class Storage:
    def __init__(
        self,
        path: str | Path,
        *,
        read_only: bool = False,
        memory_connection: sqlite3.Connection | None = None,
    ):
        self.path = Path(path)
        self.read_only = read_only
        self._memory_connection = memory_connection

    @property
    def lock_path(self) -> Path:
        return self.path.parent / "tick.lock"

    @classmethod
    def snapshot(cls, path: str | Path) -> "Storage":
        db_path = Path(path)
        if db_path.exists():
            return cls(db_path, read_only=True)
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(SCHEMA)
        return cls(db_path, read_only=True, memory_connection=connection)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if self._memory_connection is not None:
            yield self._memory_connection
            return

        if self.read_only:
            uri = f"file:{self.path.resolve().as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=1.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        if self.read_only:
            raise sqlite3.OperationalError("cannot initialize read-only storage")
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            connection.commit()

    def add_urge(self, urge: Urge) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO urges (
                    id, type, reason, urgency, confidence, interruption_cost,
                    cooldown_key, created_at, expires_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    urge.id,
                    urge.type,
                    urge.reason,
                    urge.urgency,
                    urge.confidence,
                    urge.interruption_cost,
                    urge.cooldown_key,
                    urge.created_at.isoformat(),
                    urge.expires_at.isoformat() if urge.expires_at else None,
                    urge.status,
                ),
            )
            connection.commit()

    def set_urge_status(self, urge_id: str, status: str) -> None:
        if status not in {"open", "closed", "expired"}:
            raise ValueError(f"unsupported urge status: {status}")
        with self.connect() as connection:
            connection.execute(
                "UPDATE urges SET status = ? WHERE id = ?", (status, urge_id)
            )
            connection.commit()

    @staticmethod
    def _urge_from_row(row: sqlite3.Row) -> Urge:
        return Urge(
            id=row["id"],
            type=row["type"],
            reason=row["reason"],
            urgency=float(row["urgency"]),
            confidence=float(row["confidence"]),
            interruption_cost=float(row["interruption_cost"]),
            cooldown_key=row["cooldown_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=(
                datetime.fromisoformat(row["expires_at"])
                if row["expires_at"]
                else None
            ),
            status=row["status"],
        )

    def valid_urges(self, at: datetime) -> list[Urge]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM urges
                WHERE status = 'open'
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()
        urges = [self._urge_from_row(row) for row in rows]
        return [
            urge
            for urge in urges
            if urge.created_at <= at
            and (urge.expires_at is None or urge.expires_at > at)
        ]

    def pause(self, until: datetime) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO settings(key, value) VALUES('paused_until', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (until.isoformat(),),
            )
            connection.commit()

    def resume(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM settings WHERE key = 'paused_until'")
            connection.commit()

    def paused_until(self) -> datetime | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = 'paused_until'"
            ).fetchone()
        return datetime.fromisoformat(row["value"]) if row else None

    def _outbox_rows(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM outbox ORDER BY planned_at ASC"
            ).fetchall()

    def daily_message_count(self, at: datetime, zone: ZoneInfo) -> int:
        target_date = at.astimezone(zone).date()
        return sum(
            1
            for row in self._outbox_rows()
            if row["state"] == "planned"
            and datetime.fromisoformat(row["planned_at"]).astimezone(zone).date()
            == target_date
        )

    def unanswered_count(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM outbox WHERE state = 'planned'"
            ).fetchone()
        return int(row["count"])

    def last_message_time(self) -> datetime | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT planned_at FROM outbox
                WHERE state = 'planned'
                ORDER BY planned_at DESC LIMIT 1
                """
            ).fetchone()
        return datetime.fromisoformat(row["planned_at"]) if row else None

    def cooldown_seen_since(self, key: str, since: datetime) -> bool:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT planned_at FROM outbox
                WHERE cooldown_key = ? AND state = 'planned'
                """,
                (key,),
            ).fetchall()
        return any(datetime.fromisoformat(row["planned_at"]) > since for row in rows)

    def record_decision(
        self,
        wake: WakeEvent,
        outbox: OutboxEvent | None,
        *,
        fail_after_wake: bool = False,
    ) -> bool:
        if self.read_only:
            raise sqlite3.OperationalError("cannot write read-only storage")
        with self.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO wake_events (
                        id, trigger, evaluated_at, selected_urge_id,
                        decision, reasons, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        wake.id,
                        wake.trigger,
                        wake.evaluated_at.isoformat(),
                        wake.selected_urge_id,
                        wake.decision.value,
                        json.dumps(wake.reasons, ensure_ascii=False),
                        wake.created_at.isoformat(),
                    ),
                )
                if fail_after_wake:
                    raise sqlite3.OperationalError("injected transaction failure")
                inserted = False
                if outbox is not None:
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO outbox (
                            event_id, wake_event_id, message_preview,
                            idempotency_key, planned_at, delayed_until,
                            state, cooldown_key
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            outbox.event_id,
                            outbox.wake_event_id,
                            outbox.message_preview,
                            outbox.idempotency_key,
                            outbox.planned_at.isoformat(),
                            outbox.delayed_until.isoformat(),
                            outbox.state,
                            outbox.cooldown_key,
                        ),
                    )
                    inserted = cursor.rowcount == 1
                    if not inserted:
                        connection.rollback()
                        return False
                connection.commit()
                return inserted
            except Exception:
                connection.rollback()
                raise

    def count_wake_events(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM wake_events"
            ).fetchone()
        return int(row["count"])

    def count_outbox(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM outbox").fetchone()
        return int(row["count"])

    @staticmethod
    def _wake_dict(row: sqlite3.Row) -> dict[str, object]:
        return {
            "id": row["id"],
            "trigger": row["trigger"],
            "evaluated_at": row["evaluated_at"],
            "selected_urge_id": row["selected_urge_id"],
            "decision": row["decision"],
            "reasons": json.loads(row["reasons"]),
            "created_at": row["created_at"],
        }

    def list_wake_events(self, limit: int = 50) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM wake_events
                ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._wake_dict(row) for row in rows]

    def last_wake_event(self) -> dict[str, object] | None:
        events = self.list_wake_events(limit=1)
        return events[0] if events else None
