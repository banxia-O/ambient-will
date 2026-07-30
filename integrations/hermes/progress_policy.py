"""Pure policy for rearming explicitly recurring Desire revisions."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

TERMINAL_RECEIPT_STATUSES = frozenset({"sent", "suppressed", "delivery_unknown"})
NONTERMINAL_RECEIPT_STATUSES = frozenset({"generating", "sending"})
DESIRE_STATUSES = frozenset({"open", "blocked", "satisfied", "abandoned", "expired"})


@dataclass(frozen=True)
class ProgressProposal:
    progress_id: str
    expected_revision: int
    recorded_at: str
    current_state: str
    next_step: str
    gap: float
    actionability: float
    next_review_at: str
    status: str
    note: str


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def progress_id_for_event(event_id: object) -> str:
    """Derive a stable, opaque Progress ID from one receipt event ID."""
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id must be a non-empty string")
    digest = hashlib.sha256(event_id.strip().encode("utf-8")).hexdigest()
    return f"aw_rearm_{digest}"


def _field(item: Mapping[str, object], name: str) -> object:
    try:
        return item[name]
    except KeyError as exc:
        raise ValueError(f"missing required field: {name}") from exc


def _text(item: Mapping[str, object], name: str) -> str:
    value = _field(item, name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _revision(value: object, *, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _unit_interval(value: object, *, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise ValueError(f"{name} must be finite and between 0.0 and 1.0")
    return float(value)


def propose_progress(
    *,
    desire: Mapping[str, object],
    receipt: Mapping[str, object],
    recurring_desire_ids: frozenset[str],
    evaluated_at: datetime,
    rearm_after: timedelta,
) -> ProgressProposal | None:
    """Return a private Progress proposal for an eligible terminal receipt."""
    if (
        not isinstance(evaluated_at, datetime)
        or evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
    ):
        raise ValueError("evaluated_at must include a timezone")
    if not isinstance(rearm_after, timedelta) or rearm_after <= timedelta(0):
        raise ValueError("rearm_after must be a positive interval")
    if not isinstance(recurring_desire_ids, frozenset) or any(
        not isinstance(item, str) or not item.strip() for item in recurring_desire_ids
    ):
        raise ValueError("recurring_desire_ids must contain non-empty strings")

    desire_id = _text(desire, "id")
    desire_revision = _revision(
        _field(desire, "revision"),
        name="desire revision",
    )
    desire_status = _text(desire, "status")
    if desire_status not in DESIRE_STATUSES:
        raise ValueError(f"unsupported desire status: {desire_status}")
    current_state = _text(desire, "current_state")
    next_step = _text(desire, "next_step")
    gap = _unit_interval(_field(desire, "gap"), name="gap")
    actionability = _unit_interval(
        _field(desire, "actionability"),
        name="actionability",
    )

    event_id = _text(receipt, "event_id")
    receipt_desire_id = _text(receipt, "desire_id")
    receipt_revision = _revision(
        _field(receipt, "desire_revision"),
        name="receipt desire_revision",
    )
    receipt_status = _text(receipt, "status")
    if receipt_status not in (TERMINAL_RECEIPT_STATUSES | NONTERMINAL_RECEIPT_STATUSES):
        raise ValueError(f"unsupported receipt status: {receipt_status}")
    if receipt_desire_id != desire_id:
        raise ValueError("receipt desire_id does not match Desire")

    progress_revision = receipt.get("progress_revision")
    if progress_revision is not None:
        reconciled_revision = _revision(
            progress_revision,
            name="receipt progress_revision",
        )
        if reconciled_revision != receipt_revision + 1:
            raise ValueError("receipt progress_revision is inconsistent")
        return None

    if receipt_revision != desire_revision:
        raise ValueError("receipt desire_revision does not match Desire")
    if (
        desire_id not in recurring_desire_ids
        or desire_status != "open"
        or receipt_status not in TERMINAL_RECEIPT_STATUSES
    ):
        return None
    try:
        next_review_at = evaluated_at + rearm_after
    except OverflowError as exc:
        raise ValueError("rearm interval exceeds datetime range") from exc
    return ProgressProposal(
        progress_id=progress_id_for_event(event_id),
        expected_revision=desire_revision,
        recorded_at=_timestamp(evaluated_at),
        current_state=current_state,
        next_step=next_step,
        gap=gap,
        actionability=actionability,
        next_review_at=_timestamp(next_review_at),
        status="open",
        note=f"outcome={receipt_status}",
    )
