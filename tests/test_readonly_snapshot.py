from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import make_desire

import ambientwill.storage as storage_module
from ambientwill.cli import main
from ambientwill.storage import AlreadyRunningError, Storage, TickLock


def metadata(path: Path) -> dict[str, tuple[int, int, int]]:
    return {
        item.name: (item.stat().st_size, item.stat().st_mtime_ns, item.stat().st_mode)
        for item in path.iterdir()
    }


def test_snapshot_copies_active_wal_into_memory_without_touching_source(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    storage = Storage(data / "ambientwill.db")
    storage.initialize()

    writer = sqlite3.connect(storage.path)
    try:
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute(
            "INSERT INTO settings(key, value) VALUES('probe', 'visible-in-wal')"
        )
        writer.commit()
        before = metadata(data)

        snapshot = Storage.snapshot(storage.path)
        with snapshot.connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = 'probe'"
            ).fetchone()

        after = metadata(data)
    finally:
        writer.close()

    assert row["value"] == "visible-in-wal"
    assert snapshot.read_only is True
    assert snapshot._memory_connection is not None
    assert after == before


def test_missing_database_snapshot_is_in_memory_and_creates_no_directory(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing" / "ambientwill.db"

    snapshot = Storage.snapshot(path)

    assert snapshot._memory_connection is not None
    assert snapshot.count_wake_events() == 0
    assert not path.parent.exists()


def test_existing_database_without_project_lock_fails_closed(tmp_path: Path) -> None:
    data = tmp_path / "legacy"
    data.mkdir(mode=0o700)
    path = data / "ambientwill.db"
    sqlite3.connect(path).close()
    before = metadata(data)

    with pytest.raises(OSError, match="project lock is missing"):
        Storage.snapshot(path)

    assert metadata(data) == before
    assert not (data / "tick.lock").exists()


def test_snapshot_fails_closed_while_writer_holds_project_lock(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "data" / "ambientwill.db")
    storage.initialize()

    with TickLock(storage.lock_path), pytest.raises(AlreadyRunningError):
        Storage.snapshot(storage.path)


def test_snapshot_shared_lock_blocks_project_writer_during_copy(
    tmp_path: Path, monkeypatch
) -> None:
    storage = Storage(tmp_path / "data" / "ambientwill.db")
    storage.initialize()
    original_copy = storage_module.shutil.copy2
    writer_was_blocked = False

    def probing_copy(source, target):
        nonlocal writer_was_blocked
        if not writer_was_blocked:
            with pytest.raises(AlreadyRunningError), TickLock(storage.lock_path):
                pass
            writer_was_blocked = True
        return original_copy(source, target)

    monkeypatch.setattr(storage_module.shutil, "copy2", probing_copy)
    snapshot = Storage.snapshot(storage.path)

    assert writer_was_blocked is True
    assert snapshot.count_wake_events() == 0


def test_desire_review_dry_run_preserves_all_source_metadata(
    tmp_path: Path, capsys
) -> None:
    config = tmp_path / "ambientwill.toml"
    data = tmp_path / "data"
    assert (
        main(["init", "--config", str(config), "--data-dir", str(data), "--json"]) == 0
    )
    capsys.readouterr()
    created = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
    storage = Storage(data / "ambientwill.db")
    storage.add_desire(
        make_desire(
            created_at=created,
            next_review_at=created + timedelta(hours=1),
        )
    )
    before = metadata(data)

    assert (
        main(
            [
                "desire-review",
                "--config",
                str(config),
                "--data-dir",
                str(data),
                "--at",
                "2026-02-01T13:00:00+00:00",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["results"][0]["outcome"] == "URGE_CREATED"
    assert metadata(data) == before
    assert storage.desire_details("desire-1")["reviews"] == []
    assert storage.valid_urges(created + timedelta(hours=1)) == []


def test_desire_list_and_show_preserve_all_source_metadata(
    tmp_path: Path, capsys
) -> None:
    config = tmp_path / "ambientwill.toml"
    data = tmp_path / "data"
    main(["init", "--config", str(config), "--data-dir", str(data), "--json"])
    capsys.readouterr()
    storage = Storage(data / "ambientwill.db")
    storage.add_desire(make_desire())
    before = metadata(data)

    assert main(["desire-list", "--data-dir", str(data), "--json"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "desire-show",
                "--data-dir",
                str(data),
                "--id",
                "desire-1",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert metadata(data) == before
