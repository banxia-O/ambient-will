"""Dependency-injected receipt reconciliation for recurring Desires."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from integrations.hermes.progress_policy import (
    NONTERMINAL_RECEIPT_STATUSES,
    TERMINAL_RECEIPT_STATUSES,
    ProgressProposal,
    progress_id_for_event,
    propose_progress,
)

ReadDesire = Callable[[str], Mapping[str, object] | None]
ReadProgress = Callable[[str], Mapping[str, object] | None]
AppendProgress = Callable[[str, str, ProgressProposal], int]
UpdateReceipt = Callable[[str, int], bool]


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


def _matches_progress_provenance(
    progress: Mapping[str, object],
    *,
    progress_id: str,
    desire_id: str,
    from_revision: int,
) -> bool:
    return (
        progress.get("id") == progress_id
        and progress.get("desire_id") == desire_id
        and type(progress.get("from_revision")) is int
        and progress.get("from_revision") == from_revision
        and type(progress.get("to_revision")) is int
        and progress.get("to_revision") == from_revision + 1
    )


def reconcile_receipt(
    *,
    receipt: Mapping[str, object],
    recurring_desire_ids: frozenset[str],
    evaluated_at: datetime,
    rearm_after: timedelta,
    read_desire: ReadDesire,
    read_progress: ReadProgress,
    append_progress: AppendProgress,
    update_receipt: UpdateReceipt,
) -> ReconciliationResult:
    """Reconcile one receipt without owning any storage or delivery I/O."""
    if not isinstance(recurring_desire_ids, frozenset) or any(
        not isinstance(desire_id, str) or not desire_id.strip()
        for desire_id in recurring_desire_ids
    ):
        raise ValueError(
            "recurring_desire_ids must be a frozenset of non-empty strings"
        )
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
        expected_progress_id = progress_id_for_event(event_id)
        progress = read_progress(expected_progress_id)
        if not isinstance(progress, Mapping) or not _matches_progress_provenance(
            progress,
            progress_id=expected_progress_id,
            desire_id=desire_id,
            from_revision=receipt_revision,
        ):
            return ReconciliationResult("conflict")
        if update_receipt(event_id, current_revision) is not True:
            return ReconciliationResult("conflict")
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
    if update_receipt(event_id, progress_revision) is not True:
        return ReconciliationResult("conflict")
    return ReconciliationResult("progress_appended", progress_revision)
