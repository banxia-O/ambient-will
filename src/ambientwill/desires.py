from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

from ambientwill.models import AgentPolicy, Desire, DesireReview, Urge
from ambientwill.storage import Storage, TickLock, _canonical_timestamp


def _stable_id(prefix: str, desire_id: str, revision: int) -> str:
    digest = hashlib.sha256(f"{desire_id}|{revision}".encode()).hexdigest()[:32]
    return f"{prefix}_{digest}"


class DesireReviewer:
    """Deterministically turn eligible Desire revisions into candidate Urges."""

    def __init__(self, policy: AgentPolicy, storage: Storage):
        self.policy = policy
        self.storage = storage

    def _evaluate(self, desire: Desire, at: datetime) -> dict[str, object]:
        urgency = float(desire.importance) * float(desire.gap)
        urge_confidence = float(desire.confidence) * float(desire.actionability)
        score = urgency + urge_confidence - float(desire.interruption_cost)
        expired = desire.expires_at is not None and desire.expires_at <= at
        reasons = {
            "importance": float(desire.importance),
            "gap": float(desire.gap),
            "urgency": urgency,
            "confidence": float(desire.confidence),
            "actionability": float(desire.actionability),
            "urge_confidence": urge_confidence,
            "interruption_cost": float(desire.interruption_cost),
            "score": score,
            "reflect_threshold": float(self.policy.reflect_threshold),
            "expires_at": desire.expires_at.isoformat() if desire.expires_at else None,
            "expired": expired,
        }
        if expired:
            outcome = "EXPIRED"
            urge_id = None
        elif score < self.policy.reflect_threshold:
            outcome = "SLEEP"
            urge_id = None
        else:
            outcome = "URGE_CREATED"
            urge_id = _stable_id("aw_urge", desire.id, desire.revision)
        review = DesireReview(
            id=_stable_id("aw_review", desire.id, desire.revision),
            desire_id=desire.id,
            revision=desire.revision,
            evaluated_at=at,
            score=score,
            outcome=outcome,
            urge_id=urge_id,
            reasons=reasons,
        )
        urge = (
            Urge(
                id=urge_id,
                type=desire.urge_type,
                reason=desire.next_step,
                urgency=urgency,
                confidence=urge_confidence,
                interruption_cost=desire.interruption_cost,
                cooldown_key=desire.cooldown_key,
                created_at=at,
                expires_at=desire.expires_at,
                status="open",
            )
            if urge_id is not None
            else None
        )
        return {
            "desire_id": desire.id,
            "revision": desire.revision,
            "evaluated_at": at.isoformat(),
            "score": score,
            "outcome": outcome,
            "urge_id": urge_id,
            "would_create_urge": urge is not None,
            "reasons": reasons,
            "review": review,
            "urge": urge,
        }

    @staticmethod
    def _already_reviewed(review: DesireReview) -> dict[str, object]:
        return {
            "desire_id": review.desire_id,
            "revision": review.revision,
            "evaluated_at": review.evaluated_at.isoformat(),
            "score": review.score,
            "outcome": "already_reviewed",
            "review_outcome": review.outcome,
            "urge_id": review.urge_id,
            "would_create_urge": False,
            "reasons": review.reasons,
        }

    @staticmethod
    def _public_result(plan: dict[str, object]) -> dict[str, object]:
        return {
            key: value for key, value in plan.items() if key not in {"review", "urge"}
        }

    def _due_desires(
        self, connection: sqlite3.Connection, at: datetime
    ) -> list[Desire]:
        rows = connection.execute(
            """
            SELECT * FROM desires
            WHERE status = 'open'
              AND next_review_at IS NOT NULL
            """,
        ).fetchall()
        desires = [self.storage._desire_from_row(row) for row in rows]
        due = [
            desire
            for desire in desires
            if desire.next_review_at is not None and desire.next_review_at <= at
        ]
        return sorted(
            due,
            key=lambda desire: (
                desire.next_review_at.astimezone(UTC),
                desire.created_at.astimezone(UTC),
                desire.id,
            ),
        )

    def _existing_review(
        self,
        connection: sqlite3.Connection,
        desire_id: str,
        revision: int,
    ) -> DesireReview | None:
        row = connection.execute(
            """
            SELECT * FROM desire_reviews
            WHERE desire_id = ? AND revision = ?
            """,
            (desire_id, revision),
        ).fetchone()
        return self.storage._review_from_row(row) if row else None

    @staticmethod
    def _assert_review_time_invariants(
        connection: sqlite3.Connection,
        desire: Desire,
        at: datetime,
    ) -> None:
        if desire.revision == 1:
            revision_started_at = desire.created_at
            boundary_name = "created_at"
        else:
            row = connection.execute(
                """
                SELECT recorded_at FROM desire_progress
                WHERE desire_id = ? AND to_revision = ?
                """,
                (desire.id, desire.revision),
            ).fetchone()
            if row is None:
                raise ValueError(
                    "current Desire revision has no corresponding Progress"
                )
            revision_started_at = datetime.fromisoformat(row["recorded_at"])
            boundary_name = "current revision Progress.recorded_at"
        if desire.next_review_at is None:
            raise ValueError("open Desire requires next_review_at")
        if desire.next_review_at < revision_started_at:
            raise ValueError(f"next_review_at cannot be before {boundary_name}")
        if at < revision_started_at:
            raise ValueError(f"review time cannot be before {boundary_name}")

    def review(
        self,
        *,
        at: datetime,
        dry_run: bool = False,
        fail_after_review: bool = False,
    ) -> list[dict[str, object]]:
        if not isinstance(at, datetime) or at.tzinfo is None:
            raise ValueError("review time must include a timezone")
        if dry_run:
            with self.storage.connect() as connection:
                return self._preview(connection, at)
        if self.storage.read_only:
            raise sqlite3.OperationalError("cannot write read-only storage")
        with TickLock(self.storage.lock_path), self.storage.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                results: list[dict[str, object]] = []
                inserted_reviews = 0
                for desire in self._due_desires(connection, at):
                    self._assert_review_time_invariants(connection, desire, at)
                    existing = self._existing_review(
                        connection, desire.id, desire.revision
                    )
                    if existing is not None:
                        results.append(self._already_reviewed(existing))
                        continue
                    plan = self._evaluate(desire, at)
                    review = plan["review"]
                    urge = plan["urge"]
                    if not isinstance(review, DesireReview):
                        raise TypeError("invalid review plan")
                    if urge is not None:
                        if not isinstance(urge, Urge):
                            raise TypeError("invalid urge plan")
                        connection.execute(
                            """
                            INSERT INTO urges (
                                id, type, reason, urgency, confidence,
                                interruption_cost, cooldown_key, created_at,
                                expires_at, status
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                urge.id,
                                urge.type,
                                urge.reason,
                                urge.urgency,
                                urge.confidence,
                                urge.interruption_cost,
                                urge.cooldown_key,
                                _canonical_timestamp(urge.created_at),
                                (
                                    _canonical_timestamp(urge.expires_at)
                                    if urge.expires_at
                                    else None
                                ),
                                urge.status,
                            ),
                        )
                        connection.execute(
                            """
                            INSERT INTO desire_urge_links (
                                urge_id, desire_id, desire_revision
                            ) VALUES (?, ?, ?)
                            """,
                            (urge.id, desire.id, desire.revision),
                        )
                    if review.outcome == "EXPIRED":
                        cursor = connection.execute(
                            """
                            UPDATE desires SET status = 'expired'
                            WHERE id = ? AND revision = ? AND status = 'open'
                            """,
                            (desire.id, desire.revision),
                        )
                        if cursor.rowcount != 1:
                            raise sqlite3.IntegrityError(
                                "desire changed during expiry review"
                            )
                    connection.execute(
                        """
                        INSERT INTO desire_reviews (
                            id, desire_id, revision, evaluated_at, score,
                            outcome, urge_id, reasons
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            review.id,
                            review.desire_id,
                            review.revision,
                            _canonical_timestamp(review.evaluated_at),
                            review.score,
                            review.outcome,
                            review.urge_id,
                            json.dumps(review.reasons, sort_keys=True),
                        ),
                    )
                    inserted_reviews += 1
                    results.append(self._public_result(plan))
                    if fail_after_review and inserted_reviews == 1:
                        raise sqlite3.OperationalError(
                            "injected desire review transaction failure"
                        )
                connection.commit()
                return results
            except Exception:
                connection.rollback()
                raise

    def _preview(
        self, connection: sqlite3.Connection, at: datetime
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for desire in self._due_desires(connection, at):
            self._assert_review_time_invariants(connection, desire, at)
            existing = self._existing_review(connection, desire.id, desire.revision)
            if existing is not None:
                results.append(self._already_reviewed(existing))
                continue
            results.append(self._public_result(self._evaluate(desire, at)))
        return results
