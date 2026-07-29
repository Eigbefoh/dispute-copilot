"""Tests for immutable case-run snapshots."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dispute_copilot.domain.case_run import RunStatus
from dispute_copilot.orchestration.service import create_case_run

CASES_PATH = Path(__file__).parents[1] / "data" / "cases.json"
EVIDENCE_DIRECTORY = Path(__file__).parents[1] / "data" / "evidence"


def test_case_run_contains_complete_intake_snapshot() -> None:
    run = create_case_run(
        "CB-2025-0001",
        CASES_PATH,
        EVIDENCE_DIRECTORY,
    )

    assert run.status is RunStatus.READY_FOR_POLICY_SELECTION
    assert run.case_snapshot.case_id == "CB-2025-0001"
    assert run.case_snapshot.transaction.transaction_id == "txn_8821AB"
    assert isinstance(run.evidence_snapshot, tuple)
    assert len(run.evidence_snapshot) == 2
    assert all(artifact.sha256 for artifact in run.evidence_snapshot)
    assert run.created_at.utcoffset() is not None
    assert run.software_version == "0.1.0"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("status", "EXTRACTED"),
        ("case_snapshot", None),
        ("evidence_snapshot", ()),
    ],
)
def test_case_run_intake_fields_cannot_be_reassigned(
    field: str,
    replacement: object,
) -> None:
    run = create_case_run(
        "CB-2025-0001",
        CASES_PATH,
        EVIDENCE_DIRECTORY,
    )

    with pytest.raises(ValidationError, match="Instance is frozen"):
        setattr(run, field, replacement)
