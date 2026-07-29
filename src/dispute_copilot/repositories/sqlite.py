"""SQLite persistence for immutable case runs."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from dispute_copilot.domain.case_run import CaseRun
from dispute_copilot.domain.rulebook import RulebookSource
from dispute_copilot.repositories.interfaces import (
    RulebookSourceAlreadyExistsError,
    RunAlreadyExistsError,
)


class SQLiteCaseRunRepository:
    """Append-only SQLite repository for complete case-run snapshots."""

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
                CREATE TABLE IF NOT EXISTS case_runs (
                    run_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(case_runs)"
                ).fetchall()
            }
            for column_name in ("case_id", "status", "created_at"):
                if column_name not in columns:
                    connection.execute(
                        f"ALTER TABLE case_runs ADD COLUMN {column_name} TEXT"
                    )

            legacy_rows = connection.execute(
                """
                SELECT run_id, payload_json
                FROM case_runs
                WHERE case_id IS NULL
                   OR status IS NULL
                   OR created_at IS NULL
                """
            ).fetchall()
            for run_id, payload_json in legacy_rows:
                run = CaseRun.model_validate_json(payload_json)
                connection.execute(
                    """
                    UPDATE case_runs
                    SET case_id = ?, status = ?, created_at = ?
                    WHERE run_id = ?
                    """,
                    (
                        run.case_snapshot.case_id,
                        run.status.value,
                        run.created_at.isoformat(),
                        run_id,
                    ),
                )

    def save(self, run: CaseRun) -> None:
        """Insert a new run and reject an existing run ID."""
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO case_runs (
                        run_id,
                        case_id,
                        status,
                        created_at,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(run.run_id),
                        run.case_snapshot.case_id,
                        run.status.value,
                        run.created_at.isoformat(),
                        run.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise RunAlreadyExistsError(run.run_id) from error

    def get(self, run_id: UUID) -> CaseRun | None:
        """Retrieve and validate the complete stored run snapshot."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM case_runs
                WHERE run_id = ?
                """,
                (str(run_id),),
            ).fetchone()

        if row is None:
            return None

        return CaseRun.model_validate_json(row[0])


class SQLiteRulebookSourceRepository:
    """Append-only SQLite repository for rulebook source provenance."""

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
                CREATE TABLE IF NOT EXISTS rulebook_sources (
                    rulebook_version TEXT PRIMARY KEY,
                    document_name TEXT NOT NULL,
                    document_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    transcript_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def save(self, source: RulebookSource) -> None:
        """Insert source provenance and reject an existing version."""
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO rulebook_sources (
                        rulebook_version,
                        document_name,
                        document_path,
                        source_sha256,
                        transcript_path,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source.rulebook_version,
                        source.document_name,
                        str(source.document_path),
                        source.source_sha256,
                        str(source.transcript_path),
                        source.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise RulebookSourceAlreadyExistsError(
                source.rulebook_version
            ) from error

    def get(self, rulebook_version: str) -> RulebookSource | None:
        """Retrieve rulebook source provenance by version."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    rulebook_version,
                    document_name,
                    document_path,
                    source_sha256,
                    transcript_path,
                    created_at
                FROM rulebook_sources
                WHERE rulebook_version = ?
                """,
                (rulebook_version,),
            ).fetchone()

        if row is None:
            return None

        return RulebookSource(
            rulebook_version=row[0],
            document_name=row[1],
            document_path=row[2],
            source_sha256=row[3],
            transcript_path=row[4],
            created_at=row[5],
        )
