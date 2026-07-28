from __future__ import annotations

import hashlib
import random
import sqlite3
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

from ambientwill.gates import evaluate_pre_urge_gates, in_quiet_hours
from ambientwill.models import (
    AgentPolicy,
    Decision,
    OutboxEvent,
    TickResult,
    Urge,
    WakeEvent,
)
from ambientwill.storage import AlreadyRunningError, Storage, TickLock


def _reason(
    gate: str,
    passed: bool,
    detail: str | None = None,
    **values: object,
) -> dict[str, object]:
    result: dict[str, object] = {"gate": gate, "passed": passed}
    if detail is not None:
        result["detail"] = detail
    result.update(values)
    return result


class Engine:
    def __init__(
        self,
        policy: AgentPolicy,
        storage: Storage,
        *,
        rng: random.Random | None = None,
    ):
        self.policy = policy
        self.storage = storage
        self.rng = rng or random.SystemRandom()

    def tick(
        self,
        *,
        at: datetime | None = None,
        trigger: str = "cli",
        dry_run: bool = False,
        fail_after_wake: bool = False,
    ) -> TickResult:
        evaluated_at = at or datetime.now(UTC)
        if evaluated_at.tzinfo is None:
            raise ValueError("tick time must include a timezone")

        lock = nullcontext() if dry_run else TickLock(self.storage.lock_path)
        try:
            with lock:
                if not dry_run and self.storage.wake_seen(trigger, evaluated_at):
                    return TickResult(
                        decision=Decision.SLEEP,
                        reasons=[
                            _reason(
                                "idempotency_key",
                                False,
                                "this trigger and evaluation time were already recorded",
                            )
                        ],
                        blocked_by="idempotency_key",
                    )
                result, urge = self._evaluate(evaluated_at)
                result.dry_run = dry_run
                if dry_run:
                    self._apply_delay_gates(result, urge, evaluated_at)
                    return result
                return self._commit(
                    result,
                    urge,
                    at=evaluated_at,
                    trigger=trigger,
                    fail_after_wake=fail_after_wake,
                )
        except AlreadyRunningError:
            return TickResult(
                decision=Decision.SLEEP,
                reasons=[
                    _reason(
                        "single_instance_lock",
                        False,
                        "another AmbientWill tick is already running",
                    )
                ],
                blocked_by="single_instance_lock",
                already_running=True,
                error="already_running",
                dry_run=dry_run,
            )

    def _evaluate(self, at: datetime) -> tuple[TickResult, Urge | None]:
        reasons, blocked = evaluate_pre_urge_gates(self.policy, self.storage, at)
        if blocked:
            return (
                TickResult(
                    decision=Decision.SLEEP,
                    reasons=reasons,
                    blocked_by=blocked,
                ),
                None,
            )

        urges = self.storage.valid_urges(at)
        if not urges:
            reasons.append(
                _reason("valid_urge", False, "no open, active, unexpired urge")
            )
            return (
                TickResult(
                    decision=Decision.SLEEP,
                    reasons=reasons,
                    blocked_by="no_valid_urge",
                ),
                None,
            )
        reasons.append(_reason("valid_urge", True, count=len(urges)))

        eligible = [
            urge
            for urge in urges
            if not self.storage.cooldown_seen_since(
                urge.cooldown_key, at - self.policy.cooldown
            )
        ]
        if not eligible:
            selected = max(
                urges, key=lambda urge: (urge.score, -urge.created_at.timestamp())
            )
            reasons.append(
                _reason(
                    "cooldown_key",
                    False,
                    key=selected.cooldown_key,
                    cooldown_seconds=int(self.policy.cooldown.total_seconds()),
                    excluded=len(urges),
                )
            )
            return (
                TickResult(
                    decision=Decision.SLEEP,
                    reasons=reasons,
                    selected_urge_id=selected.id,
                    score=selected.score,
                    blocked_by="cooldown_key",
                ),
                selected,
            )
        selected = max(
            eligible, key=lambda urge: (urge.score, -urge.created_at.timestamp())
        )
        score = selected.score
        reasons.append(
            _reason(
                "cooldown_key",
                True,
                key=selected.cooldown_key,
                excluded=len(urges) - len(eligible),
            )
        )

        if score >= self.policy.message_threshold:
            decision = Decision.MESSAGE_PLANNED
            reasons.append(
                _reason(
                    "score_threshold",
                    True,
                    score=score,
                    threshold=self.policy.message_threshold,
                    outcome=decision.value,
                )
            )
            blocked_by = None
        elif score >= self.policy.reflect_threshold:
            decision = Decision.REFLECT
            reasons.append(
                _reason(
                    "score_threshold",
                    True,
                    score=score,
                    threshold=self.policy.reflect_threshold,
                    outcome=decision.value,
                )
            )
            blocked_by = None
        else:
            decision = Decision.SLEEP
            blocked_by = "below_reflect_threshold"
            reasons.append(
                _reason(
                    "score_threshold",
                    False,
                    score=score,
                    threshold=self.policy.reflect_threshold,
                    outcome=decision.value,
                )
            )
        return (
            TickResult(
                decision=decision,
                reasons=reasons,
                selected_urge_id=selected.id,
                score=score,
                blocked_by=blocked_by,
            ),
            selected,
        )

    def _apply_delay_gates(
        self, result: TickResult, urge: Urge | None, at: datetime
    ) -> datetime | None:
        if result.decision is not Decision.MESSAGE_PLANNED or urge is None:
            return None
        jitter = self.rng.randint(
            self.policy.jitter_min_minutes,
            self.policy.jitter_max_minutes,
        )
        delayed_until = at + timedelta(minutes=jitter)
        if in_quiet_hours(delayed_until, self.policy.quiet_hours, self.policy.zone):
            result.decision = Decision.SLEEP
            result.blocked_by = "delayed_into_quiet_hours"
            result.reasons.append(
                _reason(
                    "delayed_quiet_hours",
                    False,
                    "jitter landed inside a quiet window",
                    delayed_until=delayed_until.isoformat(),
                )
            )
            return None

        last_message = self.storage.last_message_time()
        if (
            last_message is not None
            and delayed_until - last_message < self.policy.min_message_gap
        ):
            result.decision = Decision.SLEEP
            result.blocked_by = "delayed_minimum_message_gap"
            result.reasons.append(
                _reason(
                    "delayed_minimum_message_gap",
                    False,
                    "the delayed plan would violate the minimum message gap",
                    last_message_at=last_message.isoformat(),
                    delayed_until=delayed_until.isoformat(),
                    required_seconds=int(self.policy.min_message_gap.total_seconds()),
                )
            )
            return None
        result.reasons.append(
            _reason(
                "delayed_minimum_message_gap",
                True,
                last_message_at=last_message.isoformat() if last_message else None,
                delayed_until=delayed_until.isoformat(),
                required_seconds=int(self.policy.min_message_gap.total_seconds()),
            )
        )

        daily_count = self.storage.daily_message_count(delayed_until, self.policy.zone)
        if daily_count >= self.policy.daily_message_hard_limit:
            result.decision = Decision.SLEEP
            result.blocked_by = "daily_message_hard_limit"
            result.reasons.append(
                _reason(
                    "daily_message_hard_limit",
                    False,
                    "the delayed local day has no remaining message budget",
                    value=daily_count,
                    limit=self.policy.daily_message_hard_limit,
                    delayed_until=delayed_until.isoformat(),
                )
            )
            return None

        result.reasons.append(
            _reason(
                "daily_message_hard_limit",
                True,
                value=daily_count,
                limit=self.policy.daily_message_hard_limit,
                delayed_until=delayed_until.isoformat(),
            )
        )
        result.delayed_until = delayed_until
        return delayed_until

    def _commit(
        self,
        result: TickResult,
        urge: Urge | None,
        *,
        at: datetime,
        trigger: str,
        fail_after_wake: bool,
    ) -> TickResult:
        wake_id = f"aw_wake_{uuid.uuid4().hex}"
        outbox: OutboxEvent | None = None
        delayed_until = self._apply_delay_gates(result, urge, at)
        if (
            result.decision is Decision.MESSAGE_PLANNED
            and urge is not None
            and delayed_until is not None
        ):
            raw_key = f"{urge.id}|{urge.cooldown_key}"
            idempotency_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
            event_id = f"aw_out_{uuid.uuid4().hex}"
            outbox = OutboxEvent(
                event_id=event_id,
                wake_event_id=wake_id,
                message_preview=urge.reason,
                idempotency_key=idempotency_key,
                planned_at=at,
                delayed_until=delayed_until,
                state="planned",
                cooldown_key=urge.cooldown_key,
            )
            result.outbox_event_id = event_id

        wake = WakeEvent(
            id=wake_id,
            trigger=trigger,
            evaluated_at=at,
            selected_urge_id=result.selected_urge_id,
            decision=result.decision,
            reasons=result.reasons,
            created_at=at,
        )

        try:
            outbox_inserted = self.storage._record_decision_unlocked(
                wake,
                outbox,
                fail_after_wake=fail_after_wake,
            )
        except (sqlite3.Error, OSError):
            return TickResult(
                decision=Decision.SLEEP,
                reasons=result.reasons
                + [
                    _reason(
                        "transaction",
                        False,
                        "decision was rolled back; no message was planned",
                    )
                ],
                selected_urge_id=result.selected_urge_id,
                score=result.score,
                blocked_by="transaction_failed",
                error="transaction_failed",
            )

        result.wake_event_id = wake_id
        if outbox is not None and not outbox_inserted:
            return TickResult(
                decision=Decision.SLEEP,
                reasons=result.reasons
                + [
                    _reason(
                        "idempotency_key",
                        False,
                        "an equivalent shadow outbox event already exists",
                    )
                ],
                selected_urge_id=result.selected_urge_id,
                score=result.score,
                blocked_by="idempotency_key",
            )
        return result
