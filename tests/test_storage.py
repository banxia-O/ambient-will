from datetime import datetime, timedelta, timezone

from ambientwill.engine import Engine

from conftest import make_urge


NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_pause_and_resume(store) -> None:
    until = NOW + timedelta(hours=2)

    store.pause(until)
    assert store.paused_until() == until

    store.resume()
    assert store.paused_until() is None


def test_events_and_why_are_persisted(store, policy) -> None:
    store.add_urge(make_urge())
    Engine(policy, store).tick(at=NOW)

    events = store.list_wake_events()
    last = store.last_wake_event()

    assert len(events) == 1
    assert last is not None
    assert last["decision"] == "MESSAGE_PLANNED"
    assert isinstance(last["reasons"], list)


def test_read_only_snapshot_does_not_create_file(tmp_path, policy) -> None:
    path = tmp_path / "missing.db"
    snapshot = __import__("ambientwill.storage", fromlist=["Storage"]).Storage.snapshot(path)

    result = Engine(policy, snapshot).tick(at=NOW, dry_run=True)

    assert result.decision.value == "SLEEP"
    assert not path.exists()
