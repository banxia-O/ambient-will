from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from conftest import make_desire

from ambientwill.desires import DesireReviewer
from ambientwill.engine import Engine
from ambientwill.models import Decision, DesireProgress

CREATED = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
REVIEW_AT = CREATED + timedelta(hours=1)


def test_due_low_score_records_sleep_without_creating_urge(store, policy) -> None:
    store.add_desire(
        make_desire(
            importance=0.2,
            gap=0.2,
            confidence=0.2,
            actionability=0.2,
            interruption_cost=0.1,
        )
    )

    results = DesireReviewer(policy, store).review(at=REVIEW_AT)

    assert [item["outcome"] for item in results] == ["SLEEP"]
    assert results[0]["reasons"] == {
        "importance": 0.2,
        "gap": 0.2,
        "urgency": pytest.approx(0.04),
        "confidence": 0.2,
        "actionability": 0.2,
        "urge_confidence": pytest.approx(0.04),
        "interruption_cost": 0.1,
        "score": pytest.approx(-0.02),
        "reflect_threshold": policy.reflect_threshold,
        "expires_at": None,
        "expired": False,
    }
    assert store.valid_urges(REVIEW_AT) == []
    assert store.desire_details("desire-1")["reviews"][0]["outcome"] == "SLEEP"


def test_due_high_score_atomically_creates_mapped_urge_for_existing_engine(
    store, policy
) -> None:
    desire = make_desire()
    store.add_desire(desire)

    results = DesireReviewer(policy, store).review(at=REVIEW_AT)

    assert [item["outcome"] for item in results] == ["URGE_CREATED"]
    urge = store.valid_urges(REVIEW_AT)[0]
    assert urge.type == desire.urge_type
    assert urge.reason == desire.next_step
    assert urge.urgency == pytest.approx(desire.importance * desire.gap)
    assert urge.confidence == pytest.approx(desire.confidence * desire.actionability)
    assert urge.interruption_cost == desire.interruption_cost
    assert urge.cooldown_key == desire.cooldown_key
    assert urge.created_at == REVIEW_AT
    assert urge.expires_at == desire.expires_at

    decision = Engine(policy, store).tick(at=REVIEW_AT)
    assert decision.decision is Decision.REFLECT


def test_same_revision_review_is_an_explicit_idempotent_noop(store, policy) -> None:
    store.add_desire(make_desire())
    reviewer = DesireReviewer(policy, store)

    first = reviewer.review(at=REVIEW_AT)
    second = reviewer.review(at=REVIEW_AT + timedelta(minutes=1))

    assert first[0]["outcome"] == "URGE_CREATED"
    assert second[0]["outcome"] == "already_reviewed"
    details = store.desire_details("desire-1")
    assert len(details["reviews"]) == 1
    assert len(store.valid_urges(REVIEW_AT + timedelta(minutes=1))) == 1


def test_only_new_progress_revision_restores_review_eligibility(store, policy) -> None:
    store.add_desire(make_desire())
    reviewer = DesireReviewer(policy, store)
    reviewer.review(at=REVIEW_AT)
    progress = DesireProgress(
        id="progress-review-2",
        desire_id="desire-1",
        recorded_at=REVIEW_AT + timedelta(minutes=10),
        from_revision=1,
        to_revision=2,
        current_state="The next checkpoint is in progress.",
        next_step="Complete the second anonymous checkpoint.",
        gap=0.5,
        actionability=0.8,
        next_review_at=REVIEW_AT + timedelta(hours=1),
        status="open",
    )
    store.record_desire_progress(progress)

    results = reviewer.review(at=REVIEW_AT + timedelta(hours=1))

    assert results[0]["outcome"] == "URGE_CREATED"
    assert results[0]["revision"] == 2
    assert [
        item["revision"] for item in store.desire_details("desire-1")["reviews"]
    ] == [
        1,
        2,
    ]


def test_expired_due_desire_is_marked_and_audited_without_urge(store, policy) -> None:
    expires_at = CREATED + timedelta(minutes=30)
    store.add_desire(make_desire(expires_at=expires_at))

    results = DesireReviewer(policy, store).review(at=REVIEW_AT)

    assert results[0]["outcome"] == "EXPIRED"
    assert results[0]["reasons"]["expired"] is True
    assert results[0]["reasons"]["expires_at"] == expires_at.isoformat()
    assert store.get_desire("desire-1").status == "expired"
    assert store.valid_urges(REVIEW_AT) == []


def test_review_batch_failure_rolls_back_reviews_urges_and_expiry(
    store, policy
) -> None:
    store.add_desire(make_desire(desire_id="first"))
    store.add_desire(
        make_desire(
            desire_id="second",
            expires_at=CREATED + timedelta(minutes=30),
        )
    )

    with pytest.raises(sqlite3.OperationalError, match="injected"):
        DesireReviewer(policy, store).review(at=REVIEW_AT, fail_after_review=True)

    assert store.desire_details("first")["reviews"] == []
    assert store.desire_details("second")["reviews"] == []
    assert store.get_desire("second").status == "open"
    assert store.valid_urges(REVIEW_AT) == []


def test_review_uses_stable_order_and_skips_future_or_blocked_desires(
    store, policy
) -> None:
    store.add_desire(make_desire(desire_id="b-due"))
    store.add_desire(make_desire(desire_id="a-due"))
    store.add_desire(
        make_desire(
            desire_id="future",
            next_review_at=REVIEW_AT + timedelta(seconds=1),
        )
    )
    store.add_desire(
        make_desire(desire_id="blocked", status="blocked", next_review_at=None)
    )

    results = DesireReviewer(policy, store).review(at=REVIEW_AT, dry_run=True)

    assert [item["desire_id"] for item in results] == ["a-due", "b-due"]
    assert store.desire_details("a-due")["reviews"] == []


def test_dry_run_replay_is_stable_and_does_not_spend_revision(store, policy) -> None:
    store.add_desire(make_desire())
    reviewer = DesireReviewer(policy, store)

    first = reviewer.review(at=REVIEW_AT, dry_run=True)
    second = reviewer.review(at=REVIEW_AT, dry_run=True)

    assert second == first
    assert store.get_desire("desire-1").revision == 1
    assert store.desire_details("desire-1")["reviews"] == []
    assert store.valid_urges(REVIEW_AT) == []
