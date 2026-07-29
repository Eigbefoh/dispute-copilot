"""Atomic append-only recommendation persistence."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from dispute_copilot.recommendation.errors import (
    RecommendationPersistenceConflictError,
)
from dispute_copilot.recommendation.models import RecommendationResult


class SQLiteRecommendationRepository:
    """Persist one authoritative recommendation per evaluation."""

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
                CREATE TABLE IF NOT EXISTS recommendation_results (
                    recommendation_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    selection_id TEXT NOT NULL UNIQUE,
                    resolution_id TEXT NOT NULL,
                    evaluation_id TEXT NOT NULL UNIQUE,
                    policy_id TEXT NOT NULL,
                    evaluation_content_hash TEXT NOT NULL,
                    mapping_key TEXT NOT NULL,
                    mapping_version TEXT NOT NULL,
                    mapping_integrity_hash TEXT NOT NULL,
                    recommendation TEXT NOT NULL,
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
        result: RecommendationResult,
    ) -> RecommendationResult:
        """Atomically persist result and analyst-review progression."""
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing_row = connection.execute(
                    """
                    SELECT result_json
                    FROM recommendation_results
                    WHERE evaluation_id = ?
                    """,
                    (str(result.evaluation_id),),
                ).fetchone()
                if existing_row is not None:
                    existing = RecommendationResult.model_validate_json(
                        existing_row[0]
                    )
                    if existing == result or _same_inputs(
                        existing,
                        result,
                    ):
                        return existing
                    raise RecommendationPersistenceConflictError(
                        "A conflicting recommendation already exists for "
                        f"evaluation '{result.evaluation_id}'"
                    )

                connection.execute(
                    """
                    INSERT INTO recommendation_results (
                        recommendation_id,
                        run_id,
                        selection_id,
                        resolution_id,
                        evaluation_id,
                        policy_id,
                        evaluation_content_hash,
                        mapping_key,
                        mapping_version,
                        mapping_integrity_hash,
                        recommendation,
                        status,
                        content_hash,
                        created_at,
                        result_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(result.recommendation_id),
                        str(result.run_id),
                        str(result.selection_id),
                        str(result.resolution_id),
                        str(result.evaluation_id),
                        result.policy_id,
                        result.evaluation_content_hash,
                        result.mapping_key,
                        result.mapping_version,
                        result.mapping_integrity_hash,
                        result.recommendation.value,
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
                        str(result.recommendation_id),
                        str(result.run_id),
                        str(result.selection_id),
                        "RECOMMENDATION",
                        str(result.recommendation_id),
                        "READY_FOR_RECOMMENDATION",
                        "READY_FOR_ANALYST_REVIEW",
                        result.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise RecommendationPersistenceConflictError(
                "Recommendation or progression identity conflict"
            ) from error
        return result

    def get(
        self,
        recommendation_id: UUID,
    ) -> RecommendationResult | None:
        return self._get_by("recommendation_id", str(recommendation_id))

    def get_for_evaluation(
        self,
        evaluation_id: UUID,
    ) -> RecommendationResult | None:
        return self._get_by("evaluation_id", str(evaluation_id))

    def get_for_selection(
        self,
        selection_id: UUID,
    ) -> RecommendationResult | None:
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
    ) -> RecommendationResult | None:
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT result_json
                FROM recommendation_results
                WHERE {column} = ?
                """,
                (value,),
            ).fetchone()
        if row is None:
            return None
        return RecommendationResult.model_validate_json(row[0])


def _same_inputs(
    first: RecommendationResult,
    second: RecommendationResult,
) -> bool:
    return (
        first.evaluation_id,
        first.evaluation_content_hash,
        first.mapping_key,
        first.mapping_version,
        first.mapping_integrity_hash,
    ) == (
        second.evaluation_id,
        second.evaluation_content_hash,
        second.mapping_key,
        second.mapping_version,
        second.mapping_integrity_hash,
    )
