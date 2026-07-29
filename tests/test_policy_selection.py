"""Tests for deterministic, append-only runtime policy selection."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from dispute_copilot.domain.policy_selection import (
    PolicySelectionStatus,
)
from dispute_copilot.orchestration.service import create_case_run
from dispute_copilot.policy.import_rulebook import import_rulebook
from dispute_copilot.policy.repository import (
    DuplicatePolicySelectionError,
    SQLitePolicyRepository,
    SQLitePolicySelectionRepository,
)
from dispute_copilot.policy.selection_service import (
    CaseRunNotReadyForPolicySelectionError,
    ExactPolicyNotFoundError,
    select_policy_for_run,
    select_policy_for_run_with_repositories,
)
from dispute_copilot.repositories.sqlite import SQLiteCaseRunRepository

PROJECT_ROOT = Path(__file__).parents[1]
CASES_PATH = PROJECT_ROOT / "data" / "cases.json"
EVIDENCE_DIRECTORY = PROJECT_ROOT / "data" / "evidence"
RULEBOOK_DIRECTORY = (
    PROJECT_ROOT / "data" / "rulebooks" / "take-home-v1"
)


def prepare_selection_database(
    database_path: Path,
    case_id: str = "CB-2025-0001",
):
    case_run_repository = SQLiteCaseRunRepository(database_path)
    run = create_case_run(
        case_id,
        CASES_PATH,
        EVIDENCE_DIRECTORY,
    )
    case_run_repository.save(run)
    import_rulebook(RULEBOOK_DIRECTORY, database_path)
    return run, case_run_repository


def test_visa_13_1_is_selected_by_exact_key(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    run, _ = prepare_selection_database(database_path)
    policy_repository = SQLitePolicyRepository(database_path)
    imported_policy = policy_repository.get_imported(
        "take-home-v1",
        "visa",
        "13.1",
    )

    selection = select_policy_for_run(
        run.run_id,
        "take-home-v1",
        database_path,
    )

    assert imported_policy is not None
    assert selection.run_id == run.run_id
    assert selection.rulebook_version == "take-home-v1"
    assert selection.scheme == "visa"
    assert selection.reason_code == "13.1"
    assert selection.policy_id == "visa-13.1-take-home-v1"
    assert selection.policy_sha256 == imported_policy.policy_sha256
    assert selection.policy_snapshot == imported_policy.policy
    assert (
        selection.status
        is PolicySelectionStatus.READY_FOR_EXTRACTION
    )


def test_mastercard_4837_selects_without_selector_changes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    run, _ = prepare_selection_database(
        database_path,
        "CB-2025-0003",
    )

    selection = select_policy_for_run(
        run.run_id,
        "take-home-v1",
        database_path,
    )

    assert selection.scheme == "mastercard"
    assert selection.reason_code == "4837"
    assert selection.policy_id == "mastercard-4837-take-home-v1"
    assert (
        selection.status
        is PolicySelectionStatus.READY_FOR_EXTRACTION
    )


def test_visa_13_3_selects_before_evidence_extraction(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    run, _ = prepare_selection_database(
        database_path,
        "CB-2025-0006",
    )

    selection = select_policy_for_run(
        run.run_id,
        "take-home-v1",
        database_path,
    )

    assert selection.scheme == "visa"
    assert selection.reason_code == "13.3"
    assert selection.policy_id == "visa-13.3-take-home-v1"
    assert (
        selection.status
        is PolicySelectionStatus.READY_FOR_EXTRACTION
    )


def test_selection_round_trip_preserves_complete_policy_snapshot(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    run, case_run_repository = prepare_selection_database(database_path)
    selection_repository = SQLitePolicySelectionRepository(database_path)

    original = select_policy_for_run_with_repositories(
        run.run_id,
        "take-home-v1",
        case_run_repository,
        SQLitePolicyRepository(database_path),
        selection_repository,
    )
    retrieved = SQLitePolicySelectionRepository(
        database_path
    ).get_for_run(run.run_id)

    assert retrieved == original
    assert retrieved is not None
    assert retrieved.policy_snapshot == original.policy_snapshot
    assert retrieved.policy_sha256 == original.policy_sha256


def test_selection_leaves_original_case_run_unchanged(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    run, case_run_repository = prepare_selection_database(database_path)
    with sqlite3.connect(database_path) as connection:
        payload_before = connection.execute(
            "SELECT payload_json FROM case_runs WHERE run_id = ?",
            (str(run.run_id),),
        ).fetchone()

    select_policy_for_run_with_repositories(
        run.run_id,
        "take-home-v1",
        case_run_repository,
        SQLitePolicyRepository(database_path),
        SQLitePolicySelectionRepository(database_path),
    )

    with sqlite3.connect(database_path) as connection:
        payload_after = connection.execute(
            "SELECT payload_json FROM case_runs WHERE run_id = ?",
            (str(run.run_id),),
        ).fetchone()
    assert payload_after == payload_before
    assert case_run_repository.get(run.run_id) == run


def test_unknown_exact_policy_key_fails_closed(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    run, case_run_repository = prepare_selection_database(database_path)
    selection_repository = SQLitePolicySelectionRepository(database_path)

    with pytest.raises(
        ExactPolicyNotFoundError,
        match="missing-version/visa/13.1",
    ):
        select_policy_for_run_with_repositories(
            run.run_id,
            "missing-version",
            case_run_repository,
            SQLitePolicyRepository(database_path),
            selection_repository,
        )

    assert selection_repository.get_for_run(run.run_id) is None


@pytest.mark.parametrize(
    ("scheme", "reason_code"),
    [
        ("mastercard", "9999"),
        ("unknown", "13.1"),
    ],
)
def test_unknown_scheme_or_code_fails_closed(
    tmp_path: Path,
    scheme: str,
    reason_code: str,
) -> None:
    database_path = tmp_path / "test.db"
    run, _ = prepare_selection_database(database_path)
    altered_case = run.case_snapshot.model_copy(
        update={"scheme": scheme, "reason_code": reason_code}
    )
    altered_run = run.model_copy(
        update={"case_snapshot": altered_case}
    )

    class AlteredRunRepository:
        def get(self, run_id):
            return altered_run

    selection_repository = SQLitePolicySelectionRepository(database_path)
    with pytest.raises(ExactPolicyNotFoundError):
        select_policy_for_run_with_repositories(
            run.run_id,
            "take-home-v1",
            AlteredRunRepository(),
            SQLitePolicyRepository(database_path),
            selection_repository,
        )

    assert selection_repository.get_for_run(run.run_id) is None


def test_caller_cannot_override_case_scheme_or_reason_code(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    run, _ = prepare_selection_database(database_path)

    with pytest.raises(TypeError):
        select_policy_for_run(
            run_id=run.run_id,
            rulebook_version="take-home-v1",
            database_path=database_path,
            scheme="mastercard",
            reason_code="4837",
        )


def test_repeating_selection_for_same_run_is_rejected(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    run, case_run_repository = prepare_selection_database(database_path)
    policy_repository = SQLitePolicyRepository(database_path)
    selection_repository = SQLitePolicySelectionRepository(database_path)
    select_policy_for_run_with_repositories(
        run.run_id,
        "take-home-v1",
        case_run_repository,
        policy_repository,
        selection_repository,
    )

    with pytest.raises(DuplicatePolicySelectionError):
        select_policy_for_run_with_repositories(
            run.run_id,
            "take-home-v1",
            case_run_repository,
            policy_repository,
            selection_repository,
        )


def test_policy_selection_is_immutable(tmp_path: Path) -> None:
    database_path = tmp_path / "test.db"
    run, case_run_repository = prepare_selection_database(database_path)
    selection = select_policy_for_run_with_repositories(
        run.run_id,
        "take-home-v1",
        case_run_repository,
        SQLitePolicyRepository(database_path),
        SQLitePolicySelectionRepository(database_path),
    )

    with pytest.raises(ValidationError, match="Instance is frozen"):
        selection.policy_id = "another-policy"
    with pytest.raises(ValidationError, match="Instance is frozen"):
        selection.policy_snapshot = None


def test_run_must_be_ready_for_policy_selection(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    run, case_run_repository = prepare_selection_database(database_path)
    wrong_status_run = run.model_copy(
        update={"status": "READY_FOR_EXTRACTION"}
    )

    class WrongStatusRepository:
        def get(self, run_id):
            return wrong_status_run

    with pytest.raises(CaseRunNotReadyForPolicySelectionError):
        select_policy_for_run_with_repositories(
            run.run_id,
            "take-home-v1",
            WrongStatusRepository(),
            SQLitePolicyRepository(database_path),
            SQLitePolicySelectionRepository(database_path),
        )


def test_selection_does_not_open_pdf_or_evidence_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "test.db"
    run, case_run_repository = prepare_selection_database(database_path)

    def reject_file_access(*args, **kwargs):
        raise AssertionError("runtime selection accessed a file")

    monkeypatch.setattr(Path, "open", reject_file_access)

    selection = select_policy_for_run_with_repositories(
        run.run_id,
        "take-home-v1",
        case_run_repository,
        SQLitePolicyRepository(database_path),
        SQLitePolicySelectionRepository(database_path),
    )

    assert (
        selection.status
        is PolicySelectionStatus.READY_FOR_EXTRACTION
    )
