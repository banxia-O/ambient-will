from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from integrations.hermes.progress_policy import progress_id_for_event
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


def stored_progress(event_id: str = "event-4", **changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": progress_id_for_event(event_id),
        "desire_id": "recurring-check-in",
        "from_revision": 4,
        "to_revision": 5,
    }
    values.update(changes)
    return values


def test_unrelated_adjacent_revision_without_expected_progress_is_conflict() -> None:
    append_calls = []
    receipt_updates = []
    progress_reads = []

    def read_progress(progress_id: str):
        progress_reads.append(progress_id)

    result = reconcile_receipt(
        receipt=receipt(),
        recurring_desire_ids=RECURRING,
        evaluated_at=NOW,
        rearm_after=timedelta(hours=6),
        read_desire=lambda _desire_id: desire(revision=5),
        read_progress=read_progress,
        append_progress=lambda *args: append_calls.append(args),
        update_receipt=lambda *args: receipt_updates.append(args),
    )

    assert result.action == "conflict"
    assert progress_reads == [progress_id_for_event("event-4")]
    assert append_calls == []
    assert receipt_updates == []


def test_another_receipt_progress_cannot_be_claimed_by_current_receipt() -> None:
    progress_by_id = {
        progress_id_for_event("receipt-B"): stored_progress("receipt-B"),
    }
    requested_progress_ids = []
    receipt_updates = []

    def read_progress(progress_id: str):
        requested_progress_ids.append(progress_id)
        return progress_by_id.get(progress_id)

    result = reconcile_receipt(
        receipt=receipt(),
        recurring_desire_ids=RECURRING,
        evaluated_at=NOW,
        rearm_after=timedelta(hours=6),
        read_desire=lambda _desire_id: desire(revision=5),
        read_progress=read_progress,
        append_progress=lambda *_args: pytest.fail("must not append"),
        update_receipt=lambda *args: receipt_updates.append(args),
    )

    assert result.action == "conflict"
    assert requested_progress_ids == [progress_id_for_event("event-4")]
    assert receipt_updates == []


def test_matching_progress_provenance_repairs_only_receipt_marker() -> None:
    append_calls = []
    receipt_updates = []

    def update_receipt(event_id: str, progress_revision: int) -> bool:
        receipt_updates.append((event_id, progress_revision))
        return True

    result = reconcile_receipt(
        receipt=receipt(),
        recurring_desire_ids=RECURRING,
        evaluated_at=NOW,
        rearm_after=timedelta(hours=6),
        read_desire=lambda _desire_id: desire(revision=5),
        read_progress=lambda _progress_id: stored_progress(),
        append_progress=lambda *args: append_calls.append(args),
        update_receipt=update_receipt,
    )

    assert result.action == "receipt_marked"
    assert result.progress_revision == 5
    assert append_calls == []
    assert receipt_updates == [("event-4", 5)]


@pytest.mark.parametrize(
    "progress_changes",
    [
        {"id": progress_id_for_event("different-event")},
        {"desire_id": "different-desire"},
        {"from_revision": 3},
        {"to_revision": 6},
    ],
)
def test_mismatched_progress_provenance_fails_closed(
    progress_changes: dict[str, object],
) -> None:
    receipt_updates = []

    result = reconcile_receipt(
        receipt=receipt(),
        recurring_desire_ids=RECURRING,
        evaluated_at=NOW,
        rearm_after=timedelta(hours=6),
        read_desire=lambda _desire_id: desire(revision=5),
        read_progress=lambda _progress_id: stored_progress(**progress_changes),
        append_progress=lambda *_args: pytest.fail("must not append"),
        update_receipt=lambda *args: receipt_updates.append(args),
    )

    assert result.action == "conflict"
    assert receipt_updates == []


@pytest.mark.parametrize(
    "recurring_desire_ids",
    [
        ["recurring-check-in"],
        {"recurring-check-in"},
        "recurring-check-in",
        frozenset({""}),
        frozenset({4}),
    ],
)
def test_invalid_allowlist_fails_before_dependencies(
    recurring_desire_ids: object,
) -> None:
    dependency_calls = []

    with pytest.raises(ValueError, match="recurring_desire_ids"):
        reconcile_receipt(
            receipt=receipt(),
            recurring_desire_ids=recurring_desire_ids,
            evaluated_at=NOW,
            rearm_after=timedelta(hours=6),
            read_desire=lambda *args: dependency_calls.append(("read_desire", args)),
            read_progress=lambda *args: dependency_calls.append(
                ("read_progress", args)
            ),
            append_progress=lambda *args: dependency_calls.append(("append", args)),
            update_receipt=lambda *args: dependency_calls.append(("update", args)),
        )

    assert dependency_calls == []


def test_same_receipt_advances_progress_at_most_once() -> None:
    current_desire = desire()
    current_receipt = receipt()
    appended = []
    progress_by_id = {}
    receipt_updates = []

    def read_desire(desire_id: str):
        assert desire_id == "recurring-check-in"
        return dict(current_desire)

    def append_progress(desire_id, event_id, proposal):
        assert desire_id == "recurring-check-in"
        assert event_id == "event-4"
        assert proposal.expected_revision == current_desire["revision"]
        appended.append(proposal)
        progress_by_id[proposal.progress_id] = stored_progress(
            id=proposal.progress_id,
            from_revision=proposal.expected_revision,
            to_revision=proposal.expected_revision + 1,
        )
        current_desire["revision"] = proposal.expected_revision + 1
        return current_desire["revision"]

    def update_receipt(event_id: str, progress_revision: int) -> bool:
        assert event_id == "event-4"
        receipt_updates.append(progress_revision)
        current_receipt["progress_revision"] = progress_revision
        return True

    first = reconcile_receipt(
        receipt=current_receipt,
        recurring_desire_ids=RECURRING,
        evaluated_at=NOW,
        rearm_after=timedelta(hours=6),
        read_desire=read_desire,
        read_progress=progress_by_id.get,
        append_progress=append_progress,
        update_receipt=update_receipt,
    )
    second = reconcile_receipt(
        receipt=current_receipt,
        recurring_desire_ids=RECURRING,
        evaluated_at=NOW,
        rearm_after=timedelta(hours=6),
        read_desire=read_desire,
        read_progress=progress_by_id.get,
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
    progress_by_id = {}
    receipt_updates = []

    def read_desire(_desire_id: str):
        return dict(current_desire)

    def append_progress(_desire_id, _event_id, proposal):
        appended.append(proposal)
        progress_by_id[proposal.progress_id] = stored_progress(
            id=proposal.progress_id,
            from_revision=proposal.expected_revision,
            to_revision=proposal.expected_revision + 1,
        )
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
            read_progress=progress_by_id.get,
            append_progress=append_progress,
            update_receipt=crash_before_receipt_write,
        )

    def update_receipt(event_id: str, progress_revision: int) -> bool:
        receipt_updates.append((event_id, progress_revision))
        current_receipt["progress_revision"] = progress_revision
        return True

    recovered = reconcile_receipt(
        receipt=current_receipt,
        recurring_desire_ids=RECURRING,
        evaluated_at=NOW + timedelta(minutes=1),
        rearm_after=timedelta(hours=6),
        read_desire=read_desire,
        read_progress=progress_by_id.get,
        append_progress=append_progress,
        update_receipt=update_receipt,
    )

    assert recovered.action == "receipt_marked"
    assert recovered.progress_revision == 5
    assert len(appended) == 1
    assert receipt_updates == [("event-4", 5)]


def test_receipt_marker_cas_failure_after_append_fails_closed() -> None:
    appended = []
    receipt_updates = []

    def append_progress(_desire_id, _event_id, proposal):
        appended.append(proposal)
        return proposal.expected_revision + 1

    def reject_receipt_update(event_id: str, progress_revision: int) -> bool:
        receipt_updates.append((event_id, progress_revision))
        return False

    result = reconcile_receipt(
        receipt=receipt(),
        recurring_desire_ids=RECURRING,
        evaluated_at=NOW,
        rearm_after=timedelta(hours=6),
        read_desire=lambda _desire_id: desire(),
        read_progress=lambda _progress_id: None,
        append_progress=append_progress,
        update_receipt=reject_receipt_update,
    )

    assert result.action == "conflict"
    assert result.progress_revision is None
    assert len(appended) == 1
    assert receipt_updates == [("event-4", 5)]


def test_receipt_marker_cas_failure_during_recovery_fails_closed() -> None:
    receipt_updates = []

    def reject_receipt_update(event_id: str, progress_revision: int) -> bool:
        receipt_updates.append((event_id, progress_revision))
        return False

    result = reconcile_receipt(
        receipt=receipt(),
        recurring_desire_ids=RECURRING,
        evaluated_at=NOW,
        rearm_after=timedelta(hours=6),
        read_desire=lambda _desire_id: desire(revision=5),
        read_progress=lambda _progress_id: stored_progress(),
        append_progress=lambda *_args: pytest.fail("must not append"),
        update_receipt=reject_receipt_update,
    )

    assert result.action == "conflict"
    assert result.progress_revision is None
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
        read_progress=lambda _progress_id: None,
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
        read_progress=lambda _progress_id: None,
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
    progress_reads = []
    appends = []
    updates = []

    result = reconcile_receipt(
        receipt=current_receipt,
        recurring_desire_ids=RECURRING,
        evaluated_at=NOW,
        rearm_after=timedelta(hours=6),
        read_desire=lambda *args: reads.append(args),
        read_progress=lambda *args: progress_reads.append(args),
        append_progress=lambda *args: appends.append(args),
        update_receipt=lambda *args: updates.append(args),
    )

    assert result.action == "not_eligible"
    assert reads == []
    assert progress_reads == []
    assert appends == []
    assert updates == []
