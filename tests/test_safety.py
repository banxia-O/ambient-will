from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ambientwill.cli import main
from ambientwill.models import Decision, WakeEvent
from ambientwill.storage import AlreadyRunningError, Storage, TickLock

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_pause_uses_same_project_lock(store) -> None:
    with TickLock(store.lock_path), pytest.raises(AlreadyRunningError):
        store.pause(NOW)


def test_initialize_uses_private_permissions(tmp_path: Path) -> None:
    data_dir = tmp_path / "private-data"
    storage = Storage(data_dir / "ambientwill.db")

    storage.initialize()

    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(storage.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(storage.lock_path.stat().st_mode) == 0o600


def test_initialize_rejects_permissive_existing_directory_without_chmod(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "shared-data"
    data_dir.mkdir(mode=0o755)
    os.chmod(data_dir, 0o755)
    storage = Storage(data_dir / "ambientwill.db")

    with pytest.raises(PermissionError):
        storage.initialize()

    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o755
    assert not storage.path.exists()


def test_init_rejects_database_symlink(tmp_path: Path, capsys) -> None:
    target = tmp_path / "foreign.db"
    target.write_bytes(b"foreign")
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    (data / "ambientwill.db").symlink_to(target)
    config = tmp_path / "ambientwill.toml"

    code = main(["init", "--config", str(config), "--data-dir", str(data), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code != 0
    assert payload["decision"] == "SLEEP"
    assert target.read_bytes() == b"foreign"


def test_tick_lock_rejects_symlink_without_touching_target(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    target = tmp_path / "foreign"
    target.write_text("do-not-touch", encoding="utf-8")
    os.chmod(target, 0o644)
    (data / "tick.lock").symlink_to(target)

    with pytest.raises(OSError), TickLock(data / "tick.lock"):
        pass

    assert target.read_text(encoding="utf-8") == "do-not-touch"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_initialize_rejects_nested_ancestor_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    storage = Storage(link / "nested" / "ambientwill.db")

    with pytest.raises(OSError):
        storage.initialize()

    assert not (real / "nested" / "ambientwill.db").exists()


def test_initialize_uses_project_lock(store) -> None:
    with TickLock(store.lock_path), pytest.raises(AlreadyRunningError):
        store.initialize()


def test_cli_init_lock_conflict_writes_nothing(tmp_path: Path, capsys) -> None:
    data = tmp_path / "data"
    storage = Storage(data / "ambientwill.db")
    storage.initialize()
    config = tmp_path / "new" / "ambientwill.toml"

    with TickLock(storage.lock_path):
        before_lock = storage.lock_path.stat()
        code = main(
            [
                "init",
                "--config",
                str(config),
                "--data-dir",
                str(data),
                "--json",
            ]
        )
        after_lock = storage.lock_path.stat()
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert payload["blocked_by"] == "already_running"
    assert not config.exists()
    assert not config.parent.exists()
    assert (
        after_lock.st_mode,
        after_lock.st_ino,
        after_lock.st_size,
        after_lock.st_mtime_ns,
        after_lock.st_ctime_ns,
    ) == (
        before_lock.st_mode,
        before_lock.st_ino,
        before_lock.st_size,
        before_lock.st_mtime_ns,
        before_lock.st_ctime_ns,
    )


def test_public_record_decision_uses_project_lock(store) -> None:
    wake = WakeEvent(
        id="aw_wake_lock_probe",
        trigger="test",
        evaluated_at=NOW,
        selected_urge_id=None,
        decision=Decision.SLEEP,
        reasons=[],
        created_at=NOW,
    )

    with TickLock(store.lock_path), pytest.raises(AlreadyRunningError):
        store.record_decision(wake, None)


def test_error_json_fails_closed_for_corrupt_database(tmp_path: Path, capsys) -> None:
    config = tmp_path / "ambientwill.toml"
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    (data / "ambientwill.db").write_text("not sqlite", encoding="utf-8")
    main(
        [
            "init",
            "--config",
            str(config),
            "--data-dir",
            str(tmp_path / "good"),
            "--json",
        ]
    )
    capsys.readouterr()

    code = main(["tick", "--config", str(config), "--data-dir", str(data), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code != 0
    assert payload["decision"] == "SLEEP"
    assert payload["blocked_by"] == "storage_error"
