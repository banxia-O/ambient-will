from __future__ import annotations

import fcntl
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo

from ambientwill.models import OutboxEvent, Urge, WakeEvent

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

CREATE UNIQUE INDEX IF NOT EXISTS uq_wake_trigger_evaluated_at
ON wake_events(trigger, evaluated_at);
"""


class AlreadyRunningError(RuntimeError):
    pass


def _canonical_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _reject_symlink_components(path: Path) -> None:
    """Reject any existing symlink in a lexical absolute path."""
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode):
            raise OSError(f"refusing symlink path component: {current}")


class TickLock:
    def __init__(self, path: Path):
        self.path = path
        self._handle = None

    def __enter__(self) -> Self:
        _reject_symlink_components(self.path)
        if not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, mode=0o700)
        elif self.path.parent.stat().st_mode & 0o077:
            raise PermissionError(
                "data directory must not be accessible by group or others: "
                f"{self.path.parent}"
            )
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
        descriptor = os.open(self.path, flags, 0o600)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise OSError(f"lock must be a regular file: {self.path}")
        self._handle = os.fdopen(descriptor, "r+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            self._handle = None
            raise AlreadyRunningError("another tick holds the project lock") from exc
        os.fchmod(self._handle.fileno(), 0o600)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


class SnapshotLock:
    """Acquire a shared project lock without creating or modifying lock state."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def __enter__(self) -> Self:
        _reject_symlink_components(self.path)
        if not self.path.exists():
            return self
        descriptor = os.open(self.path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise OSError(f"lock must be a regular file: {self.path}")
        self._handle = os.fdopen(descriptor, "rb")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            self._handle = None
            raise AlreadyRunningError("a writer is already running") from exc
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()


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
    def snapshot(cls, path: str | Path) -> Storage:
        db_path = Path(path)
        _reject_symlink_components(db_path)
        lock_path = db_path.parent / "tick.lock"
        if db_path.exists() and not lock_path.exists():
            raise OSError(
                f"project lock is missing for existing database: {lock_path}; "
                "run ambientwill init before read-only inspection"
            )
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with SnapshotLock(lock_path):
                if db_path.exists():
                    if db_path.is_symlink():
                        raise OSError(f"refusing database symlink: {db_path}")
                    with tempfile.TemporaryDirectory(
                        prefix="ambientwill-snapshot-"
                    ) as temp:
                        copied = Path(temp) / db_path.name
                        shutil.copy2(db_path, copied)
                        for suffix in ("-wal", "-shm"):
                            sidecar = Path(f"{db_path}{suffix}")
                            if sidecar.exists():
                                shutil.copy2(sidecar, Path(f"{copied}{suffix}"))
                        source = sqlite3.connect(copied, timeout=1.0)
                        try:
                            source.backup(connection)
                        finally:
                            source.close()
                    result = connection.execute("PRAGMA quick_check").fetchone()
                    if result is None or result[0] != "ok":
                        raise sqlite3.DatabaseError("snapshot integrity check failed")
                else:
                    connection.executescript(SCHEMA)
        except Exception:
            connection.close()
            raise
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
            _reject_symlink_components(self.path)
            if not self.path.parent.exists():
                self.path.parent.mkdir(parents=True, mode=0o700)
            elif self.path.parent.stat().st_mode & 0o077:
                raise PermissionError(
                    "data directory must not be accessible by group or others: "
                    f"{self.path.parent}"
                )
            if self.path.exists() and self.path.stat().st_mode & 0o077:
                raise PermissionError(
                    f"database must not be accessible by group or others: {self.path}"
                )
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
        with TickLock(self.lock_path):
            self._initialize_unlocked()

    def _initialize_unlocked(self) -> None:
        _reject_symlink_components(self.path)
        if not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, mode=0o700)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            connection.commit()
            os.chmod(self.path, 0o600)

    def add_urge(self, urge: Urge) -> None:
        with TickLock(self.lock_path), self.connect() as connection:
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
                    _canonical_timestamp(urge.created_at),
                    _canonical_timestamp(urge.expires_at) if urge.expires_at else None,
                    urge.status,
                ),
            )
            connection.commit()

    def set_urge_status(self, urge_id: str, status: str) -> None:
        if status not in {"open", "closed", "expired"}:
            raise ValueError(f"unsupported urge status: {status}")
        with TickLock(self.lock_path), self.connect() as connection:
            connection.execute(
                "UPDATE urges SET status = ? WHERE id = ?", (status, urge_id)
            )
            connection.commit()

    def get_urge_status(self, urge_id: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status FROM urges WHERE id = ?", (urge_id,)
            ).fetchone()
        return str(row["status"]) if row else None

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
                datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None
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
        with TickLock(self.lock_path), self.connect() as connection:
            connection.execute(
                """
                INSERT INTO settings(key, value) VALUES('paused_until', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (_canonical_timestamp(until),),
            )
            connection.commit()

    def resume(self) -> None:
        with TickLock(self.lock_path), self.connect() as connection:
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
            and datetime.fromisoformat(row["delayed_until"]).astimezone(zone).date()
            == target_date
        )

    def last_feedback_at(self) -> datetime | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = 'last_feedback_at'"
            ).fetchone()
        return datetime.fromisoformat(row["value"]) if row else None

    def record_feedback(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("feedback time must include a timezone")
        if at > datetime.now(UTC):
            raise ValueError("feedback time cannot be in the future")
        with TickLock(self.lock_path), self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = 'last_feedback_at'"
            ).fetchone()
            previous = datetime.fromisoformat(row["value"]) if row else None
            if previous is not None and at < previous:
                raise ValueError(
                    "feedback time cannot be earlier than the previous marker"
                )
            connection.execute(
                """
                INSERT INTO settings(key, value) VALUES('last_feedback_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (_canonical_timestamp(at),),
            )
            connection.commit()

    def unanswered_count(self, at: datetime) -> int:
        feedback = self.last_feedback_at()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT delayed_until FROM outbox WHERE state = 'planned'"
            ).fetchall()
        return sum(
            1
            for row in rows
            if (delayed := datetime.fromisoformat(row["delayed_until"])) <= at
            and (feedback is None or delayed > feedback)
        )

    def last_message_time(self) -> datetime | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT delayed_until FROM outbox
                WHERE state = 'planned'
                ORDER BY delayed_until DESC LIMIT 1
                """
            ).fetchone()
        return datetime.fromisoformat(row["delayed_until"]) if row else None

    def cooldown_seen_since(self, key: str, since: datetime) -> bool:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT delayed_until FROM outbox
                WHERE cooldown_key = ? AND state = 'planned'
                """,
                (key,),
            ).fetchall()
        return any(datetime.fromisoformat(row["delayed_until"]) > since for row in rows)

    def record_decision(
        self,
        wake: WakeEvent,
        outbox: OutboxEvent | None,
        *,
        fail_after_wake: bool = False,
    ) -> bool:
        with TickLock(self.lock_path):
            return self._record_decision_unlocked(
                wake, outbox, fail_after_wake=fail_after_wake
            )

    def _record_decision_unlocked(
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
                        _canonical_timestamp(wake.evaluated_at),
                        wake.selected_urge_id,
                        wake.decision.value,
                        json.dumps(wake.reasons, ensure_ascii=False),
                        _canonical_timestamp(wake.created_at),
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
                            _canonical_timestamp(outbox.planned_at),
                            _canonical_timestamp(outbox.delayed_until),
                            outbox.state,
                            outbox.cooldown_key,
                        ),
                    )
                    inserted = cursor.rowcount == 1
                    if not inserted:
                        connection.rollback()
                        return False
                    if wake.selected_urge_id is None:
                        raise sqlite3.IntegrityError(
                            "message plan requires a selected urge"
                        )
                    cursor = connection.execute(
                        """
                        UPDATE urges SET status = 'closed'
                        WHERE id = ? AND status = 'open'
                        """,
                        (wake.selected_urge_id,),
                    )
                    if cursor.rowcount != 1:
                        raise sqlite3.IntegrityError(
                            "selected urge is missing or no longer open"
                        )
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

    def wake_seen(self, trigger: str, evaluated_at: datetime) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM wake_events
                WHERE trigger = ? AND evaluated_at = ?
                LIMIT 1
                """,
                (trigger, _canonical_timestamp(evaluated_at)),
            ).fetchone()
        return row is not None

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
