import multiprocessing
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import make_desire, make_urge

from ambientwill.desires import DesireReviewer
from ambientwill.engine import Engine
from ambientwill.models import AgentPolicy, DesireProgress, QuietWindow
from ambientwill.storage import AlreadyRunningError, Storage, TickLock

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _hold_project_lock(path: str, ready, release) -> None:
    with TickLock(Path(path)):
        ready.set()
        release.wait(timeout=10)


def _progress_once(path: str, start, outcomes) -> None:
    start.wait(timeout=10)
    storage = Storage(path)
    progress = DesireProgress(
        id=f"progress-{os.getpid()}",
        desire_id="desire-1",
        recorded_at=datetime(2026, 2, 1, 12, 30, tzinfo=UTC),
        from_revision=1,
        to_revision=2,
        current_state="The checkpoint is in progress.",
        next_step="Finish the checkpoint.",
        gap=0.5,
        actionability=0.8,
        next_review_at=datetime(2026, 2, 1, 14, 0, tzinfo=UTC),
        status="open",
    )
    try:
        storage.record_desire_progress(progress)
    except (AlreadyRunningError, ValueError) as exc:
        outcomes.put(("blocked", exc.__class__.__name__))
    else:
        outcomes.put(("ok", progress.id))


def _review_policy() -> AgentPolicy:
    return AgentPolicy(
        timezone="UTC",
        quiet_hours=(QuietWindow("23:00", "07:00"),),
        daily_message_hard_limit=10,
        unanswered_limit=10,
        min_message_gap=timedelta(0),
        jitter_min_minutes=0,
        jitter_max_minutes=0,
        cooldown=timedelta(0),
        message_threshold=1.0,
        reflect_threshold=0.5,
    )


def _review_once(path: str, start, outcomes) -> None:
    start.wait(timeout=10)
    try:
        result = DesireReviewer(_review_policy(), Storage(path)).review(
            at=datetime(2026, 2, 1, 13, 0, tzinfo=UTC)
        )
    except AlreadyRunningError:
        outcomes.put("blocked")
    else:
        outcomes.put(result[0]["outcome"])


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


def test_concurrent_progress_allows_exactly_one_revision_update(store) -> None:
    store.add_desire(make_desire())
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_progress_once,
            args=(str(store.path), start, outcomes),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    observed = [outcomes.get(timeout=2) for _ in processes]
    assert sum(outcome[0] == "ok" for outcome in observed) == 1
    details = store.desire_details("desire-1")
    assert details["desire"]["revision"] == 2
    assert len(details["progress"]) == 1


def test_concurrent_review_never_duplicates_review_or_urge(store) -> None:
    store.add_desire(make_desire())
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_review_once,
            args=(str(store.path), start, outcomes),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    observed = [outcomes.get(timeout=2) for _ in processes]
    assert "URGE_CREATED" in observed
    assert len(store.desire_details("desire-1")["reviews"]) == 1
    assert len(store.valid_urges(datetime(2026, 2, 1, 13, 0, tzinfo=UTC))) == 1
