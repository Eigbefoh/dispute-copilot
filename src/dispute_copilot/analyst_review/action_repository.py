"""Append-only SQLite persistence for suggested analyst actions."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from uuid import UUID

from dispute_copilot.analyst_review.action_models import (
    SuggestedAnalystAction,
)


class SQLiteSuggestedActionRepository:
    def __init__(
        self,
        database_path: Path,
        *,
        initialize_schema: bool = True,
    ) -> None:
        self.database_path = database_path
        self.initialize_schema = initialize_schema
        if initialize_schema:
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = (
            sqlite3.connect(self.database_path)
            if self.initialize_schema
            else sqlite3.connect(
                f"file:{self.database_path.resolve()}?mode=ro",
                uri=True,
            )
        )
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS suggested_analyst_actions (
                    action_id TEXT PRIMARY KEY,
                    review_package_id TEXT NOT NULL UNIQUE,
                    action TEXT NOT NULL,
                    rules_version TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    FOREIGN KEY (review_package_id)
                        REFERENCES analyst_review_packages(review_package_id)
                )
                """
            )

    def save(
        self,
        action: SuggestedAnalystAction,
    ) -> SuggestedAnalystAction:
        existing = self.get_for_package(action.review_package_id)
        if existing is not None:
            if existing.content_hash != action.content_hash:
                raise ValueError(
                    "review package already has a different suggested action"
                )
            return existing
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO suggested_analyst_actions (
                        action_id,
                        review_package_id,
                        action,
                        rules_version,
                        generated_at,
                        content_hash,
                        action_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(action.action_id),
                        str(action.review_package_id),
                        action.action.value,
                        action.rules_version,
                        action.generated_at.isoformat(),
                        action.content_hash,
                        action.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError(
                "suggested analyst action is append-only"
            ) from error
        return action

    def get_for_package(
        self,
        review_package_id: UUID,
    ) -> SuggestedAnalystAction | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT action_json
                    FROM suggested_analyst_actions
                    WHERE review_package_id = ?
                    """,
                    (str(review_package_id),),
                ).fetchone()
        except sqlite3.OperationalError as error:
            if "no such table" not in str(error):
                raise
            return None
        return (
            None
            if row is None
            else SuggestedAnalystAction.model_validate_json(row[0])
        )
