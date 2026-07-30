"""Atomic, append-only persistence for final analyst decisions."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from dispute_copilot.extraction.bundle_loader import DEFAULT_DATABASE_PATH
from dispute_copilot.human_review.decision_models import AnalystDecision
from dispute_copilot.human_review.errors import (
    DecisionPersistenceConflictError,
)


class SQLiteAnalystDecisionRepository:
    """Persist one immutable human decision and its workflow event."""

    def __init__(
        self,
        database_path: Path = DEFAULT_DATABASE_PATH,
    ) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analyst_decisions (
                    analyst_decision_id TEXT PRIMARY KEY,
                    review_package_id TEXT NOT NULL UNIQUE,
                    recommendation_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    selected_outcome TEXT NOT NULL,
                    review_package_hash TEXT NOT NULL,
                    decided_by TEXT NOT NULL,
                    decided_at TEXT NOT NULL,
                    decision_rules_key TEXT NOT NULL,
                    decision_rules_version TEXT NOT NULL,
                    decision_rules_integrity_hash TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    next_workflow_status TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    decision_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    selection_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL UNIQUE,
                    from_status TEXT NOT NULL,
                    to_status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def save_with_transition(
        self,
        decision: AnalystDecision,
    ) -> AnalystDecision:
        """Commit the decision and its workflow event as one transaction."""
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                replay = self._get_by_connection(
                    connection,
                    "idempotency_key",
                    decision.idempotency_key,
                )
                if replay is not None:
                    if replay.content_hash == decision.content_hash:
                        return replay
                    raise DecisionPersistenceConflictError(
                        "The idempotency key was already used for a "
                        "different analyst decision"
                    )

                existing = self._get_by_connection(
                    connection,
                    "review_package_id",
                    str(decision.review_package_id),
                )
                if existing is not None:
                    raise DecisionPersistenceConflictError(
                        "A final analyst decision already exists for this "
                        "review package"
                    )

                current_status = self._effective_status_in_transaction(
                    connection,
                    decision.lineage.policy_selection_id,
                )
                if current_status != "READY_FOR_ANALYST_REVIEW":
                    raise DecisionPersistenceConflictError(
                        "The case is not READY_FOR_ANALYST_REVIEW"
                    )

                connection.execute(
                    """
                    INSERT INTO analyst_decisions (
                        analyst_decision_id,
                        review_package_id,
                        recommendation_id,
                        action,
                        selected_outcome,
                        review_package_hash,
                        decided_by,
                        decided_at,
                        decision_rules_key,
                        decision_rules_version,
                        decision_rules_integrity_hash,
                        idempotency_key,
                        next_workflow_status,
                        content_hash,
                        decision_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(decision.analyst_decision_id),
                        str(decision.review_package_id),
                        str(decision.recommendation_id),
                        decision.action.value,
                        decision.selected_outcome.value,
                        decision.review_package_hash,
                        decision.decided_by,
                        decision.decided_at.isoformat(),
                        decision.decision_rules_key,
                        decision.decision_rules_version,
                        decision.decision_rules_integrity_hash,
                        decision.idempotency_key,
                        decision.next_workflow_status.value,
                        decision.content_hash,
                        decision.model_dump_json(),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO workflow_events (
                        event_id,
                        run_id,
                        selection_id,
                        source_type,
                        source_id,
                        from_status,
                        to_status,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        str(decision.lineage.case_run_id),
                        str(decision.lineage.policy_selection_id),
                        "ANALYST_DECISION",
                        str(decision.analyst_decision_id),
                        "READY_FOR_ANALYST_REVIEW",
                        decision.next_workflow_status.value,
                        decision.decided_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            replay = self.get_for_idempotency_key(
                decision.idempotency_key
            )
            if (
                replay is not None
                and replay.content_hash == decision.content_hash
            ):
                return replay
            raise DecisionPersistenceConflictError(
                "Analyst decision or workflow-event identity conflict"
            ) from error
        return decision

    def get(
        self,
        analyst_decision_id: UUID,
    ) -> AnalystDecision | None:
        return self._get_by(
            "analyst_decision_id",
            str(analyst_decision_id),
        )

    def get_for_review_package(
        self,
        review_package_id: UUID,
    ) -> AnalystDecision | None:
        return self._get_by(
            "review_package_id",
            str(review_package_id),
        )

    def get_for_idempotency_key(
        self,
        idempotency_key: str,
    ) -> AnalystDecision | None:
        return self._get_by("idempotency_key", idempotency_key)

    def effective_status(self, selection_id: UUID) -> str | None:
        with self._connect() as connection:
            return self._effective_status_in_transaction(
                connection,
                selection_id,
            )

    def _get_by(
        self,
        column: str,
        value: str,
    ) -> AnalystDecision | None:
        with self._connect() as connection:
            return self._get_by_connection(connection, column, value)

    def _get_by_connection(
        self,
        connection: sqlite3.Connection,
        column: str,
        value: str,
    ) -> AnalystDecision | None:
        row = connection.execute(
            f"""
            SELECT decision_json
            FROM analyst_decisions
            WHERE {column} = ?
            """,
            (value,),
        ).fetchone()
        if row is None:
            return None
        try:
            return AnalystDecision.model_validate_json(row[0])
        except ValidationError as error:
            raise DecisionPersistenceConflictError(
                "Persisted analyst decision failed integrity validation"
            ) from error

    @staticmethod
    def _effective_status_in_transaction(
        connection: sqlite3.Connection,
        selection_id: UUID,
    ) -> str | None:
        row = connection.execute(
            """
            SELECT to_status
            FROM workflow_events
            WHERE selection_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (str(selection_id),),
        ).fetchone()
        return None if row is None else row[0]
