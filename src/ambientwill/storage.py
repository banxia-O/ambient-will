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

from ambientwill.models import (
    DESIRE_STATUSES,
    Desire,
    DesireProgress,
    DesireReview,
    OutboxEvent,
    Urge,
    WakeEvent,
)

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

CREATE TABLE IF NOT EXISTS desires (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    urge_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    target_state TEXT NOT NULL,
    current_state TEXT NOT NULL,
    next_step TEXT NOT NULL,
    importance REAL NOT NULL,
    gap REAL NOT NULL,
    confidence REAL NOT NULL,
    actionability REAL NOT NULL,
    interruption_cost REAL NOT NULL,
    cooldown_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    next_review_at TEXT,
    expires_at TEXT,
    status TEXT NOT NULL,
    revision INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS desire_progress (
    id TEXT PRIMARY KEY,
    desire_id TEXT NOT NULL REFERENCES desires(id),
    recorded_at TEXT NOT NULL,
    from_revision INTEGER NOT NULL,
    to_revision INTEGER NOT NULL,
    current_state TEXT NOT NULL,
    next_step TEXT NOT NULL,
    gap REAL NOT NULL,
    actionability REAL NOT NULL,
    next_review_at TEXT,
    status TEXT NOT NULL,
    note TEXT,
    UNIQUE(desire_id, to_revision)
);

CREATE TABLE IF NOT EXISTS desire_reviews (
    id TEXT PRIMARY KEY,
    desire_id TEXT NOT NULL REFERENCES desires(id),
    revision INTEGER NOT NULL,
    evaluated_at TEXT NOT NULL,
    score REAL NOT NULL,
    outcome TEXT NOT NULL,
    urge_id TEXT REFERENCES urges(id),
    reasons TEXT NOT NULL,
    UNIQUE(desire_id, revision)
);

CREATE TABLE IF NOT EXISTS desire_urge_links (
    urge_id TEXT PRIMARY KEY REFERENCES urges(id),
    desire_id TEXT NOT NULL REFERENCES desires(id),
    desire_revision INTEGER NOT NULL,
    UNIQUE(desire_id, desire_revision)
);

CREATE INDEX IF NOT EXISTS idx_urges_status_created
ON urges(status, created_at);

CREATE INDEX IF NOT EXISTS idx_outbox_planned
ON outbox(planned_at);

CREATE UNIQUE INDEX IF NOT EXISTS uq_wake_trigger_evaluated_at
ON wake_events(trigger, evaluated_at);

CREATE INDEX IF NOT EXISTS idx_desires_due
ON desires(status, next_review_at, created_at, id);

CREATE INDEX IF NOT EXISTS idx_desire_progress_history
ON desire_progress(desire_id, to_revision);

CREATE INDEX IF NOT EXISTS idx_desire_reviews_history
ON desire_reviews(desire_id, revision);

CREATE INDEX IF NOT EXISTS idx_desire_urge_links_desire
ON desire_urge_links(desire_id, desire_revision);
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


def _validate_snapshot_entry(
    path: Path,
    *,
    expect_directory: bool,
    label: str,
) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    expected_type = (
        stat.S_ISDIR(metadata.st_mode)
        if expect_directory
        else stat.S_ISREG(metadata.st_mode)
    )
    if not expected_type:
        expected = "directory" if expect_directory else "regular file"
        raise OSError(f"{label} must be a {expected}: {path}")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise PermissionError(
            f"{label} must not be accessible by group or others: {path}"
        )
    return True


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
        directory_exists = _validate_snapshot_entry(
            db_path.parent,
            expect_directory=True,
            label="data directory",
        )
        database_exists = (
            _validate_snapshot_entry(
                db_path,
                expect_directory=False,
                label="database",
            )
            if directory_exists
            else False
        )
        lock_exists = (
            _validate_snapshot_entry(
                lock_path,
                expect_directory=False,
                label="project lock",
            )
            if directory_exists
            else False
        )
        for suffix in ("-wal", "-shm"):
            _validate_snapshot_entry(
                Path(f"{db_path}{suffix}"),
                expect_directory=False,
                label=f"SQLite {suffix[1:].upper()} sidecar",
            )
        if database_exists and not lock_exists:
            raise OSError(
                f"project lock is missing for existing database: {lock_path}; "
                "run ambientwill init before read-only inspection"
            )
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with SnapshotLock(lock_path):
                if database_exists:
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
            try:
                connection.executescript(f"BEGIN IMMEDIATE;\n{SCHEMA}\nCOMMIT;")
            except Exception:
                connection.rollback()
                raise
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

    def add_desire(self, desire: Desire) -> None:
        with TickLock(self.lock_path), self.connect() as connection:
            connection.execute(
                """
                INSERT INTO desires (
                    id, source, urge_type, reason, target_state, current_state,
                    next_step, importance, gap, confidence, actionability,
                    interruption_cost, cooldown_key, created_at, next_review_at,
                    expires_at, status, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    desire.id,
                    desire.source,
                    desire.urge_type,
                    desire.reason,
                    desire.target_state,
                    desire.current_state,
                    desire.next_step,
                    desire.importance,
                    desire.gap,
                    desire.confidence,
                    desire.actionability,
                    desire.interruption_cost,
                    desire.cooldown_key,
                    _canonical_timestamp(desire.created_at),
                    (
                        _canonical_timestamp(desire.next_review_at)
                        if desire.next_review_at
                        else None
                    ),
                    (
                        _canonical_timestamp(desire.expires_at)
                        if desire.expires_at
                        else None
                    ),
                    desire.status,
                    desire.revision,
                ),
            )
            connection.commit()

    @staticmethod
    def _desire_from_row(row: sqlite3.Row) -> Desire:
        return Desire(
            id=row["id"],
            source=row["source"],
            urge_type=row["urge_type"],
            reason=row["reason"],
            target_state=row["target_state"],
            current_state=row["current_state"],
            next_step=row["next_step"],
            importance=float(row["importance"]),
            gap=float(row["gap"]),
            confidence=float(row["confidence"]),
            actionability=float(row["actionability"]),
            interruption_cost=float(row["interruption_cost"]),
            cooldown_key=row["cooldown_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
            next_review_at=(
                datetime.fromisoformat(row["next_review_at"])
                if row["next_review_at"]
                else None
            ),
            expires_at=(
                datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None
            ),
            status=row["status"],
            revision=int(row["revision"]),
        )

    def get_desire(self, desire_id: str) -> Desire | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM desires WHERE id = ?", (desire_id,)
            ).fetchone()
        return self._desire_from_row(row) if row else None

    def list_desires(
        self, *, status: str | None = None, limit: int = 50
    ) -> list[Desire]:
        if status is not None and status not in DESIRE_STATUSES:
            raise ValueError(f"unsupported desire status: {status}")
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        query = "SELECT * FROM desires"
        parameters: list[object] = []
        if status is not None:
            query += " WHERE status = ?"
            parameters.append(status)
        query += (
            " ORDER BY next_review_at IS NULL, next_review_at ASC, "
            "created_at ASC, id ASC LIMIT ?"
        )
        parameters.append(limit)
        with self.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._desire_from_row(row) for row in rows]

    @staticmethod
    def _progress_from_row(row: sqlite3.Row) -> DesireProgress:
        return DesireProgress(
            id=row["id"],
            desire_id=row["desire_id"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
            from_revision=int(row["from_revision"]),
            to_revision=int(row["to_revision"]),
            current_state=row["current_state"],
            next_step=row["next_step"],
            gap=float(row["gap"]),
            actionability=float(row["actionability"]),
            next_review_at=(
                datetime.fromisoformat(row["next_review_at"])
                if row["next_review_at"]
                else None
            ),
            status=row["status"],
            note=row["note"],
        )

    @staticmethod
    def _review_from_row(row: sqlite3.Row) -> DesireReview:
        return DesireReview(
            id=row["id"],
            desire_id=row["desire_id"],
            revision=int(row["revision"]),
            evaluated_at=datetime.fromisoformat(row["evaluated_at"]),
            score=float(row["score"]),
            outcome=row["outcome"],
            urge_id=row["urge_id"],
            reasons=json.loads(row["reasons"]),
        )

    def desire_details(self, desire_id: str) -> dict[str, object]:
        desire = self.get_desire(desire_id)
        if desire is None:
            raise ValueError(f"desire not found: {desire_id}")
        with self.connect() as connection:
            progress_rows = connection.execute(
                """
                SELECT * FROM desire_progress
                WHERE desire_id = ? ORDER BY to_revision ASC, id ASC
                """,
                (desire_id,),
            ).fetchall()
            review_rows = connection.execute(
                """
                SELECT * FROM desire_reviews
                WHERE desire_id = ? ORDER BY revision ASC, id ASC
                """,
                (desire_id,),
            ).fetchall()
        return {
            "desire": desire.to_dict(),
            "progress": [
                self._progress_from_row(row).to_dict() for row in progress_rows
            ],
            "reviews": [self._review_from_row(row).to_dict() for row in review_rows],
        }

    def record_desire_progress(
        self,
        progress: DesireProgress,
        *,
        fail_after_history: bool = False,
        fail_after_urge_expiry: bool = False,
    ) -> Desire:
        with TickLock(self.lock_path), self.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM desires WHERE id = ?", (progress.desire_id,)
                ).fetchone()
                if row is None:
                    raise ValueError(f"desire not found: {progress.desire_id}")
                current = self._desire_from_row(row)
                if current.revision != progress.from_revision:
                    raise ValueError(
                        "revision conflict: "
                        f"expected {progress.from_revision}, current {current.revision}"
                    )
                if current.status in {"satisfied", "abandoned", "expired"}:
                    raise ValueError(
                        f"terminal desire cannot accept progress: {current.status}"
                    )
                if progress.recorded_at < current.created_at:
                    raise ValueError("progress cannot predate desire creation")
                previous = connection.execute(
                    """
                    SELECT recorded_at FROM desire_progress
                    WHERE desire_id = ? ORDER BY to_revision DESC LIMIT 1
                    """,
                    (progress.desire_id,),
                ).fetchone()
                if (
                    previous is not None
                    and progress.recorded_at
                    < datetime.fromisoformat(previous["recorded_at"])
                ):
                    raise ValueError("progress time cannot move backwards")
                connection.execute(
                    """
                    INSERT INTO desire_progress (
                        id, desire_id, recorded_at, from_revision, to_revision,
                        current_state, next_step, gap, actionability,
                        next_review_at, status, note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        progress.id,
                        progress.desire_id,
                        _canonical_timestamp(progress.recorded_at),
                        progress.from_revision,
                        progress.to_revision,
                        progress.current_state,
                        progress.next_step,
                        progress.gap,
                        progress.actionability,
                        (
                            _canonical_timestamp(progress.next_review_at)
                            if progress.next_review_at
                            else None
                        ),
                        progress.status,
                        progress.note,
                    ),
                )
                if fail_after_history:
                    raise sqlite3.OperationalError(
                        "injected progress transaction failure"
                    )
                connection.execute(
                    """
                    UPDATE urges
                    SET status = 'expired'
                    WHERE status = 'open'
                      AND id IN (
                          SELECT urge_id FROM desire_urge_links
                          WHERE desire_id = ? AND desire_revision < ?
                      )
                    """,
                    (progress.desire_id, progress.to_revision),
                )
                if fail_after_urge_expiry:
                    raise sqlite3.OperationalError(
                        "injected urge expiry transaction failure"
                    )
                cursor = connection.execute(
                    """
                    UPDATE desires
                    SET current_state = ?, next_step = ?, gap = ?,
                        actionability = ?, next_review_at = ?, status = ?, revision = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (
                        progress.current_state,
                        progress.next_step,
                        progress.gap,
                        progress.actionability,
                        (
                            _canonical_timestamp(progress.next_review_at)
                            if progress.next_review_at
                            else None
                        ),
                        progress.status,
                        progress.to_revision,
                        progress.desire_id,
                        progress.from_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise sqlite3.IntegrityError(
                        "desire projection changed during progress update"
                    )
                updated_row = connection.execute(
                    "SELECT * FROM desires WHERE id = ?", (progress.desire_id,)
                ).fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self._desire_from_row(updated_row)

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
