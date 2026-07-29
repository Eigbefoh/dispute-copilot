"""Tests for immutable case-run persistence in SQLite."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from dispute_copilot.orchestration.service import create_case_run
from dispute_copilot.repositories.interfaces import RunAlreadyExistsError
from dispute_copilot.repositories.sqlite import SQLiteCaseRunRepository

PROJECT_ROOT = Path(__file__).parents[1]
CASES_PATH = PROJECT_ROOT / "data" / "cases.json"
EVIDENCE_DIRECTORY = PROJECT_ROOT / "data" / "evidence"


def make_run(case_id: str = "CB-2025-0001"):
    return create_case_run(
        case_id,
        CASES_PATH,
        EVIDENCE_DIRECTORY,
    )


def test_saved_run_can_be_retrieved_by_id(tmp_path: Path) -> None:
    database_path = tmp_path / "runs.db"
    first_repository = SQLiteCaseRunRepository(database_path)
    run = make_run()

    first_repository.save(run)
    second_repository = SQLiteCaseRunRepository(database_path)

    assert second_repository.get(run.run_id) == run


def test_complete_case_and_evidence_snapshots_are_preserved(
    tmp_path: Path,
) -> None:
    repository = SQLiteCaseRunRepository(tmp_path / "runs.db")
    run = make_run()

    repository.save(run)
    retrieved = repository.get(run.run_id)

    assert retrieved is not None
    assert retrieved.case_snapshot == run.case_snapshot
    assert retrieved.evidence_snapshot == run.evidence_snapshot
    assert [
        artifact.sha256 for artifact in retrieved.evidence_snapshot
    ] == [
        artifact.sha256 for artifact in run.evidence_snapshot
    ]
    assert retrieved.model_dump() == run.model_dump()


def test_get_unknown_run_returns_none(tmp_path: Path) -> None:
    repository = SQLiteCaseRunRepository(tmp_path / "runs.db")

    assert repository.get(uuid4()) is None


def test_saving_same_run_id_twice_is_rejected(tmp_path: Path) -> None:
    repository = SQLiteCaseRunRepository(tmp_path / "runs.db")
    run = make_run()
    repository.save(run)

    with pytest.raises(
        RunAlreadyExistsError,
        match=rf"Case run '{run.run_id}' already exists",
    ):
        repository.save(run)


def test_existing_run_cannot_be_overwritten(tmp_path: Path) -> None:
    repository = SQLiteCaseRunRepository(tmp_path / "runs.db")
    original = make_run()
    replacement = original.model_copy(
        update={"software_version": "999.0.0"},
    )
    repository.save(original)

    with pytest.raises(RunAlreadyExistsError):
        repository.save(replacement)

    assert repository.get(original.run_id) == original


def test_saving_new_run_never_changes_existing_run(
    tmp_path: Path,
) -> None:
    repository = SQLiteCaseRunRepository(tmp_path / "runs.db")
    existing_run = make_run("CB-2025-0001")
    new_run = make_run("CB-2025-0002")
    repository.save(existing_run)
    existing_snapshot = repository.get(existing_run.run_id)

    repository.save(new_run)

    assert repository.get(existing_run.run_id) == existing_snapshot
    assert repository.get(new_run.run_id) == new_run
