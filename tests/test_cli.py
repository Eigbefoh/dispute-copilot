"""Tests for the Dispute Copilot command-line interface."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import dispute_copilot.cli as cli
import pytest
from dispute_copilot.cli import main
from dispute_copilot.intake.evidence_registry import (
    UnsupportedEvidenceError,
)
from dispute_copilot.policy.import_rulebook import import_rulebook
from dispute_copilot.policy.constants import DEFAULT_RULEBOOK_VERSION
from dispute_copilot.policy.repository import (
    SQLitePolicySelectionRepository,
)
from dispute_copilot.repositories.sqlite import SQLiteCaseRunRepository

PROJECT_ROOT = Path(__file__).parents[1]
RULEBOOK_DIRECTORY = (
    PROJECT_ROOT / "data" / "rulebooks" / "take-home-v1"
)


@pytest.fixture(autouse=True)
def isolated_database(
    monkeypatch,
    tmp_path: Path,
) -> Path:
    database_path = tmp_path / "dispute_copilot.db"
    monkeypatch.setattr(cli, "DATABASE_PATH", database_path)
    return database_path


def test_cli_creates_saves_and_prints_run(
    capsys,
    isolated_database: Path,
) -> None:
    exit_code = main(["CB-2025-0001"])
    output_lines = capsys.readouterr().out.splitlines()

    assert exit_code == 0
    assert output_lines[0].startswith("Run created: ")
    run_id = UUID(output_lines[0].removeprefix("Run created: "))
    assert output_lines[1:] == [
        "Case: CB-2025-0001",
        "Evidence files: 2",
        "Status: READY_FOR_POLICY_SELECTION",
        "Saved to SQLite",
    ]

    saved_run = SQLiteCaseRunRepository(isolated_database).get(run_id)
    assert saved_run is not None
    assert saved_run.run_id == run_id
    assert saved_run.case_snapshot.case_id == "CB-2025-0001"
    assert len(saved_run.evidence_snapshot) == 2


def test_cli_policy_selection_defaults_to_take_home_v4() -> None:
    arguments = cli.build_parser().parse_args(
        [
            "select-policy",
            "00000000-0000-0000-0000-000000000001",
        ]
    )

    assert DEFAULT_RULEBOOK_VERSION == "take-home-v4"
    assert arguments.rulebook_version == DEFAULT_RULEBOOK_VERSION


def test_cli_get_run_retrieves_persisted_snapshot(
    capsys,
    isolated_database: Path,
) -> None:
    assert main(["CB-2025-0001"]) == 0
    intake_lines = capsys.readouterr().out.splitlines()
    run_id = UUID(intake_lines[0].removeprefix("Run created: "))

    exit_code = main(["get-run", str(run_id)])
    output = capsys.readouterr().out
    saved_run = SQLiteCaseRunRepository(isolated_database).get(run_id)

    assert exit_code == 0
    assert saved_run is not None
    assert f"Run: {run_id}" in output
    assert f"Created at: {saved_run.created_at.isoformat()}" in output
    assert "Case: CB-2025-0001" in output
    assert "Evidence files: 2" in output
    for artifact in saved_run.evidence_snapshot:
        assert artifact.filename in output
        assert artifact.sha256 in output
    assert "Status: READY_FOR_POLICY_SELECTION" in output


def test_cli_get_run_reports_unknown_id(
    capsys,
) -> None:
    run_id = UUID("00000000-0000-0000-0000-000000000001")

    exit_code = main(["get-run", str(run_id)])

    assert exit_code == 1
    assert capsys.readouterr().out == f"Run not found: {run_id}\n"


def test_cli_selects_and_persists_exact_policy(
    capsys,
    isolated_database: Path,
) -> None:
    assert main(["CB-2025-0001"]) == 0
    intake_lines = capsys.readouterr().out.splitlines()
    run_id = UUID(intake_lines[0].removeprefix("Run created: "))
    import_rulebook(RULEBOOK_DIRECTORY, isolated_database)

    exit_code = main(
        [
            "select-policy",
            str(run_id),
            "--rulebook-version",
            "take-home-v1",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert f"Run: {run_id}" in output
    assert "Policy: visa-13.1-take-home-v1" in output
    assert "Rulebook: take-home-v1" in output
    assert "Status: READY_FOR_EXTRACTION" in output
    assert "Saved to SQLite" in output

    selection = SQLitePolicySelectionRepository(
        isolated_database
    ).get_for_run(run_id)
    assert selection is not None
    assert selection.policy_snapshot.policy_id == (
        "visa-13.1-take-home-v1"
    )


def test_cli_policy_selection_fails_closed_for_unknown_version(
    capsys,
    isolated_database: Path,
) -> None:
    assert main(["CB-2025-0001"]) == 0
    intake_lines = capsys.readouterr().out.splitlines()
    run_id = UUID(intake_lines[0].removeprefix("Run created: "))
    import_rulebook(RULEBOOK_DIRECTORY, isolated_database)

    exit_code = main(
        [
            "select-policy",
            str(run_id),
            "--rulebook-version",
            "unknown-version",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "No exact policy for unknown-version/visa/13.1" in output
    assert "READY_FOR_EXTRACTION" not in output


def test_cli_verbose_mode_prints_complete_run_json(capsys) -> None:
    exit_code = main(["CB-2025-0001", "--verbose"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert '"run_id":' in output
    assert '"status": "READY_FOR_POLICY_SELECTION"' in output
    assert '"case_snapshot": {' in output
    assert '"case_id": "CB-2025-0001"' in output
    assert '"evidence_snapshot": [' in output
    assert '"mime_type": "application/pdf"' in output
    assert '"sha256":' in output
    assert '"software_version": "0.1.0"' in output


def test_cli_reports_missing_evidence(
    capsys,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cli, "EVIDENCE_DIRECTORY", tmp_path)

    exit_code = main(["CB-2025-0001"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "Status: MISSING_EVIDENCE" in output
    assert "Status: READY_FOR_POLICY_SELECTION" not in output
    assert (
        "Missing: CB-2025-0001_delivery_confirmation.pdf" in output
    )
    assert "Missing: CB-2025-0001_carrier_tracking.pdf" in output


def test_cli_only_marks_ready_after_integrity_checks_pass(
    capsys,
    monkeypatch,
) -> None:
    def reject_unsupported_evidence(*args, **kwargs):
        raise UnsupportedEvidenceError("evidence.txt", "text/plain")

    monkeypatch.setattr(
        cli,
        "create_case_run",
        reject_unsupported_evidence,
    )

    exit_code = main(["CB-2025-0001"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "Status: UNSUPPORTED_EVIDENCE" in output
    assert "Status: READY_FOR_POLICY_SELECTION" not in output
