from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ambientwill.models import AgentPolicy, Desire, QuietWindow, Urge
from ambientwill.storage import Storage


@pytest.fixture
def policy() -> AgentPolicy:
    return AgentPolicy(
        timezone="Asia/Singapore",
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


@pytest.fixture
def store(tmp_path: Path) -> Storage:
    storage = Storage(tmp_path / "ambientwill.db")
    storage.initialize()
    return storage


def make_urge(
    *,
    urge_id: str = "urge-1",
    urgency: float = 0.7,
    confidence: float = 0.8,
    interruption_cost: float = 0.2,
    cooldown_key: str = "follow-up",
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> Urge:
    created = created_at or datetime(2026, 1, 1, 1, 0, tzinfo=UTC)
    return Urge(
        id=urge_id,
        type="follow_up",
        reason="An anonymous follow-up is due.",
        urgency=urgency,
        confidence=confidence,
        interruption_cost=interruption_cost,
        cooldown_key=cooldown_key,
        created_at=created,
        expires_at=expires_at,
        status="open",
    )


def make_desire(
    *,
    desire_id: str = "desire-1",
    created_at: datetime | None = None,
    next_review_at: datetime | None = None,
    expires_at: datetime | None = None,
    importance: float = 0.8,
    gap: float = 0.7,
    confidence: float = 0.6,
    actionability: float = 0.9,
    interruption_cost: float = 0.2,
    status: str = "open",
    revision: int = 1,
) -> Desire:
    created = created_at or datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
    review_at = (
        next_review_at if next_review_at is not None else created + timedelta(hours=1)
    )
    return Desire(
        id=desire_id,
        source="project_goal",
        urge_type="follow_up",
        reason="Advance an anonymous project goal.",
        target_state="The next checkpoint is complete.",
        current_state="The checkpoint is pending.",
        next_step="Complete the next anonymous checkpoint.",
        importance=importance,
        gap=gap,
        confidence=confidence,
        actionability=actionability,
        interruption_cost=interruption_cost,
        cooldown_key=f"project-checkpoint-{desire_id}",
        created_at=created,
        next_review_at=review_at if status == "open" else next_review_at,
        expires_at=expires_at,
        status=status,
        revision=revision,
    )
