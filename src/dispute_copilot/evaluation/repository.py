"""Atomic append-only persistence for requirement evaluations."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from dispute_copilot.evaluation.errors import (
    EvaluationPersistenceConflictError,
)
from dispute_copilot.evaluation.models import RequirementEvaluationResult


class SQLiteEvaluationRepository:
    """Persist evaluation and immutable workflow progression together."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS requirement_evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    selection_id TEXT NOT NULL UNIQUE,
                    resolution_id TEXT NOT NULL UNIQUE,
                    policy_id TEXT NOT NULL,
                    policy_sha256 TEXT NOT NULL,
                    fact_resolution_content_hash TEXT NOT NULL,
                    predicate_manifest_sha256 TEXT NOT NULL,
                    evaluator_version TEXT NOT NULL,
                    predicate_schema_version TEXT NOT NULL,
                    overall_status TEXT NOT NULL,
                    status TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    result_json TEXT NOT NULL
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

    def save_with_progression(
        self,
        result: RequirementEvaluationResult,
    ) -> RequirementEvaluationResult:
        """Atomically insert evaluation and READY progression."""
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing_row = connection.execute(
                    """
                    SELECT result_json
                    FROM requirement_evaluations
                    WHERE selection_id = ?
                    """,
                    (str(result.selection_id),),
                ).fetchone()
                if existing_row is not None:
                    existing = (
                        RequirementEvaluationResult.model_validate_json(
                            existing_row[0]
                        )
                    )
                    if existing == result:
                        return existing
                    if _same_idempotency_inputs(existing, result):
                        return existing
                    raise EvaluationPersistenceConflictError(
                        "A conflicting requirement evaluation already "
                        f"exists for selection '{result.selection_id}'"
                    )

                connection.execute(
                    """
                    INSERT INTO requirement_evaluations (
                        evaluation_id,
                        run_id,
                        selection_id,
                        resolution_id,
                        policy_id,
                        policy_sha256,
                        fact_resolution_content_hash,
                        predicate_manifest_sha256,
                        evaluator_version,
                        predicate_schema_version,
                        overall_status,
                        status,
                        content_hash,
                        created_at,
                        result_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(result.evaluation_id),
                        str(result.run_id),
                        str(result.selection_id),
                        str(result.resolution_id),
                        result.policy_id,
                        result.policy_sha256,
                        result.fact_resolution_content_hash,
                        result.predicate_manifest_sha256,
                        result.evaluator_version,
                        result.predicate_schema_version,
                        result.overall_status.value,
                        result.status,
                        result.content_hash,
                        result.created_at.isoformat(),
                        result.model_dump_json(),
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
                        str(result.evaluation_id),
                        str(result.run_id),
                        str(result.selection_id),
                        "REQUIREMENT_EVALUATION",
                        str(result.evaluation_id),
                        "READY_FOR_EVALUATION",
                        "READY_FOR_RECOMMENDATION",
                        result.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise EvaluationPersistenceConflictError(
                "Evaluation or progression identity conflict"
            ) from error
        return result

    def get(
        self,
        evaluation_id: UUID,
    ) -> RequirementEvaluationResult | None:
        return self._get_by("evaluation_id", str(evaluation_id))

    def get_for_selection(
        self,
        selection_id: UUID,
    ) -> RequirementEvaluationResult | None:
        return self._get_by("selection_id", str(selection_id))

    def effective_status(self, selection_id: UUID) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT to_status
                FROM workflow_events
                WHERE selection_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (str(selection_id),),
            ).fetchone()
        return None if row is None else row[0]

    def _get_by(
        self,
        column: str,
        value: str,
    ) -> RequirementEvaluationResult | None:
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT result_json
                FROM requirement_evaluations
                WHERE {column} = ?
                """,
                (value,),
            ).fetchone()
        if row is None:
            return None
        return RequirementEvaluationResult.model_validate_json(row[0])


def _same_idempotency_inputs(
    first: RequirementEvaluationResult,
    second: RequirementEvaluationResult,
) -> bool:
    return (
        first.selection_id,
        first.fact_resolution_content_hash,
        first.policy_sha256,
        first.predicate_manifest_sha256,
        first.evaluator_version,
        first.predicate_schema_version,
    ) == (
        second.selection_id,
        second.fact_resolution_content_hash,
        second.policy_sha256,
        second.predicate_manifest_sha256,
        second.evaluator_version,
        second.predicate_schema_version,
    )
