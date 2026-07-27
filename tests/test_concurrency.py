from datetime import datetime, timezone

from ambientwill.engine import Engine
from ambientwill.storage import TickLock

from conftest import make_urge


NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_second_tick_returns_already_running(store, policy) -> None:
    store.add_urge(make_urge())
    engine = Engine(policy, store)

    with TickLock(store.lock_path):
        result = engine.tick(at=NOW)

    assert result.already_running is True
    assert result.error == "already_running"
    assert store.count_wake_events() == 0
    assert store.count_outbox() == 0
