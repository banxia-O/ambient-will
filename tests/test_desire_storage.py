from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ambientwill.desires import DesireReviewer
from ambientwill.models import Desire, DesireProgress
from ambientwill.storage import Storage

NOW = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)


def make_desire(desire_id: str = "desire-1", **changes) -> Desire:
    values = {
        "id": desire_id,
        "source": "project_goal",
        "urge_type": "follow_up",
        "reason": "Advance an anonymous project goal.",
        "target_state": "The next checkpoint is complete.",
        "current_state": "The checkpoint is pending.",
        "next_step": "Complete the next anonymous checkpoint.",
        "importance": 0.8,
        "gap": 0.7,
        "confidence": 0.6,
        "actionability": 0.9,
        "interruption_cost": 0.2,
        "cooldown_key": "project-checkpoint",
        "created_at": NOW,
        "next_review_at": NOW + timedelta(hours=1),
        "expires_at": NOW + timedelta(days=7),
        "status": "open",
        "revision": 1,
    }
    values.update(changes)
    return Desire(**values)


def test_initialize_upgrades_v01_database_without_changing_old_data(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    database = data / "ambientwill.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO settings(key, value) VALUES('legacy_probe', 'preserved');
        """
    )
    connection.commit()
    connection.close()
    os.chmod(database, 0o600)

    Storage(database).initialize()

    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        legacy = connection.execute(
            "SELECT value FROM settings WHERE key = 'legacy_probe'"
        ).fetchone()
    finally:
        connection.close()

    assert {"desires", "desire_progress", "desire_reviews"} <= tables
    assert legacy == ("preserved",)


def test_v02_schema_upgrade_is_atomic_under_authorizer_failure(
    tmp_path: Path, monkeypatch
) -> None:
    data = tmp_path / "legacy-data"
    data.mkdir(mode=0o700)
    database = data / "ambientwill.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE urges (
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
        CREATE TABLE wake_events (
            id TEXT PRIMARY KEY,
            trigger TEXT NOT NULL,
            evaluated_at TEXT NOT NULL,
            selected_urge_id TEXT,
            decision TEXT NOT NULL,
            reasons TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE outbox (
            event_id TEXT PRIMARY KEY,
            wake_event_id TEXT NOT NULL REFERENCES wake_events(id),
            message_preview TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            planned_at TEXT NOT NULL,
            delayed_until TEXT NOT NULL,
            state TEXT NOT NULL,
            cooldown_key TEXT NOT NULL
        );
        INSERT INTO settings VALUES ('legacy_probe', 'preserved');
        INSERT INTO urges VALUES (
            'legacy-urge', 'follow_up', 'Anonymous legacy reason.',
            0.7, 0.8, 0.2, 'legacy', '2026-01-01T12:00:00+00:00',
            NULL, 'open'
        );
        INSERT INTO wake_events VALUES (
            'legacy-wake', 'legacy', '2026-01-01T12:00:00+00:00',
            'legacy-urge', 'REFLECT', '[]', '2026-01-01T12:00:00+00:00'
        );
        INSERT INTO outbox VALUES (
            'legacy-outbox', 'legacy-wake', 'Anonymous preview.',
            'legacy-key', '2026-01-01T12:00:00+00:00',
            '2026-01-01T12:00:00+00:00', 'planned', 'legacy'
        );
        """
    )
    connection.commit()
    os.chmod(database, 0o600)

    def master_rows(db: sqlite3.Connection) -> list[tuple]:
        return db.execute(
            """
            SELECT type, name, tbl_name, sql FROM sqlite_master
            ORDER BY type, name
            """
        ).fetchall()

    before_master = master_rows(connection)
    before_data = {
        table: connection.execute(f"SELECT * FROM {table}").fetchall()
        for table in ("settings", "urges", "wake_events", "outbox")
    }
    connection.close()

    storage = Storage(database)
    original_connect = storage.connect

    def deny_second_v02_table(action, argument1, _argument2, _database, _trigger):
        if action == sqlite3.SQLITE_CREATE_TABLE and argument1 == "desire_progress":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @contextmanager
    def failing_connect():
        with original_connect() as active:
            active.set_authorizer(deny_second_v02_table)
            yield active

    monkeypatch.setattr(storage, "connect", failing_connect)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        storage.initialize()

    connection = sqlite3.connect(database)
    try:
        assert master_rows(connection) == before_master
        assert {
            table: connection.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("settings", "urges", "wake_events", "outbox")
        } == before_data
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
    finally:
        connection.close()

    monkeypatch.setattr(storage, "connect", original_connect)
    storage.initialize()
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "desires",
            "desire_progress",
            "desire_reviews",
            "desire_urge_links",
        } <= tables
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert {
            table: connection.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("settings", "urges", "wake_events", "outbox")
        } == before_data
    finally:
        connection.close()


def test_add_list_and_show_desire_are_stable_and_auditable(store: Storage) -> None:
    later = make_desire("later", next_review_at=NOW + timedelta(hours=2))
    sooner = make_desire("sooner", next_review_at=NOW + timedelta(minutes=30))
    blocked = make_desire(
        "blocked",
        status="blocked",
        next_review_at=None,
        created_at=NOW - timedelta(days=1),
    )

    store.add_desire(later)
    store.add_desire(sooner)
    store.add_desire(blocked)

    assert [item.id for item in store.list_desires()] == [
        "sooner",
        "later",
        "blocked",
    ]
    assert [item.id for item in store.list_desires(status="open", limit=1)] == [
        "sooner"
    ]
    details = store.desire_details("later")
    assert details["desire"]["revision"] == 1
    assert details["progress"] == []
    assert details["reviews"] == []


def test_duplicate_desire_id_fails_without_changing_existing_row(
    store: Storage,
) -> None:
    original = make_desire()
    store.add_desire(original)

    with pytest.raises(sqlite3.IntegrityError):
        store.add_desire(make_desire(reason="A different anonymous reason."))

    assert store.get_desire("desire-1") == original


def test_desire_queries_fail_closed_for_invalid_input(store: Storage) -> None:
    with pytest.raises(ValueError, match="status"):
        store.list_desires(status="unknown")
    with pytest.raises(ValueError, match="limit"):
        store.list_desires(limit=0)
    with pytest.raises(ValueError, match="not found"):
        store.desire_details("missing")


def make_progress(
    *,
    progress_id: str = "progress-1",
    from_revision: int = 1,
    recorded_at: datetime = NOW + timedelta(minutes=30),
    status: str = "open",
    next_review_at: datetime | None = NOW + timedelta(hours=2),
) -> DesireProgress:
    return DesireProgress(
        id=progress_id,
        desire_id="desire-1",
        recorded_at=recorded_at,
        from_revision=from_revision,
        to_revision=from_revision + 1,
        current_state="The checkpoint is in progress.",
        next_step="Finish the checkpoint.",
        gap=0.5,
        actionability=0.8,
        next_review_at=next_review_at,
        status=status,
        note="Anonymous progress note.",
    )


def test_progress_atomically_appends_history_and_updates_projection(
    store: Storage,
) -> None:
    store.add_desire(make_desire())

    updated = store.record_desire_progress(make_progress())

    assert updated.revision == 2
    assert updated.current_state == "The checkpoint is in progress."
    assert updated.gap == 0.5
    details = store.desire_details("desire-1")
    assert [item["to_revision"] for item in details["progress"]] == [2]


def test_progress_revision_conflict_fails_closed_without_partial_write(
    store: Storage,
) -> None:
    store.add_desire(make_desire())
    store.record_desire_progress(make_progress())
    before = store.desire_details("desire-1")

    with pytest.raises(ValueError, match="revision conflict"):
        store.record_desire_progress(make_progress(progress_id="stale"))

    assert store.desire_details("desire-1") == before


def test_blocked_can_reopen_but_terminal_desire_cannot(store: Storage) -> None:
    store.add_desire(make_desire(status="blocked", next_review_at=None))
    reopened = store.record_desire_progress(make_progress())
    assert reopened.status == "open"

    store.record_desire_progress(
        make_progress(
            progress_id="satisfied",
            from_revision=2,
            status="satisfied",
            next_review_at=None,
        )
    )
    before = store.desire_details("desire-1")
    with pytest.raises(ValueError, match="terminal"):
        store.record_desire_progress(
            make_progress(progress_id="reopen-terminal", from_revision=3)
        )
    assert store.desire_details("desire-1") == before


def test_progress_failure_after_history_rolls_back_projection_and_history(
    store: Storage,
) -> None:
    store.add_desire(make_desire())

    with pytest.raises(sqlite3.OperationalError, match="injected"):
        store.record_desire_progress(make_progress(), fail_after_history=True)

    details = store.desire_details("desire-1")
    assert details["desire"]["revision"] == 1
    assert details["progress"] == []


def test_progress_cannot_be_backfilled_before_current_revision_review(
    store: Storage, policy
) -> None:
    review_at = NOW + timedelta(hours=1)
    store.add_desire(make_desire())
    DesireReviewer(policy, store).review(at=review_at)
    before = store.desire_details("desire-1")

    with pytest.raises(ValueError, match="predate current revision review"):
        store.record_desire_progress(
            make_progress(recorded_at=review_at - timedelta(microseconds=1))
        )

    assert store.desire_details("desire-1") == before
    assert len(store.valid_urges(review_at)) == 1


def test_progress_at_current_revision_review_boundary_is_allowed(
    store: Storage, policy
) -> None:
    review_at = NOW + timedelta(hours=1)
    store.add_desire(make_desire())
    DesireReviewer(policy, store).review(at=review_at)

    updated = store.record_desire_progress(
        make_progress(recorded_at=review_at, next_review_at=review_at)
    )

    assert updated.revision == 2
    assert store.desire_details("desire-1")["progress"][0]["recorded_at"] == (
        review_at.isoformat()
    )


def test_progress_backfill_compares_review_offsets_as_absolute_instants(
    store: Storage, policy
) -> None:
    review_at = NOW + timedelta(hours=1)
    store.add_desire(make_desire())
    DesireReviewer(policy, store).review(at=review_at)
    with store.connect() as connection:
        connection.execute(
            """
            UPDATE desire_reviews SET evaluated_at = ?
            WHERE desire_id = 'desire-1' AND revision = 1
            """,
            ("2026-02-01T14:00:00+01:00",),
        )
        connection.commit()

    with pytest.raises(ValueError, match="predate current revision review"):
        store.record_desire_progress(
            make_progress(recorded_at=review_at - timedelta(microseconds=1))
        )

    with store.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM desire_progress").fetchone()[0]
            == 0
        )
        assert connection.execute("SELECT status FROM urges").fetchone()[0] == "open"
    assert store.get_desire("desire-1").revision == 1


def test_invalid_current_revision_review_time_blocks_progress_atomically(
    store: Storage, policy
) -> None:
    review_at = NOW + timedelta(hours=1)
    store.add_desire(make_desire())
    DesireReviewer(policy, store).review(at=review_at)
    with store.connect() as connection:
        connection.execute(
            """
            UPDATE desire_reviews SET evaluated_at = 'invalid'
            WHERE desire_id = 'desire-1' AND revision = 1
            """
        )
        connection.commit()

    with pytest.raises(ValueError):
        store.record_desire_progress(make_progress(recorded_at=review_at))

    with store.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM desire_progress").fetchone()[0]
            == 0
        )
        assert connection.execute("SELECT status FROM urges").fetchone()[0] == "open"
    assert store.get_desire("desire-1").revision == 1


@pytest.mark.parametrize("terminal", ["satisfied", "abandoned", "expired"])
def test_every_terminal_status_rejects_further_progress(
    store: Storage, terminal: str
) -> None:
    store.add_desire(make_desire(status=terminal, next_review_at=None))

    with pytest.raises(ValueError, match="terminal"):
        store.record_desire_progress(make_progress())

    assert store.get_desire("desire-1").revision == 1
    assert store.desire_details("desire-1")["progress"] == []
