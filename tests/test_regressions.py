from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest
from conftest import make_urge

from ambientwill.engine import Engine
from ambientwill.models import Decision, QuietWindow, ValidationError

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
SGT = timezone(timedelta(hours=8))


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("23:00", "23:00:00"),
        ("7:00", "08:00"),
        ("23:00+08:00", "07:00"),
        ("23:00:00.000000", "07:00"),
    ],
)
def test_quiet_windows_require_strict_hhmm(start: str, end: str) -> None:
    with pytest.raises(ValidationError):
        QuietWindow(start, end)


def test_jitter_that_lands_in_quiet_hours_fails_closed(store, policy) -> None:
    local_policy = replace(
        policy,
        timezone="Asia/Singapore",
        quiet_hours=(QuietWindow("23:00", "07:00"),),
        jitter_min_minutes=35,
        jitter_max_minutes=35,
    )
    at = datetime(2026, 1, 1, 22, 50, tzinfo=SGT)
    store.add_urge(make_urge(created_at=at - timedelta(minutes=1)))

    result = Engine(local_policy, store).tick(at=at)

    assert result.decision is Decision.SLEEP
    assert result.blocked_by == "delayed_into_quiet_hours"
    assert result.outbox_event_id is None
    assert store.count_outbox() == 0
    assert store.last_wake_event()["decision"] == "SLEEP"


def test_dry_run_applies_delayed_quiet_gate(store, policy) -> None:
    local_policy = replace(
        policy,
        timezone="Asia/Singapore",
        quiet_hours=(QuietWindow("23:00", "07:00"),),
        jitter_min_minutes=35,
        jitter_max_minutes=35,
    )
    at = datetime(2026, 1, 1, 22, 50, tzinfo=SGT)
    store.add_urge(make_urge(created_at=at - timedelta(minutes=1)))

    result = Engine(local_policy, store).tick(at=at, dry_run=True)

    assert result.decision is Decision.SLEEP
    assert result.blocked_by == "delayed_into_quiet_hours"
    assert store.count_wake_events() == 0
    assert store.count_outbox() == 0


@pytest.mark.parametrize(
    ("at", "jitter", "blocked"),
    [
        (datetime(2026, 1, 1, 22, 55, tzinfo=SGT), 5, True),
        (datetime(2026, 1, 2, 6, 55, tzinfo=SGT), 5, False),
    ],
)
def test_delayed_quiet_boundaries(store, policy, at, jitter, blocked) -> None:
    local_policy = replace(
        policy,
        timezone="Asia/Singapore",
        quiet_hours=(QuietWindow("23:00", "07:00"),),
        jitter_min_minutes=jitter,
        jitter_max_minutes=jitter,
    )
    store.add_urge(make_urge(created_at=at - timedelta(minutes=1)))

    result = Engine(local_policy, store).tick(at=at)

    assert (result.blocked_by == "delayed_into_quiet_hours") is blocked


def test_idempotent_replay_is_explicit_safe_noop(store, policy) -> None:
    local_policy = replace(policy, quiet_hours=(), cooldown=timedelta(0))
    store.add_urge(make_urge())
    engine = Engine(local_policy, store)

    first = engine.tick(at=NOW)
    store.set_urge_status("urge-1", "open")
    second = engine.tick(at=NOW + timedelta(microseconds=1))

    assert first.decision is Decision.MESSAGE_PLANNED
    assert second.decision is Decision.SLEEP
    assert second.blocked_by == "idempotency_key"
    assert second.error is None
    assert second.wake_event_id is None
    assert second.outbox_event_id is None
    assert store.count_wake_events() == 1
    assert store.count_outbox() == 1


def test_successful_plan_atomically_closes_urge(store, policy) -> None:
    store.add_urge(make_urge())

    result = Engine(policy, store).tick(at=NOW)

    assert result.decision is Decision.MESSAGE_PLANNED
    assert store.get_urge_status("urge-1") == "closed"


def test_transaction_failure_keeps_urge_open(store, policy) -> None:
    store.add_urge(make_urge())

    result = Engine(policy, store).tick(at=NOW, fail_after_wake=True)

    assert result.decision is Decision.SLEEP
    assert store.get_urge_status("urge-1") == "open"


def test_single_urge_is_not_planned_again(store, policy) -> None:
    local_policy = replace(policy, quiet_hours=(), cooldown=timedelta(0))
    store.add_urge(make_urge())
    engine = Engine(local_policy, store)

    assert engine.tick(at=NOW).decision is Decision.MESSAGE_PLANNED
    second = engine.tick(at=NOW + timedelta(hours=1))

    assert second.decision is Decision.SLEEP
    assert second.blocked_by == "no_valid_urge"
    assert store.count_outbox() == 1


def test_budget_uses_delayed_until_local_day(store, policy) -> None:
    local_policy = replace(
        policy,
        timezone="Asia/Singapore",
        quiet_hours=(),
        jitter_min_minutes=20,
        jitter_max_minutes=20,
    )
    at = datetime(2026, 1, 1, 23, 50, tzinfo=SGT)
    store.add_urge(make_urge(created_at=at - timedelta(minutes=1)))
    Engine(local_policy, store).tick(at=at)

    assert store.daily_message_count(at, local_policy.zone) == 0
    assert store.daily_message_count(at + timedelta(days=1), local_policy.zone) == 1


def test_full_current_day_does_not_block_plan_for_next_local_day(store, policy) -> None:
    local_policy = replace(
        policy,
        timezone="Asia/Singapore",
        quiet_hours=(),
        daily_message_hard_limit=1,
        unanswered_limit=10,
        min_message_gap=timedelta(0),
        cooldown=timedelta(0),
        jitter_min_minutes=0,
        jitter_max_minutes=0,
    )
    first_at = datetime(2026, 1, 1, 22, 0, tzinfo=SGT)
    store.add_urge(
        make_urge(urge_id="first", created_at=first_at - timedelta(minutes=1))
    )
    assert (
        Engine(local_policy, store).tick(at=first_at).decision
        is Decision.MESSAGE_PLANNED
    )

    second_at = datetime(2026, 1, 1, 23, 50, tzinfo=SGT)
    next_day_policy = replace(
        local_policy, jitter_min_minutes=20, jitter_max_minutes=20
    )
    store.add_urge(
        make_urge(
            urge_id="second",
            cooldown_key="second",
            created_at=second_at - timedelta(minutes=1),
        )
    )

    result = Engine(next_day_policy, store).tick(at=second_at, dry_run=True)

    assert result.decision is Decision.MESSAGE_PLANNED
    assert result.delayed_until == datetime(2026, 1, 2, 0, 10, tzinfo=SGT)


def test_future_delayed_plan_is_not_yet_unanswered(store, policy) -> None:
    local_policy = replace(
        policy,
        quiet_hours=(),
        jitter_min_minutes=30,
        jitter_max_minutes=30,
    )
    store.add_urge(make_urge())
    Engine(local_policy, store).tick(at=NOW)

    assert store.unanswered_count(NOW + timedelta(minutes=29)) == 0
    assert store.unanswered_count(NOW + timedelta(minutes=30)) == 1


def test_cooldown_does_not_starve_eligible_lower_score_urge(store, policy) -> None:
    local_policy = replace(policy, quiet_hours=(), cooldown=timedelta(hours=3))
    store.add_urge(
        make_urge(urge_id="old", urgency=0.9, confidence=0.9, cooldown_key="busy")
    )
    Engine(local_policy, store).tick(at=NOW)
    store.add_urge(
        make_urge(
            urge_id="high-blocked",
            urgency=0.9,
            confidence=0.9,
            cooldown_key="busy",
            created_at=NOW + timedelta(minutes=1),
        )
    )
    store.add_urge(
        make_urge(
            urge_id="lower-free",
            urgency=0.6,
            confidence=0.6,
            cooldown_key="free",
            created_at=NOW + timedelta(minutes=1),
        )
    )

    result = Engine(local_policy, store).tick(at=NOW + timedelta(hours=1), dry_run=True)

    assert result.decision is Decision.MESSAGE_PLANNED
    assert result.selected_urge_id == "lower-free"


def test_delayed_plan_explicitly_rechecks_minimum_gap(
    store, policy, monkeypatch
) -> None:
    store.add_urge(make_urge())
    observations = iter([None, NOW + timedelta(minutes=30)])
    monkeypatch.setattr(store, "last_message_time", lambda: next(observations))
    delayed_policy = replace(
        policy,
        quiet_hours=(),
        min_message_gap=timedelta(minutes=60),
        jitter_min_minutes=60,
        jitter_max_minutes=60,
    )

    result = Engine(delayed_policy, store).tick(at=NOW, dry_run=True)

    assert result.decision is Decision.SLEEP
    assert result.blocked_by == "delayed_minimum_message_gap"
