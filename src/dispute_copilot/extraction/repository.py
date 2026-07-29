"""Append-only SQLite persistence for converted evidence documents."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from dispute_copilot.extraction.errors import (
    EvidenceDocumentAlreadyExistsError,
)
from dispute_copilot.extraction.models import EvidenceDocument


class SQLiteEvidenceDocumentRepository:
    """Persist one immutable row per converted source document."""

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
                CREATE TABLE IF NOT EXISTS evidence_documents (
                    document_id TEXT PRIMARY KEY,
                    source_sha256 TEXT NOT NULL,
                    converter_version TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    UNIQUE (source_sha256, converter_version)
                )
                """
            )

    def save(self, document: EvidenceDocument) -> None:
        """Insert a document without replacing an existing cache entry."""
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO evidence_documents (
                        document_id,
                        source_sha256,
                        converter_version,
                        file_name,
                        media_type,
                        created_at,
                        document_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(document.document_id),
                        document.source_sha256,
                        document.converter_version,
                        document.file_name,
                        document.media_type,
                        document.created_at.isoformat(),
                        document.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise EvidenceDocumentAlreadyExistsError(
                document.source_sha256,
                document.converter_version,
            ) from error

    def get(self, document_id: UUID) -> EvidenceDocument | None:
        """Retrieve one immutable converted document by ID."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT document_json
                FROM evidence_documents
                WHERE document_id = ?
                """,
                (str(document_id),),
            ).fetchone()
        if row is None:
            return None
        return EvidenceDocument.model_validate_json(row[0])

    def get_cached(
        self,
        *,
        source_sha256: str,
        converter_version: str,
    ) -> EvidenceDocument | None:
        """Retrieve the unique cached conversion for source and version."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT document_json
                FROM evidence_documents
                WHERE source_sha256 = ?
                  AND converter_version = ?
                """,
                (source_sha256, converter_version),
            ).fetchone()
        if row is None:
            return None
        return EvidenceDocument.model_validate_json(row[0])
