"""Tests for case-run orchestration."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from dispute_copilot.domain.case_run import CaseRun
from dispute_copilot.orchestration.service import create_case_run

CASES_PATH = Path(__file__).parents[1] / "data" / "cases.json"
EVIDENCE_DIRECTORY = Path(__file__).parents[1] / "data" / "evidence"


def test_create_case_run_coordinates_complete_intake() -> None:
    run = create_case_run(
        "CB-2025-0001",
        CASES_PATH,
        EVIDENCE_DIRECTORY,
    )

    assert isinstance(run, CaseRun)
    assert isinstance(run.run_id, UUID)
    assert run.case_snapshot.case_id == "CB-2025-0001"
    assert [artifact.filename for artifact in run.evidence_snapshot] == [
        "CB-2025-0001_delivery_confirmation.pdf",
        "CB-2025-0001_carrier_tracking.pdf",
    ]


def test_each_created_case_run_receives_a_unique_id() -> None:
    first_run = create_case_run(
        "CB-2025-0001",
        CASES_PATH,
        EVIDENCE_DIRECTORY,
    )
    second_run = create_case_run(
        "CB-2025-0001",
        CASES_PATH,
        EVIDENCE_DIRECTORY,
    )

    assert first_run.run_id != second_run.run_id

