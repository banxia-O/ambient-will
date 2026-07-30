from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from integrations.hermes.progress_policy import (
    progress_id_for_event,
    propose_progress,
)

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def desire(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": "recurring-check-in",
        "revision": 4,
        "status": "open",
        "current_state": "A recurring private goal remains active.",
        "next_step": "Reassess whether contact is worthwhile.",
        "gap": 0.6,
        "actionability": 0.7,
    }
    values.update(changes)
    return values


def receipt(outcome: str, **changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "event_id": "event-4",
        "desire_id": "recurring-check-in",
        "desire_revision": 4,
        "status": outcome,
    }
    values.update(changes)
    return values


def test_sent_proposes_future_open_progress() -> None:
    proposal = propose_progress(
        desire=desire(),
        receipt=receipt("sent"),
        recurring_desire_ids=frozenset({"recurring-check-in"}),
        evaluated_at=NOW,
        rearm_after=timedelta(hours=6),
    )

    assert proposal is not None
    assert proposal.progress_id == progress_id_for_event("event-4")
    assert proposal.expected_revision == 4
    assert proposal.recorded_at == "2026-03-01T12:00:00.000000+00:00"
    assert proposal.current_state == "A recurring private goal remains active."
    assert proposal.next_step == "Reassess whether contact is worthwhile."
    assert proposal.gap == 0.6
    assert proposal.actionability == 0.7
    assert proposal.next_review_at == "2026-03-01T18:00:00.000000+00:00"
    assert proposal.status == "open"
    assert proposal.note == "outcome=sent"


def test_progress_id_is_stable_and_event_specific() -> None:
    first = progress_id_for_event("event-4")

    assert first == progress_id_for_event("event-4")
    assert first != progress_id_for_event("event-5")
    assert first.startswith("aw_rearm_")
    assert len(first) == len("aw_rearm_") + 64
    assert all(character in "0123456789abcdef" for character in first[9:])


@pytest.mark.parametrize("event_id", ["", "   ", None, 4])
def test_progress_id_rejects_invalid_event_id(event_id: object) -> None:
    with pytest.raises(ValueError):
        progress_id_for_event(event_id)


@pytest.mark.parametrize("outcome", ["suppressed", "delivery_unknown"])
def test_other_terminal_outcomes_propose_future_open_progress(outcome: str) -> None:
    proposal = propose_progress(
        desire=desire(),
        receipt=receipt(outcome),
        recurring_desire_ids=frozenset({"recurring-check-in"}),
        evaluated_at=NOW,
        rearm_after=timedelta(days=1),
    )

    assert proposal is not None
    assert proposal.status == "open"
    assert proposal.next_review_at == "2026-03-02T12:00:00.000000+00:00"
    assert proposal.note == f"outcome={outcome}"
    if outcome == "delivery_unknown":
        normalized = (
            f"{proposal.current_state} {proposal.next_step} {proposal.note}".casefold()
        )
        assert all(
            word not in normalized for word in ("retry", "resend", "重试", "补发")
        )


@pytest.mark.parametrize("outcome", ["generating", "sending"])
def test_nonterminal_receipt_does_not_propose_progress(outcome: str) -> None:
    assert (
        propose_progress(
            desire=desire(),
            receipt=receipt(outcome),
            recurring_desire_ids=frozenset({"recurring-check-in"}),
            evaluated_at=NOW,
            rearm_after=timedelta(hours=6),
        )
        is None
    )


def test_non_allowlisted_desire_does_not_propose_progress() -> None:
    assert (
        propose_progress(
            desire=desire(),
            receipt=receipt("sent"),
            recurring_desire_ids=frozenset({"another-recurring-desire"}),
            evaluated_at=NOW,
            rearm_after=timedelta(hours=6),
        )
        is None
    )


@pytest.mark.parametrize("status", ["blocked", "satisfied", "abandoned", "expired"])
def test_non_open_desire_does_not_propose_progress(status: str) -> None:
    assert (
        propose_progress(
            desire=desire(status=status),
            receipt=receipt("sent"),
            recurring_desire_ids=frozenset({"recurring-check-in"}),
            evaluated_at=NOW,
            rearm_after=timedelta(hours=6),
        )
        is None
    )


@pytest.mark.parametrize(
    ("evaluated_at", "rearm_after"),
    [
        (NOW.replace(tzinfo=None), timedelta(hours=1)),
        (NOW, timedelta(0)),
        (NOW, timedelta(microseconds=-1)),
    ],
)
def test_invalid_time_inputs_fail_closed(
    evaluated_at: datetime, rearm_after: timedelta
) -> None:
    with pytest.raises(ValueError):
        propose_progress(
            desire=desire(),
            receipt=receipt("sent"),
            recurring_desire_ids=frozenset({"recurring-check-in"}),
            evaluated_at=evaluated_at,
            rearm_after=rearm_after,
        )


@pytest.mark.parametrize("revision", [True, 0, -1, 4.0, "4"])
def test_invalid_desire_revision_fails_closed(revision: object) -> None:
    with pytest.raises(ValueError):
        propose_progress(
            desire=desire(revision=revision),
            receipt=receipt("sent"),
            recurring_desire_ids=frozenset({"recurring-check-in"}),
            evaluated_at=NOW,
            rearm_after=timedelta(hours=6),
        )


@pytest.mark.parametrize(
    "receipt_changes",
    [
        {"desire_id": "different-desire"},
        {"desire_revision": 3},
        {"desire_revision": True},
        {"status": "unknown"},
    ],
)
def test_invalid_receipt_identity_or_status_fails_closed(
    receipt_changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        propose_progress(
            desire=desire(),
            receipt=receipt("sent", **receipt_changes),
            recurring_desire_ids=frozenset({"recurring-check-in"}),
            evaluated_at=NOW,
            rearm_after=timedelta(hours=6),
        )


def test_already_reconciled_receipt_does_not_propose_progress() -> None:
    assert (
        propose_progress(
            desire=desire(),
            receipt=receipt("sent", progress_revision=5),
            recurring_desire_ids=frozenset({"recurring-check-in"}),
            evaluated_at=NOW,
            rearm_after=timedelta(hours=6),
        )
        is None
    )


def test_proposal_ignores_receipt_prose_and_has_no_visible_template_labels() -> None:
    proposal = propose_progress(
        desire=desire(),
        receipt=receipt(
            "delivery_unknown",
            generated_message="Do not copy this private generated prose.",
            user_text="Do not copy private conversation text.",
        ),
        recurring_desire_ids=frozenset({"recurring-check-in"}),
        evaluated_at=NOW,
        rearm_after=timedelta(hours=6),
    )

    assert proposal is not None
    normalized = (
        f"{proposal.current_state} {proposal.next_step} {proposal.note}".casefold()
    )
    assert "do not copy" not in normalized
    assert all(
        marker not in normalized
        for marker in (
            "ambientwill",
            "wake",
            "test",
            "adapter",
            "message_preview",
            "retry",
            "resend",
            "接入",
            "补发",
            "重试",
        )
    )
