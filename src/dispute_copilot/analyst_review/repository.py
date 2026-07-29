"""Append-only persistence for immutable analyst-review packages."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from dispute_copilot.analyst_review.errors import (
    AnalystReviewPersistenceConflictError,
)
from dispute_copilot.analyst_review.models import AnalystReviewPackage


class SQLiteAnalystReviewPackageRepository:
    """Persist materialised UI snapshots without workflow progression."""

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
                CREATE TABLE IF NOT EXISTS analyst_review_packages (
                    review_package_id TEXT PRIMARY KEY,
                    recommendation_id TEXT NOT NULL,
                    selection_id TEXT NOT NULL,
                    presentation_rules_key TEXT NOT NULL,
                    presentation_rules_version TEXT NOT NULL,
                    presentation_rules_integrity_hash TEXT NOT NULL,
                    recommendation_content_hash TEXT NOT NULL,
                    evaluation_content_hash TEXT NOT NULL,
                    fact_resolution_content_hash TEXT NOT NULL,
                    policy_integrity_hash TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    package_json TEXT NOT NULL,
                    UNIQUE (
                        recommendation_id,
                        presentation_rules_key,
                        presentation_rules_version
                    )
                )
                """
            )

    def save(
        self,
        package: AnalystReviewPackage,
    ) -> AnalystReviewPackage:
        """Insert a snapshot or return its identical idempotent replay."""
        existing = self.get_for_recommendation(
            recommendation_id=package.lineage.recommendation_id,
            presentation_rules_key=package.presentation_rules_key,
            presentation_rules_version=package.presentation_rules_version,
        )
        if existing is not None:
            if _same_materialised_input(existing, package):
                if existing.content_hash != package.content_hash:
                    raise AnalystReviewPersistenceConflictError(
                        "Identical review-package inputs produced different "
                        "semantic content"
                    )
                return existing
            raise AnalystReviewPersistenceConflictError(
                "A conflicting analyst-review package already exists for "
                "the recommendation and presentation identity"
            )
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO analyst_review_packages (
                        review_package_id,
                        recommendation_id,
                        selection_id,
                        presentation_rules_key,
                        presentation_rules_version,
                        presentation_rules_integrity_hash,
                        recommendation_content_hash,
                        evaluation_content_hash,
                        fact_resolution_content_hash,
                        policy_integrity_hash,
                        content_hash,
                        created_at,
                        package_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(package.review_package_id),
                        str(package.lineage.recommendation_id),
                        str(package.lineage.selection_id),
                        package.presentation_rules_key,
                        package.presentation_rules_version,
                        (
                            package.lineage
                            .presentation_rules_integrity_hash
                        ),
                        package.lineage.recommendation_content_hash,
                        package.lineage.evaluation_content_hash,
                        package.lineage.fact_resolution_content_hash,
                        package.lineage.policy_integrity_hash,
                        package.content_hash,
                        package.created_at.isoformat(),
                        package.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            concurrent = self.get_for_recommendation(
                recommendation_id=package.lineage.recommendation_id,
                presentation_rules_key=package.presentation_rules_key,
                presentation_rules_version=(
                    package.presentation_rules_version
                ),
            )
            if (
                concurrent is not None
                and _same_materialised_input(concurrent, package)
                and concurrent.content_hash == package.content_hash
            ):
                return concurrent
            raise AnalystReviewPersistenceConflictError(
                "Analyst-review package identity conflict"
            ) from error
        return package

    def get(
        self,
        review_package_id: UUID,
    ) -> AnalystReviewPackage | None:
        return self._get_by(
            "review_package_id = ?",
            (str(review_package_id),),
        )

    def get_for_recommendation(
        self,
        *,
        recommendation_id: UUID,
        presentation_rules_key: str,
        presentation_rules_version: str,
    ) -> AnalystReviewPackage | None:
        return self._get_by(
            """
            recommendation_id = ?
            AND presentation_rules_key = ?
            AND presentation_rules_version = ?
            """,
            (
                str(recommendation_id),
                presentation_rules_key,
                presentation_rules_version,
            ),
        )

    def _get_by(
        self,
        predicate: str,
        values: tuple[str, ...],
    ) -> AnalystReviewPackage | None:
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT package_json
                FROM analyst_review_packages
                WHERE {predicate}
                """,
                values,
            ).fetchone()
        if row is None:
            return None
        try:
            return AnalystReviewPackage.model_validate_json(row[0])
        except ValidationError as error:
            raise AnalystReviewPersistenceConflictError(
                "Persisted analyst-review package failed integrity "
                "validation"
            ) from error


def _same_materialised_input(
    first: AnalystReviewPackage,
    second: AnalystReviewPackage,
) -> bool:
    return (
        first.lineage.recommendation_id,
        first.lineage.recommendation_content_hash,
        first.lineage.evaluation_content_hash,
        first.lineage.fact_resolution_content_hash,
        first.lineage.policy_integrity_hash,
        first.presentation_rules_key,
        first.presentation_rules_version,
        first.lineage.presentation_rules_integrity_hash,
    ) == (
        second.lineage.recommendation_id,
        second.lineage.recommendation_content_hash,
        second.lineage.evaluation_content_hash,
        second.lineage.fact_resolution_content_hash,
        second.lineage.policy_integrity_hash,
        second.presentation_rules_key,
        second.presentation_rules_version,
        second.lineage.presentation_rules_integrity_hash,
    )
