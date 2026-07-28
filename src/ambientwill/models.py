from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ValidationError(ValueError):
    """Raised when local input cannot form a valid domain object."""


def _validate_unit_interval(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number")
    if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
        raise ValidationError(f"{name} must be between 0.0 and 1.0")


def _normalize_text(instance: object, name: str) -> str:
    value = getattr(instance, name)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} cannot be empty")
    normalized = value.strip()
    object.__setattr__(instance, name, normalized)
    return normalized


def _validate_aware_datetime(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValidationError(f"{name} must include a timezone")


def parse_clock(value: str) -> time:
    if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) is None:
        raise ValidationError("quiet-hour clocks must use strict local HH:MM values")
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
        start = parse_clock(self.start)
        end = parse_clock(self.end)
        if start == end:
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
        if type(self.daily_message_hard_limit) is not int:
            raise ValidationError("daily_message_hard_limit must be an integer")
        if type(self.unanswered_limit) is not int:
            raise ValidationError("unanswered_limit must be an integer")
        if type(self.jitter_min_minutes) is not int:
            raise ValidationError("jitter_min_minutes must be an integer")
        if type(self.jitter_max_minutes) is not int:
            raise ValidationError("jitter_max_minutes must be an integer")
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
        for name, value in (
            ("message_threshold", self.message_threshold),
            ("reflect_threshold", self.reflect_threshold),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError(f"{name} must be a number")
            if not math.isfinite(float(value)) or not -1.0 <= float(value) <= 2.0:
                raise ValidationError(f"{name} must be finite and between -1.0 and 2.0")
        if self.reflect_threshold > self.message_threshold:
            raise ValidationError("reflect_threshold cannot exceed message_threshold")

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


DESIRE_STATUSES = {"open", "blocked", "satisfied", "abandoned", "expired"}
TERMINAL_DESIRE_STATUSES = {"satisfied", "abandoned", "expired"}


@dataclass(frozen=True)
class Desire:
    id: str
    source: str
    urge_type: str
    reason: str
    target_state: str
    current_state: str
    next_step: str
    importance: float
    gap: float
    confidence: float
    actionability: float
    interruption_cost: float
    cooldown_key: str
    created_at: datetime
    next_review_at: datetime | None
    expires_at: datetime | None
    status: str = "open"
    revision: int = 1

    def __post_init__(self) -> None:
        for name in (
            "id",
            "source",
            "urge_type",
            "reason",
            "target_state",
            "current_state",
            "next_step",
            "cooldown_key",
        ):
            _normalize_text(self, name)
        for name in (
            "importance",
            "gap",
            "confidence",
            "actionability",
            "interruption_cost",
        ):
            _validate_unit_interval(name, getattr(self, name))
        _validate_aware_datetime("created_at", self.created_at)
        if self.next_review_at is not None:
            _validate_aware_datetime("next_review_at", self.next_review_at)
        if self.expires_at is not None:
            _validate_aware_datetime("expires_at", self.expires_at)
            if self.expires_at <= self.created_at:
                raise ValidationError("expires_at must be later than created_at")
        if self.status not in DESIRE_STATUSES:
            raise ValidationError(f"unsupported desire status: {self.status}")
        if self.status == "open" and self.next_review_at is None:
            raise ValidationError("open desire requires next_review_at")
        if (
            self.status == "open"
            and self.next_review_at is not None
            and self.next_review_at < self.created_at
        ):
            raise ValidationError("next_review_at cannot be before created_at")
        if type(self.revision) is not int or self.revision < 1:
            raise ValidationError("revision must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "urge_type": self.urge_type,
            "reason": self.reason,
            "target_state": self.target_state,
            "current_state": self.current_state,
            "next_step": self.next_step,
            "importance": float(self.importance),
            "gap": float(self.gap),
            "confidence": float(self.confidence),
            "actionability": float(self.actionability),
            "interruption_cost": float(self.interruption_cost),
            "cooldown_key": self.cooldown_key,
            "created_at": self.created_at.isoformat(),
            "next_review_at": (
                self.next_review_at.isoformat() if self.next_review_at else None
            ),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "status": self.status,
            "revision": self.revision,
        }


@dataclass(frozen=True)
class DesireProgress:
    id: str
    desire_id: str
    recorded_at: datetime
    from_revision: int
    to_revision: int
    current_state: str
    next_step: str
    gap: float
    actionability: float
    next_review_at: datetime | None
    status: str
    note: str | None = None

    def __post_init__(self) -> None:
        for name in ("id", "desire_id", "current_state", "next_step"):
            _normalize_text(self, name)
        _validate_aware_datetime("recorded_at", self.recorded_at)
        if self.next_review_at is not None:
            _validate_aware_datetime("next_review_at", self.next_review_at)
        for name in ("from_revision", "to_revision"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValidationError(f"{name} must be a positive integer")
        if self.to_revision != self.from_revision + 1:
            raise ValidationError("progress must advance revision by exactly one")
        _validate_unit_interval("gap", self.gap)
        _validate_unit_interval("actionability", self.actionability)
        if self.status not in DESIRE_STATUSES:
            raise ValidationError(f"unsupported desire status: {self.status}")
        if self.status == "open" and self.next_review_at is None:
            raise ValidationError("open progress requires next_review_at")
        if (
            self.status == "open"
            and self.next_review_at is not None
            and self.next_review_at < self.recorded_at
        ):
            raise ValidationError("next_review_at cannot be before recorded_at")
        if self.note is not None:
            _normalize_text(self, "note")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "desire_id": self.desire_id,
            "recorded_at": self.recorded_at.isoformat(),
            "from_revision": self.from_revision,
            "to_revision": self.to_revision,
            "current_state": self.current_state,
            "next_step": self.next_step,
            "gap": float(self.gap),
            "actionability": float(self.actionability),
            "next_review_at": (
                self.next_review_at.isoformat() if self.next_review_at else None
            ),
            "status": self.status,
            "note": self.note,
        }


@dataclass(frozen=True)
class DesireReview:
    id: str
    desire_id: str
    revision: int
    evaluated_at: datetime
    score: float
    outcome: str
    urge_id: str | None
    reasons: dict[str, Any]

    def __post_init__(self) -> None:
        for name in ("id", "desire_id"):
            _normalize_text(self, name)
        if type(self.revision) is not int or self.revision < 1:
            raise ValidationError("revision must be a positive integer")
        _validate_aware_datetime("evaluated_at", self.evaluated_at)
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise ValidationError("score must be a number")
        if not math.isfinite(float(self.score)):
            raise ValidationError("score must be finite")
        if self.outcome not in {"SLEEP", "URGE_CREATED", "EXPIRED"}:
            raise ValidationError(f"unsupported review outcome: {self.outcome}")
        if self.outcome == "URGE_CREATED":
            if self.urge_id is None:
                raise ValidationError("URGE_CREATED review requires urge_id")
            _normalize_text(self, "urge_id")
        elif self.urge_id is not None:
            raise ValidationError(f"{self.outcome} review cannot have urge_id")
        if not isinstance(self.reasons, dict):
            raise ValidationError("review reasons must be an object")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "desire_id": self.desire_id,
            "revision": self.revision,
            "evaluated_at": self.evaluated_at.isoformat(),
            "score": float(self.score),
            "outcome": self.outcome,
            "urge_id": self.urge_id,
            "reasons": self.reasons,
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
