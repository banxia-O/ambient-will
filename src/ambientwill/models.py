from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ValidationError(ValueError):
    """Raised when local input cannot form a valid domain object."""


def _validate_unit_interval(name: str, value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValidationError(f"{name} must be between 0.0 and 1.0")


def parse_clock(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"invalid clock time: {value!r}") from exc
    if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
        raise ValidationError("quiet-hour clocks must use local HH:MM values")
    return parsed


@dataclass(frozen=True)
class QuietWindow:
    start: str
    end: str

    def __post_init__(self) -> None:
        parse_clock(self.start)
        parse_clock(self.end)
        if self.start == self.end:
            raise ValidationError("quiet-hour start and end cannot be equal")

    def to_dict(self) -> dict[str, str]:
        return {"start": self.start, "end": self.end}


@dataclass(frozen=True)
class AgentPolicy:
    timezone: str
    quiet_hours: tuple[QuietWindow, ...]
    daily_message_hard_limit: int
    unanswered_limit: int
    min_message_gap: timedelta
    jitter_min_minutes: int
    jitter_max_minutes: int
    cooldown: timedelta
    message_threshold: float
    reflect_threshold: float

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValidationError(f"unknown timezone: {self.timezone}") from exc
        if self.daily_message_hard_limit < 0:
            raise ValidationError("daily_message_hard_limit must be non-negative")
        if self.unanswered_limit < 0:
            raise ValidationError("unanswered_limit must be non-negative")
        if self.min_message_gap < timedelta(0):
            raise ValidationError("min_message_gap cannot be negative")
        if self.cooldown < timedelta(0):
            raise ValidationError("cooldown cannot be negative")
        if self.jitter_min_minutes < 0:
            raise ValidationError("jitter_min_minutes cannot be negative")
        if self.jitter_max_minutes < self.jitter_min_minutes:
            raise ValidationError(
                "jitter_max_minutes must be at least jitter_min_minutes"
            )
        if self.reflect_threshold > self.message_threshold:
            raise ValidationError(
                "reflect_threshold cannot exceed message_threshold"
            )

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timezone": self.timezone,
            "quiet_hours": [window.to_dict() for window in self.quiet_hours],
            "daily_message_hard_limit": self.daily_message_hard_limit,
            "unanswered_limit": self.unanswered_limit,
            "min_message_gap_minutes": int(self.min_message_gap.total_seconds() / 60),
            "jitter_min_minutes": self.jitter_min_minutes,
            "jitter_max_minutes": self.jitter_max_minutes,
            "cooldown_minutes": int(self.cooldown.total_seconds() / 60),
            "message_threshold": self.message_threshold,
            "reflect_threshold": self.reflect_threshold,
        }


@dataclass(frozen=True)
class Urge:
    id: str
    type: str
    reason: str
    urgency: float
    confidence: float
    interruption_cost: float
    cooldown_key: str
    created_at: datetime
    expires_at: datetime | None
    status: str = "open"

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValidationError("urge id cannot be empty")
        if not self.type.strip():
            raise ValidationError("urge type cannot be empty")
        if not self.reason.strip():
            raise ValidationError("urge reason cannot be empty")
        if not self.cooldown_key.strip():
            raise ValidationError("cooldown_key cannot be empty")
        _validate_unit_interval("urgency", self.urgency)
        _validate_unit_interval("confidence", self.confidence)
        _validate_unit_interval("interruption_cost", self.interruption_cost)
        if self.created_at.tzinfo is None:
            raise ValidationError("created_at must include a timezone")
        if self.expires_at is not None:
            if self.expires_at.tzinfo is None:
                raise ValidationError("expires_at must include a timezone")
            if self.expires_at <= self.created_at:
                raise ValidationError("expires_at must be later than created_at")
        if self.status not in {"open", "closed", "expired"}:
            raise ValidationError(f"unsupported urge status: {self.status}")

    @property
    def score(self) -> float:
        return self.urgency + self.confidence - self.interruption_cost

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "reason": self.reason,
            "urgency": self.urgency,
            "confidence": self.confidence,
            "interruption_cost": self.interruption_cost,
            "cooldown_key": self.cooldown_key,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "status": self.status,
            "score": self.score,
        }


class Decision(str, Enum):
    SLEEP = "SLEEP"
    REFLECT = "REFLECT"
    MESSAGE_PLANNED = "MESSAGE_PLANNED"


@dataclass(frozen=True)
class WakeEvent:
    id: str
    trigger: str
    evaluated_at: datetime
    selected_urge_id: str | None
    decision: Decision
    reasons: list[dict[str, Any]]
    created_at: datetime


@dataclass(frozen=True)
class OutboxEvent:
    event_id: str
    wake_event_id: str
    message_preview: str
    idempotency_key: str
    planned_at: datetime
    delayed_until: datetime
    state: str
    cooldown_key: str

    def __post_init__(self) -> None:
        if self.state not in {"planned", "cancelled", "expired"}:
            raise ValidationError(f"unsupported shadow outbox state: {self.state}")


@dataclass
class TickResult:
    decision: Decision
    reasons: list[dict[str, Any]] = field(default_factory=list)
    selected_urge_id: str | None = None
    score: float | None = None
    blocked_by: str | None = None
    wake_event_id: str | None = None
    outbox_event_id: str | None = None
    delayed_until: datetime | None = None
    dry_run: bool = False
    already_running: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reasons": self.reasons,
            "selected_urge_id": self.selected_urge_id,
            "score": self.score,
            "blocked_by": self.blocked_by,
            "wake_event_id": self.wake_event_id,
            "outbox_event_id": self.outbox_event_id,
            "delayed_until": (
                self.delayed_until.isoformat() if self.delayed_until else None
            ),
            "dry_run": self.dry_run,
            "already_running": self.already_running,
            "error": self.error,
        }
