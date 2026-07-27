from dataclasses import replace
from datetime import datetime, timedelta, timezone

from ambientwill.engine import Engine
from ambientwill.models import QuietWindow

from conftest import make_urge


NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _message(store, policy, urge_id: str, at: datetime, cooldown_key: str = "key") -> None:
    store.add_urge(make_urge(urge_id=urge_id, created_at=at - timedelta(minutes=1), cooldown_key=cooldown_key))
    result = Engine(policy, store).tick(at=at)
    assert result.decision.value == "MESSAGE_PLANNED"
    store.set_urge_status(urge_id, "closed")


def test_quiet_hours_block_message(store, policy) -> None:
    store.add_urge(make_urge())
    local_quiet = datetime(2026, 1, 1, 23, 30, tzinfo=timezone(timedelta(hours=8)))

    result = Engine(policy, store).tick(at=local_quiet, dry_run=True)

    assert result.blocked_by == "quiet_hours"


def test_daily_hard_limit_uses_local_natural_day(store, policy) -> None:
    limited = replace(policy, quiet_hours=(), daily_message_hard_limit=1)
    _message(store, limited, "first", NOW)
    store.add_urge(make_urge(urge_id="second", created_at=NOW))

    result = Engine(limited, store).tick(at=NOW + timedelta(hours=1), dry_run=True)

    assert result.blocked_by == "daily_message_hard_limit"
    assert Engine(limited, store).tick(
        at=NOW + timedelta(days=1), dry_run=True
    ).blocked_by != "daily_message_hard_limit"


def test_unanswered_limit(store, policy) -> None:
    limited = replace(policy, quiet_hours=(), unanswered_limit=1)
    _message(store, limited, "first", NOW)
    store.add_urge(make_urge(urge_id="second", created_at=NOW))

    result = Engine(limited, store).tick(at=NOW + timedelta(hours=1), dry_run=True)

    assert result.blocked_by == "unanswered_limit"


def test_minimum_message_gap(store, policy) -> None:
    limited = replace(policy, quiet_hours=(), min_message_gap=timedelta(hours=2))
    _message(store, limited, "first", NOW)
    store.add_urge(make_urge(urge_id="second", created_at=NOW))

    result = Engine(limited, store).tick(at=NOW + timedelta(minutes=30), dry_run=True)

    assert result.blocked_by == "min_message_gap"


def test_expired_urge_is_ignored(store, policy) -> None:
    store.add_urge(make_urge(expires_at=NOW - timedelta(seconds=1)))

    result = Engine(replace(policy, quiet_hours=()), store).tick(at=NOW, dry_run=True)

    assert result.blocked_by == "no_valid_urge"


def test_cooldown_key_blocks_repeated_intent(store, policy) -> None:
    limited = replace(policy, quiet_hours=(), cooldown=timedelta(hours=2))
    _message(store, limited, "first", NOW, cooldown_key="same")
    store.add_urge(
        make_urge(
            urge_id="second",
            created_at=NOW + timedelta(minutes=1),
            cooldown_key="same",
        )
    )

    result = Engine(limited, store).tick(at=NOW + timedelta(hours=1), dry_run=True)

    assert result.blocked_by == "cooldown_key"


def test_score_threshold_is_explained(store, policy) -> None:
    store.add_urge(
        make_urge(urgency=0.1, confidence=0.1, interruption_cost=0.1)
    )

    result = Engine(replace(policy, quiet_hours=()), store).tick(at=NOW, dry_run=True)

    assert result.blocked_by == "below_reflect_threshold"
    assert any(reason["gate"] == "score_threshold" for reason in result.reasons)
