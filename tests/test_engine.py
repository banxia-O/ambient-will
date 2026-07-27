from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

from conftest import make_urge

from ambientwill.engine import Engine
from ambientwill.models import Decision

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_sleep_reflect_and_message_decisions(store, policy) -> None:
    engine = Engine(policy, store)

    assert engine.tick(at=NOW, dry_run=True).decision is Decision.SLEEP

    store.add_urge(
        make_urge(urge_id="reflect", urgency=0.3, confidence=0.4, interruption_cost=0.1)
    )
    reflected = engine.tick(at=NOW, dry_run=True)
    assert reflected.decision is Decision.REFLECT

    store.set_urge_status("reflect", "closed")
    store.add_urge(make_urge(urge_id="message"))
    messaged = engine.tick(at=NOW, dry_run=True)
    assert messaged.decision is Decision.MESSAGE_PLANNED


def test_hard_gate_wins_over_score(store, policy) -> None:
    store.add_urge(make_urge())
    engine = Engine(replace(policy, quiet_hours=()), store)
    store.pause(NOW.replace(hour=13))

    result = engine.tick(at=NOW, dry_run=True)

    assert result.decision is Decision.SLEEP
    assert result.blocked_by == "paused"


def test_dry_run_does_not_write_wake_or_outbox(store, policy) -> None:
    store.add_urge(make_urge())

    result = Engine(policy, store).tick(at=NOW, dry_run=True)

    assert result.decision is Decision.MESSAGE_PLANNED
    assert store.count_wake_events() == 0
    assert store.count_outbox() == 0


def test_normal_tick_commits_wake_and_outbox_together(store, policy) -> None:
    store.add_urge(make_urge())

    result = Engine(policy, store).tick(at=NOW)

    assert result.decision is Decision.MESSAGE_PLANNED
    assert store.count_wake_events() == 1
    assert store.count_outbox() == 1


def test_transaction_failure_leaves_no_partial_outbox(store, policy) -> None:
    store.add_urge(make_urge())
    engine = Engine(policy, store)

    result = engine.tick(at=NOW, fail_after_wake=True)

    assert result.error == "transaction_failed"
    assert result.decision is Decision.SLEEP
    assert store.count_wake_events() == 0
    assert store.count_outbox() == 0


def test_same_idempotency_key_does_not_duplicate_outbox(store, policy) -> None:
    store.add_urge(make_urge())
    engine = Engine(policy, store)

    engine.tick(at=NOW)
    engine.tick(at=NOW)

    assert store.count_outbox() == 1
    assert store.count_wake_events() == 1


def test_equivalent_offset_timestamps_are_one_idempotent_tick(store, policy) -> None:
    store.add_urge(make_urge(urgency=0.4, confidence=0.4, interruption_cost=0.1))
    engine = Engine(policy, store)

    first = engine.tick(at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    second = engine.tick(
        at=datetime(2026, 1, 1, 13, 0, tzinfo=timezone(timedelta(hours=1)))
    )

    assert first.decision is Decision.REFLECT
    assert second.decision is Decision.SLEEP
    assert second.blocked_by == "idempotency_key"
    assert store.count_wake_events() == 1
