"""Append-only persistence for authoritative fact-resolution results."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from dispute_copilot.extraction.fact_resolution_models import (
    FactResolutionResult,
)


class FactResolutionConflictError(Exception):
    """Raised when immutable resolution identity or lineage conflicts."""


class SQLiteFactResolutionRepository:
    """Persist one authoritative immutable fact result per selection."""

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
                CREATE TABLE IF NOT EXISTS fact_resolution_results (
                    resolution_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    selection_id TEXT NOT NULL UNIQUE,
                    policy_id TEXT NOT NULL,
                    rulebook_version TEXT NOT NULL,
                    policy_sha256 TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    result_json TEXT NOT NULL
                )
                """
            )

    def save(self, result: FactResolutionResult) -> None:
        """Insert a result, allowing only an identical idempotent replay."""
        existing = self.get_for_selection(result.selection_id)
        if existing is not None:
            if existing == result:
                return
            raise FactResolutionConflictError(
                "A different fact resolution already exists for "
                f"selection '{result.selection_id}'"
            )
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO fact_resolution_results (
                        resolution_id,
                        run_id,
                        selection_id,
                        policy_id,
                        rulebook_version,
                        policy_sha256,
                        content_hash,
                        status,
                        created_at,
                        result_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(result.resolution_id),
                        str(result.run_id),
                        str(result.selection_id),
                        result.policy_id,
                        result.rulebook_version,
                        result.policy_sha256,
                        result.content_hash,
                        result.status,
                        result.created_at.isoformat(),
                        result.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise FactResolutionConflictError(
                "Fact-resolution identity conflict"
            ) from error

    def get(
        self,
        resolution_id: UUID,
    ) -> FactResolutionResult | None:
        """Retrieve and integrity-check a result by ID."""
        return self._get_by("resolution_id", str(resolution_id))

    def get_for_selection(
        self,
        selection_id: UUID,
    ) -> FactResolutionResult | None:
        """Retrieve the authoritative result for one selection."""
        return self._get_by("selection_id", str(selection_id))

    def _get_by(
        self,
        column_name: str,
        value: str,
    ) -> FactResolutionResult | None:
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT result_json
                FROM fact_resolution_results
                WHERE {column_name} = ?
                """,
                (value,),
            ).fetchone()
        if row is None:
            return None
        return FactResolutionResult.model_validate_json(row[0])
