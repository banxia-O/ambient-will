import multiprocessing
from datetime import UTC, datetime
from pathlib import Path

from conftest import make_urge

from ambientwill.engine import Engine
from ambientwill.storage import AlreadyRunningError, TickLock

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _hold_project_lock(path: str, ready, release) -> None:
    with TickLock(Path(path)):
        ready.set()
        release.wait(timeout=10)


def test_second_tick_returns_already_running(store, policy) -> None:
    store.add_urge(make_urge())
    engine = Engine(policy, store)

    with TickLock(store.lock_path):
        result = engine.tick(at=NOW)

    assert result.already_running is True
    assert result.blocked_by == "single_instance_lock"
    assert store.count_wake_events() == 0
    assert store.count_outbox() == 0


def test_project_lock_is_enforced_across_processes(store) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_project_lock,
        args=(str(store.lock_path), ready, release),
    )
    process.start()
    try:
        assert ready.wait(timeout=10)
        try:
            with TickLock(store.lock_path):
                pass
        except AlreadyRunningError:
            pass
        else:
            raise AssertionError("a second process acquired the project lock")
    finally:
        release.set()
        process.join(timeout=10)
        if process.is_alive():
            process.kill()
    assert process.exitcode == 0
