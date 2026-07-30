from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from integrations.hermes.progress_reconciliation import (
    RevisionConflictError,
    reconcile_receipt,
)

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
RECURRING = frozenset({"recurring-check-in"})


def desire(revision: int = 4) -> dict[str, object]:
    return {
        "id": "recurring-check-in",
        "revision": revision,
        "status": "open",
        "current_state": "A recurring private goal remains active.",
        "next_step": "Reassess whether contact is worthwhile.",
        "gap": 0.6,
        "actionability": 0.7,
    }


def receipt() -> dict[str, object]:
    return {
        "event_id": "event-4",
        "desire_id": "recurring-check-in",
        "desire_revision": 4,
        "status": "delivery_unknown",
    }


def test_same_receipt_advances_progress_at_most_once() -> None:
    current_desire = desire()
    current_receipt = receipt()
    appended = []
    receipt_updates = []

    def read_desire(desire_id: str):
        assert desire_id == "recurring-check-in"
        return dict(current_desire)

    def append_progress(desire_id, event_id, proposal):
        assert desire_id == "recurring-check-in"
        assert event_id == "event-4"
        assert proposal.expected_revision == current_desire["revision"]
        appended.append(proposal)
        current_desire["revision"] = proposal.expected_revision + 1
        return current_desire["revision"]

    def update_receipt(event_id: str, progress_revision: int):
        assert event_id == "event-4"
        receipt_updates.append(progress_revision)
        current_receipt["progress_revision"] = progress_revision

    first = reconcile_receipt(
        receipt=current_receipt,
        recurring_desire_ids=RECURRING,
        evaluated_at=NOW,
        rearm_after=timedelta(hours=6),
        read_desire=read_desire,
        append_progress=append_progress,
        update_receipt=update_receipt,
    )
    second = reconcile_receipt(
        receipt=current_receipt,
        recurring_desire_ids=RECURRING,
        evaluated_at=NOW,
        rearm_after=timedelta(hours=6),
        read_desire=read_desire,
        append_progress=append_progress,
        update_receipt=update_receipt,
    )

    assert first.action == "progress_appended"
    assert first.progress_revision == 5
    assert second.action == "already_reconciled"
    assert len(appended) == 1
    assert receipt_updates == [5]


def test_crash_after_progress_commit_only_repairs_receipt_marker() -> None:
    current_desire = desire()
    current_receipt = receipt()
    appended = []
    receipt_updates = []

    def read_desire(_desire_id: str):
        return dict(current_desire)

    def append_progress(_desire_id, _event_id, proposal):
        appended.append(proposal)
        current_desire["revision"] = proposal.expected_revision + 1
        return current_desire["revision"]

    def crash_before_receipt_write(_event_id: str, _progress_revision: int):
        raise RuntimeError("simulated crash before receipt marker")

    with pytest.raises(RuntimeError, match="simulated crash"):
        reconcile_receipt(
            receipt=current_receipt,
            recurring_desire_ids=RECURRING,
            evaluated_at=NOW,
            rearm_after=timedelta(hours=6),
            read_desire=read_desire,
            append_progress=append_progress,
            update_receipt=crash_before_receipt_write,
        )

    def update_receipt(event_id: str, progress_revision: int):
        receipt_updates.append((event_id, progress_revision))
        current_receipt["progress_revision"] = progress_revision

    recovered = reconcile_receipt(
        receipt=current_receipt,
        recurring_desire_ids=RECURRING,
        evaluated_at=NOW + timedelta(minutes=1),
        rearm_after=timedelta(hours=6),
        read_desire=read_desire,
        append_progress=append_progress,
        update_receipt=update_receipt,
    )

    assert recovered.action == "receipt_marked"
    assert recovered.progress_revision == 5
    assert len(appended) == 1
    assert receipt_updates == [("event-4", 5)]


def test_real_cas_conflict_fails_closed_without_receipt_marker() -> None:
    append_calls = []
    receipt_updates = []

    def append_progress(_desire_id, _event_id, proposal):
        append_calls.append(proposal)
        raise RevisionConflictError("revision changed concurrently")

    result = reconcile_receipt(
        receipt=receipt(),
        recurring_desire_ids=RECURRING,
        evaluated_at=NOW,
        rearm_after=timedelta(hours=6),
        read_desire=lambda _desire_id: desire(),
        append_progress=append_progress,
        update_receipt=lambda *args: receipt_updates.append(args),
    )

    assert result.action == "conflict"
    assert result.progress_revision is None
    assert len(append_calls) == 1
    assert receipt_updates == []


def test_preexisting_revision_drift_fails_closed_without_append() -> None:
    append_calls = []
    receipt_updates = []

    result = reconcile_receipt(
        receipt=receipt(),
        recurring_desire_ids=RECURRING,
        evaluated_at=NOW,
        rearm_after=timedelta(hours=6),
        read_desire=lambda _desire_id: desire(revision=6),
        append_progress=lambda *args: append_calls.append(args),
        update_receipt=lambda *args: receipt_updates.append(args),
    )

    assert result.action == "conflict"
    assert append_calls == []
    assert receipt_updates == []


def test_nonterminal_receipt_does_not_call_dependencies() -> None:
    current_receipt = receipt()
    current_receipt["status"] = "sending"
    reads = []
    appends = []
    updates = []

    result = reconcile_receipt(
        receipt=current_receipt,
        recurring_desire_ids=RECURRING,
        evaluated_at=NOW,
        rearm_after=timedelta(hours=6),
        read_desire=lambda *args: reads.append(args),
        append_progress=lambda *args: appends.append(args),
        update_receipt=lambda *args: updates.append(args),
    )

    assert result.action == "not_eligible"
    assert reads == []
    assert appends == []
    assert updates == []
