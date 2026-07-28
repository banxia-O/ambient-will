from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from conftest import make_desire, make_urge

from ambientwill.desires import DesireReviewer
from ambientwill.engine import Engine
from ambientwill.models import Decision, DesireProgress

CREATED = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
REVIEW_AT = CREATED + timedelta(hours=1)


def progress_for(
    *,
    status: str,
    from_revision: int = 1,
    next_review_at: datetime | None = None,
) -> DesireProgress:
    return DesireProgress(
        id=f"progress-{status}-{from_revision + 1}",
        desire_id="desire-1",
        recorded_at=REVIEW_AT + timedelta(minutes=10),
        from_revision=from_revision,
        to_revision=from_revision + 1,
        current_state=f"The anonymous checkpoint is {status}.",
        next_step="Continue the anonymous checkpoint.",
        gap=0.7,
        actionability=0.9,
        next_review_at=next_review_at,
        status=status,
    )


def linked_urges(store) -> list[sqlite3.Row]:
    with store.connect() as connection:
        return connection.execute(
            """
            SELECT links.desire_id, links.desire_revision, urges.id, urges.status
            FROM desire_urge_links AS links
            JOIN urges ON urges.id = links.urge_id
            ORDER BY links.desire_revision
            """
        ).fetchall()


@pytest.mark.parametrize("status", ["open", "blocked", "satisfied", "abandoned"])
def test_any_progress_expires_old_revision_urge_before_engine_can_select_it(
    store, policy, status: str
) -> None:
    store.add_desire(make_desire())
    DesireReviewer(policy, store).review(at=REVIEW_AT)
    old_urge_id = store.valid_urges(REVIEW_AT)[0].id
    next_review_at = REVIEW_AT + timedelta(hours=1) if status == "open" else None

    store.record_desire_progress(
        progress_for(status=status, next_review_at=next_review_at)
    )

    assert store.get_urge_status(old_urge_id) == "expired"
    result = Engine(policy, store).tick(
        at=REVIEW_AT + timedelta(minutes=11), dry_run=True
    )
    assert result.decision is Decision.SLEEP
    assert result.selected_urge_id is None
    assert result.blocked_by == "no_valid_urge"


@pytest.mark.parametrize(
    "failure_flag", ["fail_after_history", "fail_after_urge_expiry"]
)
def test_progress_failure_rolls_back_history_projection_and_urge_expiry(
    store, policy, failure_flag: str
) -> None:
    store.add_desire(make_desire())
    DesireReviewer(policy, store).review(at=REVIEW_AT)
    old_urge_id = store.valid_urges(REVIEW_AT)[0].id
    before = store.desire_details("desire-1")

    with pytest.raises(sqlite3.OperationalError, match="injected"):
        store.record_desire_progress(
            progress_for(status="open", next_review_at=REVIEW_AT + timedelta(hours=1)),
            **{failure_flag: True},
        )

    assert store.get_urge_status(old_urge_id) == "open"
    assert store.desire_details("desire-1") == before


def test_new_revision_review_creates_a_new_correctly_linked_urge(store, policy) -> None:
    store.add_desire(make_desire())
    reviewer = DesireReviewer(policy, store)
    reviewer.review(at=REVIEW_AT)
    reviewer_due = REVIEW_AT + timedelta(hours=1)
    store.record_desire_progress(
        progress_for(status="open", next_review_at=reviewer_due)
    )

    result = reviewer.review(at=reviewer_due)

    assert result[0]["revision"] == 2
    assert result[0]["outcome"] == "URGE_CREATED"
    links = linked_urges(store)
    assert [
        (row["desire_id"], row["desire_revision"], row["status"]) for row in links
    ] == [
        ("desire-1", 1, "expired"),
        ("desire-1", 2, "open"),
    ]


def test_manual_urge_is_not_expired_by_desire_progress(store, policy) -> None:
    store.add_desire(make_desire())
    reviewer = DesireReviewer(policy, store)
    reviewer.review(at=REVIEW_AT)
    store.add_urge(
        make_urge(
            urge_id="manual-urge",
            created_at=REVIEW_AT,
            cooldown_key="manual",
        )
    )

    store.record_desire_progress(
        progress_for(status="open", next_review_at=REVIEW_AT + timedelta(hours=1))
    )

    assert store.get_urge_status("manual-urge") == "open"
    result = Engine(policy, store).tick(
        at=REVIEW_AT + timedelta(minutes=11), dry_run=True
    )
    assert result.selected_urge_id == "manual-urge"
    assert result.decision is Decision.MESSAGE_PLANNED
