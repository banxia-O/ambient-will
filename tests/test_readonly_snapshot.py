from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import make_desire

import ambientwill.storage as storage_module
from ambientwill.cli import main
from ambientwill.storage import AlreadyRunningError, Storage, TickLock


def metadata(path: Path) -> dict[str, tuple[int, int, int, int, int, str | None]]:
    items = [path, *path.iterdir()]
    result = {}
    for item in items:
        info = os.lstat(item)
        digest = (
            hashlib.sha256(item.read_bytes()).hexdigest()
            if stat.S_ISREG(info.st_mode)
            else None
        )
        result["." if item == path else item.name] = (
            info.st_size,
            info.st_mtime_ns,
            info.st_mode,
            info.st_ino,
            info.st_ctime_ns,
            digest,
        )
    return result


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
    os.chmod(path, 0o600)
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
    original_copy = storage_module._copy_snapshot_file
    writer_was_blocked = False

    def probing_copy(descriptor, target):
        nonlocal writer_was_blocked
        if not writer_was_blocked:
            with pytest.raises(AlreadyRunningError), TickLock(storage.lock_path):
                pass
            writer_was_blocked = True
        return original_copy(descriptor, target)

    monkeypatch.setattr(storage_module, "_copy_snapshot_file", probing_copy)
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


@pytest.mark.parametrize("target", ["directory", "database", "lock"])
def test_snapshot_rejects_permissive_component_without_mutation(
    tmp_path: Path, target: str
) -> None:
    storage = Storage(tmp_path / "data" / "ambientwill.db")
    storage.initialize()
    selected = {
        "directory": storage.path.parent,
        "database": storage.path,
        "lock": storage.lock_path,
    }[target]
    os.chmod(selected, 0o755 if target == "directory" else 0o644)
    before = metadata(storage.path.parent)

    with pytest.raises(PermissionError, match="group or others"):
        Storage.snapshot(storage.path)

    assert metadata(storage.path.parent) == before


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_snapshot_rejects_permissive_sqlite_sidecar_without_mutation(
    tmp_path: Path, suffix: str
) -> None:
    storage = Storage(tmp_path / "data" / "ambientwill.db")
    storage.initialize()
    writer = sqlite3.connect(storage.path)
    try:
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute(
            "INSERT INTO settings(key, value) VALUES('sidecar_probe', 'private')"
        )
        writer.commit()
        sidecar = Path(f"{storage.path}{suffix}")
        assert sidecar.exists()
        os.chmod(sidecar, 0o644)
        before = metadata(storage.path.parent)

        with pytest.raises(PermissionError, match="group or others"):
            Storage.snapshot(storage.path)

        assert metadata(storage.path.parent) == before
    finally:
        writer.close()


@pytest.mark.parametrize(
    ("target", "label"),
    [
        ("directory", "data directory"),
        ("database", "database"),
        ("lock", "project lock"),
        ("wal", "SQLite WAL sidecar"),
        ("shm", "SQLite SHM sidecar"),
    ],
)
def test_snapshot_fails_closed_when_validated_path_is_replaced(
    tmp_path: Path, monkeypatch, target: str, label: str
) -> None:
    storage = Storage(tmp_path / "data" / "ambientwill.db")
    storage.initialize()
    writer = sqlite3.connect(storage.path)
    try:
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("INSERT INTO settings(key, value) VALUES('trusted', 'original')")
        writer.commit()

        replacements = tmp_path / "replacements"
        replacement_storage = Storage(replacements / "ambientwill.db")
        replacement_storage.initialize()
        with replacement_storage.connect() as connection:
            connection.execute(
                "INSERT INTO settings(key, value) VALUES('foreign', 'replacement')"
            )
            connection.commit()

        if target in {"wal", "shm"}:
            suffix = f"-{target}"
            selected_replacement = tmp_path / f"replacement{suffix}"
            shutil.copy2(Path(f"{storage.path}{suffix}"), selected_replacement)
            os.chmod(selected_replacement, 0o600)
        elif target == "database":
            selected_replacement = replacement_storage.path
        elif target == "lock":
            selected_replacement = replacement_storage.lock_path
        else:
            selected_replacement = replacements

        original_validate = storage_module._validate_snapshot_entry
        replaced = False

        def replace_after_validation(path, *, expect_directory, label):
            nonlocal replaced
            result = original_validate(
                path,
                expect_directory=expect_directory,
                label=label,
            )
            if not replaced and label == expected_label:
                if target == "directory":
                    os.replace(storage.path.parent, tmp_path / "original-data")
                    os.replace(selected_replacement, storage.path.parent)
                else:
                    selected = {
                        "database": storage.path,
                        "lock": storage.lock_path,
                        "wal": Path(f"{storage.path}-wal"),
                        "shm": Path(f"{storage.path}-shm"),
                    }[target]
                    os.replace(selected_replacement, selected)
                replaced = True
            return result

        expected_label = label
        monkeypatch.setattr(
            storage_module,
            "_validate_snapshot_entry",
            replace_after_validation,
        )

        with pytest.raises(OSError, match="changed during snapshot"):
            Storage.snapshot(storage.path)

        assert replaced is True
    finally:
        writer.close()


def test_snapshot_fails_closed_when_database_path_changes_during_copy(
    tmp_path: Path, monkeypatch
) -> None:
    storage = Storage(tmp_path / "data" / "ambientwill.db")
    storage.initialize()
    replacement = Storage(tmp_path / "replacement" / "ambientwill.db")
    replacement.initialize()
    original_copy = storage_module._copy_snapshot_file
    replaced = False

    def replace_while_copying(descriptor, target):
        nonlocal replaced
        if not replaced:
            os.replace(replacement.path, storage.path)
            replaced = True
        return original_copy(descriptor, target)

    monkeypatch.setattr(
        storage_module,
        "_copy_snapshot_file",
        replace_while_copying,
    )

    with pytest.raises(OSError, match="database changed during snapshot"):
        Storage.snapshot(storage.path)

    assert replaced is True


@pytest.mark.parametrize(
    "arguments",
    [
        ["desire-list"],
        ["desire-show", "--id", "desire-1"],
        [
            "desire-review",
            "--at",
            "2026-02-01T13:00:00+00:00",
            "--dry-run",
        ],
        ["status"],
        ["events"],
        ["why"],
        ["tick", "--at", "2026-02-01T13:00:00+00:00", "--dry-run"],
        ["simulate", "--at", "2026-02-01T13:00:00+00:00"],
    ],
)
def test_readonly_cli_commands_fail_closed_for_permissive_database(
    tmp_path: Path, capsys, arguments: list[str]
) -> None:
    config = tmp_path / "ambientwill.toml"
    data = tmp_path / "data"
    assert (
        main(["init", "--config", str(config), "--data-dir", str(data), "--json"]) == 0
    )
    capsys.readouterr()
    storage = Storage(data / "ambientwill.db")
    storage.add_desire(make_desire())
    os.chmod(storage.path, 0o644)
    before = metadata(data)
    command = [*arguments]
    if arguments[0] not in {"status", "events", "why"}:
        command.extend(["--config", str(config)])
    command.extend(["--data-dir", str(data), "--json"])

    code = main(command)
    payload = json.loads(capsys.readouterr().out)

    assert code != 0
    assert payload["blocked_by"] == "storage_error"
    assert "desire" not in payload
    assert "desires" not in payload
    assert metadata(data) == before
