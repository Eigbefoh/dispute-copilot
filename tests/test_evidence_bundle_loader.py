"""Tests for deterministic, integrity-checked evidence-bundle loading."""

from __future__ import annotations

import inspect
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from dispute_copilot.extraction.bundle_loader import (
    load_evidence_bundle,
    load_evidence_bundle_with_repositories,
)
from dispute_copilot.extraction.errors import (
    EvidenceFileMissingError,
    EvidenceHashMismatchError,
    PolicySelectionNotFoundError,
    SelectionCaseRunNotFoundError,
    SelectionNotReadyForExtractionError,
)
from dispute_copilot.extraction.models import EvidenceBundle
from dispute_copilot.intake.case_loader import load_case
from dispute_copilot.intake.hashing import calculate_sha256
from dispute_copilot.orchestration.service import create_case_run
from dispute_copilot.policy.import_rulebook import import_rulebook
from dispute_copilot.policy.repository import (
    SQLitePolicySelectionRepository,
)
from dispute_copilot.policy.selection_service import select_policy_for_run
from dispute_copilot.repositories.sqlite import SQLiteCaseRunRepository

PROJECT_ROOT = Path(__file__).parents[1]
CASES_PATH = PROJECT_ROOT / "data" / "cases.json"
SOURCE_EVIDENCE_DIRECTORY = PROJECT_ROOT / "data" / "evidence"
RULEBOOK_DIRECTORY = (
    PROJECT_ROOT / "data" / "rulebooks" / "take-home-v1"
)
CASE_ID = "CB-2025-0001"
EVIDENCE_FILENAMES = [
    "CB-2025-0001_delivery_confirmation.pdf",
    "CB-2025-0001_carrier_tracking.pdf",
]
VISA_13_3_CASE_ID = "CB-2025-0006"
VISA_13_3_EVIDENCE_FILENAMES = [
    "CB-2025-0006_product_listing.pdf",
    "CB-2025-0006_delivery_photo.png",
    "CB-2025-0006_returns_policy.pdf",
]


def prepare_bundle_state(
    tmp_path: Path,
    case_id: str = CASE_ID,
):
    evidence_directory = tmp_path / "evidence"
    evidence_directory.mkdir()
    case = load_case(case_id, CASES_PATH)
    for filename in case.merchant_evidence_documents:
        shutil.copy2(
            SOURCE_EVIDENCE_DIRECTORY / filename,
            evidence_directory / filename,
        )

    database_path = tmp_path / "test.db"
    run = create_case_run(
        case_id,
        CASES_PATH,
        evidence_directory,
    )
    SQLiteCaseRunRepository(database_path).save(run)
    import_rulebook(RULEBOOK_DIRECTORY, database_path)
    selection = select_policy_for_run(
        run.run_id,
        "take-home-v1",
        database_path,
    )
    return database_path, evidence_directory, run, selection


def test_visa_13_3_bundle_loads_all_declared_evidence_in_order(
    tmp_path: Path,
) -> None:
    database_path, _, run, selection = prepare_bundle_state(
        tmp_path,
        VISA_13_3_CASE_ID,
    )

    bundle = load_evidence_bundle(
        selection_id=selection.selection_id,
        database_path=database_path,
    )

    assert selection.policy_id == "visa-13.3-take-home-v1"
    assert [
        evidence_file.file_name
        for evidence_file in bundle.evidence_files
    ] == VISA_13_3_EVIDENCE_FILENAMES
    assert [
        evidence_file.sha256
        for evidence_file in bundle.evidence_files
    ] == [
        artifact.sha256 for artifact in run.evidence_snapshot
    ]
    assert not hasattr(bundle, "facts")
    assert not hasattr(bundle, "conclusions")


def test_visa_13_3_bundle_never_discovers_extra_directory_files(
    tmp_path: Path,
) -> None:
    (
        database_path,
        evidence_directory,
        _,
        selection,
    ) = prepare_bundle_state(tmp_path, VISA_13_3_CASE_ID)
    (evidence_directory / "undeclared-extra.pdf").write_bytes(
        b"must not be discovered"
    )

    bundle = load_evidence_bundle(
        selection_id=selection.selection_id,
        database_path=database_path,
    )

    assert [
        evidence_file.file_name
        for evidence_file in bundle.evidence_files
    ] == VISA_13_3_EVIDENCE_FILENAMES


def test_visa_13_3_loader_only_opens_declared_evidence_for_hashing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import dispute_copilot.extraction.bundle_loader as bundle_loader

    database_path, _, run, selection = prepare_bundle_state(
        tmp_path,
        VISA_13_3_CASE_ID,
    )
    hashed_paths: list[Path] = []

    def recording_sha256(path: Path) -> str:
        hashed_paths.append(path)
        return calculate_sha256(path)

    monkeypatch.setattr(
        bundle_loader,
        "calculate_sha256",
        recording_sha256,
    )

    load_evidence_bundle(
        selection_id=selection.selection_id,
        database_path=database_path,
    )

    assert hashed_paths == [
        artifact.path for artifact in run.evidence_snapshot
    ]
    assert all(
        path.name != "reason-codes.pdf" for path in hashed_paths
    )


def test_loads_ordered_bundle_from_persisted_snapshots(
    tmp_path: Path,
) -> None:
    database_path, _, run, selection = prepare_bundle_state(tmp_path)

    bundle = load_evidence_bundle(
        selection_id=selection.selection_id,
        database_path=database_path,
    )

    assert isinstance(bundle, EvidenceBundle)
    assert bundle.run_id == run.run_id
    assert bundle.selection_id == selection.selection_id
    assert bundle.policy_id == selection.policy_id
    assert bundle.case_transaction_id == (
        run.case_snapshot.transaction.transaction_id
    )
    assert [
        evidence_file.file_name
        for evidence_file in bundle.evidence_files
    ] == EVIDENCE_FILENAMES

    for evidence_file, artifact in zip(
        bundle.evidence_files,
        run.evidence_snapshot,
        strict=True,
    ):
        assert evidence_file.file_path == artifact.path
        assert evidence_file.media_type == artifact.mime_type
        assert evidence_file.sha256 == artifact.sha256
        assert evidence_file.size_bytes == artifact.size_bytes


def test_bundle_and_files_are_immutable(tmp_path: Path) -> None:
    database_path, _, _, selection = prepare_bundle_state(tmp_path)
    bundle = load_evidence_bundle(
        selection_id=selection.selection_id,
        database_path=database_path,
    )

    with pytest.raises(ValidationError, match="Instance is frozen"):
        bundle.policy_id = "different-policy"
    with pytest.raises(ValidationError, match="Instance is frozen"):
        bundle.evidence_files[0].sha256 = "0" * 64


def test_unknown_selection_fails_closed(tmp_path: Path) -> None:
    database_path = tmp_path / "test.db"
    selection_id = uuid4()

    with pytest.raises(
        PolicySelectionNotFoundError,
        match=str(selection_id),
    ):
        load_evidence_bundle(
            selection_id=selection_id,
            database_path=database_path,
        )


def test_selection_must_be_ready_for_extraction(
    tmp_path: Path,
) -> None:
    _, _, run, selection = prepare_bundle_state(tmp_path)
    wrong_status_selection = selection.model_copy(
        update={"status": "NOT_READY"}
    )

    class SelectionRepository:
        def get(self, selection_id):
            return wrong_status_selection

    class CaseRunRepository:
        def get(self, run_id):
            return run

    with pytest.raises(SelectionNotReadyForExtractionError):
        load_evidence_bundle_with_repositories(
            selection_id=selection.selection_id,
            selection_repository=SelectionRepository(),
            case_run_repository=CaseRunRepository(),
        )


def test_selection_must_reference_existing_case_run(
    tmp_path: Path,
) -> None:
    _, _, _, selection = prepare_bundle_state(tmp_path)

    class SelectionRepository:
        def get(self, selection_id):
            return selection

    class MissingCaseRunRepository:
        def get(self, run_id):
            return None

    with pytest.raises(
        SelectionCaseRunNotFoundError,
        match=str(selection.run_id),
    ):
        load_evidence_bundle_with_repositories(
            selection_id=selection.selection_id,
            selection_repository=SelectionRepository(),
            case_run_repository=MissingCaseRunRepository(),
        )


def test_missing_evidence_file_is_rejected(tmp_path: Path) -> None:
    (
        database_path,
        evidence_directory,
        _,
        selection,
    ) = prepare_bundle_state(tmp_path)
    missing_path = evidence_directory / EVIDENCE_FILENAMES[0]
    missing_path.unlink()

    with pytest.raises(EvidenceFileMissingError) as caught:
        load_evidence_bundle(
            selection_id=selection.selection_id,
            database_path=database_path,
        )

    assert caught.value.filename == EVIDENCE_FILENAMES[0]
    assert caught.value.path == missing_path


def test_modified_evidence_file_is_rejected(tmp_path: Path) -> None:
    (
        database_path,
        evidence_directory,
        run,
        selection,
    ) = prepare_bundle_state(tmp_path)
    modified_path = evidence_directory / EVIDENCE_FILENAMES[0]
    contents = bytearray(modified_path.read_bytes())
    contents[0] ^= 1
    modified_path.write_bytes(contents)

    with pytest.raises(EvidenceHashMismatchError) as caught:
        load_evidence_bundle(
            selection_id=selection.selection_id,
            database_path=database_path,
        )

    assert caught.value.filename == EVIDENCE_FILENAMES[0]
    assert caught.value.expected_sha256 == (
        run.evidence_snapshot[0].sha256
    )
    assert caught.value.actual_sha256 != caught.value.expected_sha256


def test_loading_does_not_mutate_persisted_snapshots(
    tmp_path: Path,
) -> None:
    database_path, _, run, selection = prepare_bundle_state(tmp_path)
    case_repository = SQLiteCaseRunRepository(database_path)
    selection_repository = SQLitePolicySelectionRepository(database_path)

    load_evidence_bundle(
        selection_id=selection.selection_id,
        database_path=database_path,
    )

    assert case_repository.get(run.run_id) == run
    assert selection_repository.get(selection.selection_id) == selection


def test_caller_cannot_supply_evidence_identities(
    tmp_path: Path,
) -> None:
    database_path, _, _, selection = prepare_bundle_state(tmp_path)

    with pytest.raises(TypeError):
        load_evidence_bundle(
            selection_id=selection.selection_id,
            database_path=database_path,
            filenames=["attacker-controlled.pdf"],
        )


def test_loader_has_no_parser_ocr_vision_or_ai_dependencies() -> None:
    import dispute_copilot.extraction.bundle_loader as bundle_loader

    source = inspect.getsource(bundle_loader).lower()
    for forbidden_dependency in (
        "pdfplumber",
        "pypdf",
        "fitz",
        "ocr",
        "vision",
        "openai",
    ):
        assert forbidden_dependency not in source
