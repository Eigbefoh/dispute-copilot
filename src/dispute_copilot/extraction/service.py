"""Coordinate bundle conversion without policy fact extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from dispute_copilot.extraction.bundle_loader import (
    DEFAULT_DATABASE_PATH,
    load_evidence_bundle,
)
from dispute_copilot.extraction.document_converter import DocumentConverter
from dispute_copilot.extraction.models import (
    EvidenceBundle,
    EvidenceDocument,
)
from dispute_copilot.extraction.providers import (
    OCRProvider,
    VisionProvider,
)
from dispute_copilot.extraction.repository import (
    SQLiteEvidenceDocumentRepository,
)


class EvidenceDocumentRepository(Protocol):
    """Persistence operations required by conversion orchestration."""

    def save(self, document: EvidenceDocument) -> None:
        """Persist one immutable document."""
        ...

    def get_cached(
        self,
        *,
        source_sha256: str,
        converter_version: str,
    ) -> EvidenceDocument | None:
        """Retrieve a source/version cache entry."""
        ...


def convert_evidence_bundle(
    *,
    bundle: EvidenceBundle,
    converter: DocumentConverter,
    repository: EvidenceDocumentRepository,
) -> tuple[EvidenceDocument, ...]:
    """Convert and persist every bundle file in its declared order."""
    documents: list[EvidenceDocument] = []
    for evidence_file in bundle.evidence_files:
        converter_version = converter.converter_version_for(
            evidence_file
        )
        cached = repository.get_cached(
            source_sha256=evidence_file.sha256,
            converter_version=converter_version,
        )
        if cached is not None:
            documents.append(cached)
            continue

        document = converter.convert(evidence_file)
        repository.save(document)
        documents.append(document)

    return tuple(documents)


def convert_selection_documents(
    *,
    selection_id: UUID,
    database_path: Path = DEFAULT_DATABASE_PATH,
    ocr_provider: OCRProvider | None = None,
    vision_provider: VisionProvider | None = None,
) -> tuple[EvidenceDocument, ...]:
    """Load, convert, cache, and persist one selected evidence bundle."""
    bundle = load_evidence_bundle(
        selection_id=selection_id,
        database_path=database_path,
    )
    return convert_evidence_bundle(
        bundle=bundle,
        converter=DocumentConverter(
            ocr_provider=ocr_provider,
            vision_provider=vision_provider,
        ),
        repository=SQLiteEvidenceDocumentRepository(database_path),
    )
