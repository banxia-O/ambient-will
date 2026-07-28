from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from ambientwill import __version__
from ambientwill.models import (
    Desire,
    DesireProgress,
    DesireReview,
    ValidationError,
)

NOW = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)


def test_v02_package_version() -> None:
    assert __version__ == "0.2.0"


def make_desire(**changes) -> Desire:
    values = {
        "id": "desire-1",
        "source": "project_goal",
        "urge_type": "follow_up",
        "reason": "Advance an anonymous project goal.",
        "target_state": "The next checkpoint is complete.",
        "current_state": "The checkpoint is pending.",
        "next_step": "Complete the next anonymous checkpoint.",
        "importance": 0.8,
        "gap": 0.7,
        "confidence": 0.6,
        "actionability": 0.9,
        "interruption_cost": 0.2,
        "cooldown_key": "project-checkpoint",
        "created_at": NOW,
        "next_review_at": NOW + timedelta(hours=1),
        "expires_at": NOW + timedelta(days=7),
        "status": "open",
        "revision": 1,
    }
    values.update(changes)
    return Desire(**values)


def test_desire_accepts_valid_input_and_normalizes_text() -> None:
    desire = make_desire(source="  project_goal  ")

    assert desire.source == "project_goal"
    assert desire.revision == 1
    assert desire.to_dict()["next_review_at"] == "2026-02-01T13:00:00+00:00"


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -0.1, 1.1])
def test_desire_rejects_invalid_unit_interval_values(value: object) -> None:
    with pytest.raises(ValidationError):
        make_desire(gap=value)


def test_open_desire_requires_next_step_and_review_time() -> None:
    with pytest.raises(ValidationError, match="next_review_at"):
        make_desire(next_review_at=None)
    with pytest.raises(ValidationError, match="next_step"):
        make_desire(next_step="   ")


def test_open_desire_review_time_cannot_precede_creation() -> None:
    with pytest.raises(ValidationError, match="before created_at"):
        make_desire(next_review_at=NOW - timedelta(microseconds=1))

    at_creation = make_desire(next_review_at=NOW)
    equivalent_offset = make_desire(
        next_review_at=datetime(2026, 2, 1, 13, 0, tzinfo=timezone(timedelta(hours=1)))
    )

    assert at_creation.next_review_at == NOW
    assert equivalent_offset.next_review_at == NOW


def test_desire_rejects_naive_or_invalid_times_and_revision() -> None:
    with pytest.raises(ValidationError, match="created_at"):
        make_desire(created_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValidationError, match="expires_at"):
        make_desire(expires_at=NOW)
    with pytest.raises(ValidationError, match="revision"):
        make_desire(revision=True)
    with pytest.raises(ValidationError, match="revision"):
        make_desire(revision=0)


def test_progress_requires_exact_revision_increment_and_open_review_time() -> None:
    progress = DesireProgress(
        id="progress-1",
        desire_id="desire-1",
        recorded_at=NOW + timedelta(minutes=30),
        from_revision=1,
        to_revision=2,
        current_state="Checkpoint started.",
        next_step="Finish the checkpoint.",
        gap=0.5,
        actionability=0.8,
        next_review_at=NOW + timedelta(hours=2),
        status="open",
        note="  Anonymous progress note.  ",
    )

    assert progress.note == "Anonymous progress note."
    assert progress.to_revision == 2
    with pytest.raises(ValidationError, match="exactly one"):
        replace(progress, to_revision=3)
    with pytest.raises(ValidationError, match="next_review_at"):
        replace(progress, next_review_at=None)


def test_open_progress_review_time_cannot_precede_recording() -> None:
    recorded_at = NOW + timedelta(minutes=30)
    progress = DesireProgress(
        id="progress-time",
        desire_id="desire-1",
        recorded_at=recorded_at,
        from_revision=1,
        to_revision=2,
        current_state="Checkpoint started.",
        next_step="Finish the checkpoint.",
        gap=0.5,
        actionability=0.8,
        next_review_at=recorded_at,
        status="open",
    )

    assert progress.next_review_at == recorded_at
    assert (
        replace(
            progress,
            next_review_at=datetime(
                2026, 2, 1, 13, 30, tzinfo=timezone(timedelta(hours=1))
            ),
        ).next_review_at
        == recorded_at
    )
    with pytest.raises(ValidationError, match="before recorded_at"):
        replace(progress, next_review_at=recorded_at - timedelta(microseconds=1))


def test_review_requires_consistent_outcome_and_urge_link() -> None:
    review = DesireReview(
        id="review-1",
        desire_id="desire-1",
        revision=1,
        evaluated_at=NOW + timedelta(hours=1),
        score=0.9,
        outcome="URGE_CREATED",
        urge_id="urge-1",
        reasons={"urgency": 0.5, "urge_confidence": 0.6, "score": 0.9},
    )

    assert review.to_dict()["outcome"] == "URGE_CREATED"
    with pytest.raises(ValidationError, match="urge_id"):
        replace(review, urge_id=None)
    with pytest.raises(ValidationError, match="urge_id"):
        replace(review, outcome="SLEEP")
