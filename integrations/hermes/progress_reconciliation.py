"""Dependency-injected receipt reconciliation for recurring Desires."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from integrations.hermes.progress_policy import (
    NONTERMINAL_RECEIPT_STATUSES,
    TERMINAL_RECEIPT_STATUSES,
    ProgressProposal,
    propose_progress,
)

ReadDesire = Callable[[str], Mapping[str, object] | None]
AppendProgress = Callable[[str, str, ProgressProposal], int]
UpdateReceipt = Callable[[str, int], None]


class RevisionConflictError(RuntimeError):
    """Raised by an injected append operation when optimistic CAS fails."""


@dataclass(frozen=True)
class ReconciliationResult:
    action: str
    progress_revision: int | None = None


def _text(item: Mapping[str, object], name: str) -> str:
    value = item.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _revision(value: object, *, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def reconcile_receipt(
    *,
    receipt: Mapping[str, object],
    recurring_desire_ids: frozenset[str],
    evaluated_at: datetime,
    rearm_after: timedelta,
    read_desire: ReadDesire,
    append_progress: AppendProgress,
    update_receipt: UpdateReceipt,
) -> ReconciliationResult:
    """Reconcile one receipt without owning any storage or delivery I/O."""
    event_id = _text(receipt, "event_id")
    desire_id = _text(receipt, "desire_id")
    receipt_revision = _revision(
        receipt.get("desire_revision"),
        name="receipt desire_revision",
    )
    receipt_status = _text(receipt, "status")
    if receipt_status in NONTERMINAL_RECEIPT_STATUSES:
        return ReconciliationResult("not_eligible")
    if receipt_status not in TERMINAL_RECEIPT_STATUSES:
        raise ValueError(f"unsupported receipt status: {receipt_status}")
    if desire_id not in recurring_desire_ids:
        return ReconciliationResult("not_eligible")
    marked_revision = receipt.get("progress_revision")
    if marked_revision is not None:
        progress_revision = _revision(
            marked_revision,
            name="receipt progress_revision",
        )
        if progress_revision != receipt_revision + 1:
            raise ValueError("receipt progress_revision is inconsistent")
        return ReconciliationResult("already_reconciled", progress_revision)

    current = read_desire(desire_id)
    if current is None:
        return ReconciliationResult("desire_missing")
    current_id = _text(current, "id")
    if current_id != desire_id:
        raise ValueError("read_desire returned a different Desire")
    current_revision = _revision(
        current.get("revision"),
        name="current desire revision",
    )
    if current.get("status") != "open":
        return ReconciliationResult("not_eligible")
    if current_revision == receipt_revision + 1:
        update_receipt(event_id, current_revision)
        return ReconciliationResult("receipt_marked", current_revision)
    if current_revision != receipt_revision:
        return ReconciliationResult("conflict")
    proposal = propose_progress(
        desire=current,
        receipt=receipt,
        recurring_desire_ids=recurring_desire_ids,
        evaluated_at=evaluated_at,
        rearm_after=rearm_after,
    )
    if proposal is None:
        return ReconciliationResult("not_eligible")
    try:
        progress_revision = append_progress(desire_id, event_id, proposal)
    except RevisionConflictError:
        return ReconciliationResult("conflict")
    expected_new_revision = proposal.expected_revision + 1
    if type(progress_revision) is not int or progress_revision != expected_new_revision:
        raise ValueError("append_progress returned an unexpected revision")
    update_receipt(event_id, progress_revision)
    return ReconciliationResult("progress_appended", progress_revision)
