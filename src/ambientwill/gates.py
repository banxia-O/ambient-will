from __future__ import annotations

from datetime import datetime, time
from typing import Protocol
from zoneinfo import ZoneInfo

from ambientwill.models import AgentPolicy, QuietWindow


class GateStorage(Protocol):
    def paused_until(self) -> datetime | None: ...

    def daily_message_count(self, at: datetime, zone: ZoneInfo) -> int: ...

    def unanswered_count(self, at: datetime) -> int: ...

    def last_message_time(self) -> datetime | None: ...


def _clock(value: str) -> time:
    return time.fromisoformat(value)


def in_quiet_hours(
    at: datetime,
    windows: tuple[QuietWindow, ...],
    zone: ZoneInfo | None = None,
) -> bool:
    local = at.astimezone(zone) if zone else at
    current = local.timetz().replace(tzinfo=None)
    for window in windows:
        start, end = _clock(window.start), _clock(window.end)
        if start < end and start <= current < end:
            return True
        if start > end and (current >= start or current < end):
            return True
    return False


def evaluate_pre_urge_gates(
    policy: AgentPolicy,
    storage: GateStorage,
    at: datetime,
) -> tuple[list[dict[str, object]], str | None]:
    reasons: list[dict[str, object]] = []

    paused_until = storage.paused_until()
    if paused_until is not None and at < paused_until:
        reasons.append(
            {
                "gate": "paused",
                "passed": False,
                "detail": f"paused until {paused_until.isoformat()}",
            }
        )
        return reasons, "paused"
    reasons.append({"gate": "paused", "passed": True})

    if in_quiet_hours(at, policy.quiet_hours, policy.zone):
        reasons.append(
            {
                "gate": "quiet_hours",
                "passed": False,
                "detail": "current local time is inside a quiet window",
            }
        )
        return reasons, "quiet_hours"
    reasons.append({"gate": "quiet_hours", "passed": True})

    unanswered = storage.unanswered_count(at)
    if unanswered >= policy.unanswered_limit:
        reasons.append(
            {
                "gate": "unanswered_limit",
                "passed": False,
                "value": unanswered,
                "limit": policy.unanswered_limit,
            }
        )
        return reasons, "unanswered_limit"
    reasons.append(
        {
            "gate": "unanswered_limit",
            "passed": True,
            "value": unanswered,
            "limit": policy.unanswered_limit,
        }
    )

    last_message = storage.last_message_time()
    if last_message is not None and at - last_message < policy.min_message_gap:
        reasons.append(
            {
                "gate": "min_message_gap",
                "passed": False,
                "last_message": last_message.isoformat(),
                "required_seconds": int(policy.min_message_gap.total_seconds()),
            }
        )
        return reasons, "min_message_gap"
    reasons.append({"gate": "min_message_gap", "passed": True})
    return reasons, None
