"""Orchestration services for dispute processing runs."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dispute_copilot import __version__
from dispute_copilot.domain.case_run import CaseRun, RunStatus
from dispute_copilot.intake.case_loader import load_case
from dispute_copilot.intake.evidence_registry import register_evidence


def create_case_run(
    case_id: str,
    cases_path: Path,
    evidence_directory: Path,
) -> CaseRun:
    """Create an immutable run after all intake checks succeed."""
    case = load_case(case_id, cases_path)

    evidence = register_evidence(
        filenames=case.merchant_evidence_documents,
        evidence_directory=evidence_directory,
    )

    return CaseRun(
        run_id=uuid4(),
        created_at=datetime.now(timezone.utc),
        status=RunStatus.READY_FOR_POLICY_SELECTION,
        case_snapshot=case,
        evidence_snapshot=tuple(evidence),
        software_version=__version__,
    )
