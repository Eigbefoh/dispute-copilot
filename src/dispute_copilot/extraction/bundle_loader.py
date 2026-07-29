"""Deterministically load evidence admitted by an immutable case run."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from dispute_copilot.domain.case_run import CaseRun
from dispute_copilot.domain.policy_selection import (
    PolicySelection,
    PolicySelectionStatus,
)
from dispute_copilot.extraction.errors import (
    EvidenceFileMissingError,
    EvidenceHashMismatchError,
    EvidenceSizeMismatchError,
    PolicySelectionNotFoundError,
    SelectionCaseRunNotFoundError,
    SelectionNotReadyForExtractionError,
)
from dispute_copilot.extraction.models import (
    EvidenceBundle,
    EvidenceFile,
)
from dispute_copilot.intake.hashing import calculate_sha256

DEFAULT_DATABASE_PATH = Path("data/dispute_copilot.db")


class PolicySelectionReader(Protocol):
    """Read-only selection access required by bundle loading."""

    def get(self, selection_id: UUID) -> PolicySelection | None:
        """Retrieve an immutable policy selection."""
        ...


class CaseRunReader(Protocol):
    """Read-only case-run access required by bundle loading."""

    def get(self, run_id: UUID) -> CaseRun | None:
        """Retrieve an immutable case run."""
        ...


def load_evidence_bundle(
    *,
    selection_id: UUID,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> EvidenceBundle:
    """Load a bundle using only its selection identity."""
    from dispute_copilot.policy.repository import (
        SQLitePolicySelectionRepository,
    )
    from dispute_copilot.repositories.sqlite import (
        SQLiteCaseRunRepository,
    )

    return load_evidence_bundle_with_repositories(
        selection_id=selection_id,
        selection_repository=SQLitePolicySelectionRepository(
            database_path
        ),
        case_run_repository=SQLiteCaseRunRepository(database_path),
    )


def load_evidence_bundle_with_repositories(
    *,
    selection_id: UUID,
    selection_repository: PolicySelectionReader,
    case_run_repository: CaseRunReader,
) -> EvidenceBundle:
    """Internal dependency-injected evidence-bundle implementation."""
    selection = selection_repository.get(selection_id)
    if selection is None:
        raise PolicySelectionNotFoundError(selection_id)
    if selection.status is not PolicySelectionStatus.READY_FOR_EXTRACTION:
        raise SelectionNotReadyForExtractionError(
            f"Policy selection '{selection_id}' has status "
            f"{selection.status}; expected "
            f"{PolicySelectionStatus.READY_FOR_EXTRACTION}"
        )

    run = case_run_repository.get(selection.run_id)
    if run is None:
        raise SelectionCaseRunNotFoundError(selection.run_id)

    evidence_files: list[EvidenceFile] = []
    for artifact in run.evidence_snapshot:
        path = artifact.path
        if not path.is_file():
            raise EvidenceFileMissingError(artifact.filename, path)

        actual_size = path.stat().st_size
        actual_sha256 = calculate_sha256(path)
        if actual_sha256 != artifact.sha256:
            raise EvidenceHashMismatchError(
                artifact.filename,
                artifact.sha256,
                actual_sha256,
            )
        if actual_size != artifact.size_bytes:
            raise EvidenceSizeMismatchError(
                artifact.filename,
                artifact.size_bytes,
                actual_size,
            )

        evidence_files.append(
            EvidenceFile(
                file_name=artifact.filename,
                file_path=path,
                media_type=artifact.mime_type,
                sha256=actual_sha256,
                size_bytes=actual_size,
            )
        )

    return EvidenceBundle(
        run_id=run.run_id,
        selection_id=selection.selection_id,
        policy_id=selection.policy_id,
        evidence_files=tuple(evidence_files),
    )

