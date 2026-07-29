"""Tests for controlled rulebook validation and import."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from dispute_copilot.intake.hashing import calculate_sha256
from dispute_copilot.policy.import_rulebook import (
    RulebookImportError,
    import_rulebook,
    main,
    validate_rulebook,
)
from dispute_copilot.policy.repository import (
    ConflictingPolicyKeyError,
    DuplicatePolicyError,
    SQLitePolicyRepository,
)

PROJECT_ROOT = Path(__file__).parents[1]
SOURCE_RULEBOOK = (
    PROJECT_ROOT / "data" / "rulebooks" / "take-home-v1"
)


def copy_rulebook(tmp_path: Path) -> Path:
    destination = tmp_path / "take-home-v1"
    shutil.copytree(SOURCE_RULEBOOK, destination)
    return destination


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, indent=2) + "\n",
        encoding="utf-8",
    )


def refresh_policy_hash(
    rulebook: Path,
    policy_relative_path: str = "policies/visa-13.1.json",
) -> None:
    metadata_path = rulebook / "metadata.json"
    metadata = read_json(metadata_path)
    policy_metadata = next(
        item
        for item in metadata["policies"]
        if item["path"] == policy_relative_path
    )
    policy_path = rulebook / policy_metadata["path"]
    policy_metadata["sha256"] = calculate_sha256(policy_path)
    write_json(metadata_path, metadata)


def refresh_transcript_hash(rulebook: Path) -> None:
    metadata_path = rulebook / "metadata.json"
    metadata = read_json(metadata_path)
    transcript_path = (
        rulebook / metadata["canonical_transcript"]["path"]
    )
    transcript_hash = calculate_sha256(transcript_path)
    metadata["canonical_transcript"]["sha256"] = transcript_hash
    for policy_metadata in metadata["policies"]:
        policy_metadata["transcript_sha256"] = transcript_hash
    write_json(metadata_path, metadata)


def test_imported_policy_round_trips_from_sqlite(
    tmp_path: Path,
) -> None:
    rulebook = copy_rulebook(tmp_path)
    database_path = tmp_path / "test.db"

    validated = import_rulebook(rulebook, database_path)
    original = validated.policies[0]
    retrieved = SQLitePolicyRepository(database_path).get(
        original.rulebook_version,
        original.scheme,
        original.reason_code,
    )

    assert retrieved == original
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT policy_id, policy_sha256, transcript_sha256
            FROM policies
            """
        ).fetchone()
    assert row == (
        original.policy_id,
        validated.policy_hashes[original.policy_id],
        validated.transcript_sha256,
    )


def test_importer_command_reports_success(
    tmp_path: Path,
    capsys,
) -> None:
    rulebook = copy_rulebook(tmp_path)
    database_path = tmp_path / "test.db"

    exit_code = main(
        [str(rulebook), "--database", str(database_path)]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == (
        "Imported rulebook: take-home-v1\n"
        "Policies imported: 8\n"
        f"Database: {database_path}\n"
    )


def test_importer_rejects_source_pdf_hash_mismatch(
    tmp_path: Path,
) -> None:
    rulebook = copy_rulebook(tmp_path)
    metadata_path = rulebook / "metadata.json"
    metadata = read_json(metadata_path)
    metadata["source_documents"][0]["sha256"] = "0" * 64
    write_json(metadata_path, metadata)

    with pytest.raises(
        RulebookImportError,
        match="source PDF reason-codes.pdf SHA-256 mismatch",
    ):
        validate_rulebook(rulebook)


def test_importer_validates_transcript_with_pydantic(
    tmp_path: Path,
) -> None:
    rulebook = copy_rulebook(tmp_path)
    transcript_path = (
        rulebook / "transcript" / "canonical-transcript.json"
    )
    transcript = read_json(transcript_path)
    transcript["line_count"] = 999
    write_json(transcript_path, transcript)
    refresh_transcript_hash(rulebook)

    with pytest.raises(
        RulebookImportError,
        match="line_count must match",
    ):
        validate_rulebook(rulebook)


def test_importer_validates_policy_records_with_pydantic(
    tmp_path: Path,
) -> None:
    rulebook = copy_rulebook(tmp_path)
    policy_path = rulebook / "policies" / "visa-13.1.json"
    policy = read_json(policy_path)
    policy["unexpected_field"] = True
    write_json(policy_path, policy)
    refresh_policy_hash(rulebook)

    with pytest.raises(
        RulebookImportError,
        match="unexpected_field",
    ):
        validate_rulebook(rulebook)


def test_importer_rejects_exact_quote_mismatch_atomically(
    tmp_path: Path,
) -> None:
    rulebook = copy_rulebook(tmp_path)
    policy_path = rulebook / "policies" / "visa-13.1.json"
    policy = read_json(policy_path)
    policy["requirements"][0]["source_citation"][
        "exact_quote"
    ] = "Not the canonical quote"
    write_json(policy_path, policy)
    refresh_policy_hash(rulebook)
    database_path = tmp_path / "test.db"

    with pytest.raises(
        RulebookImportError,
        match="exact_quote does not match",
    ):
        import_rulebook(rulebook, database_path)

    assert not database_path.exists()


def test_importer_rejects_missing_transcript_line(
    tmp_path: Path,
) -> None:
    rulebook = copy_rulebook(tmp_path)
    policy_path = rulebook / "policies" / "visa-13.1.json"
    policy = read_json(policy_path)
    policy["requirements"][0]["source_citation"][
        "start_line_id"
    ] = "P002-L999"
    write_json(policy_path, policy)
    refresh_policy_hash(rulebook)

    with pytest.raises(
        RulebookImportError,
        match="cites missing start line P002-L999",
    ):
        validate_rulebook(rulebook)


def test_importer_rejects_duplicate_policy_key(
    tmp_path: Path,
) -> None:
    rulebook = copy_rulebook(tmp_path)
    database_path = tmp_path / "test.db"
    import_rulebook(rulebook, database_path)

    with pytest.raises(DuplicatePolicyError):
        import_rulebook(rulebook, database_path)


def test_importer_rejects_conflicting_policy_key(
    tmp_path: Path,
) -> None:
    rulebook = copy_rulebook(tmp_path)
    database_path = tmp_path / "test.db"
    import_rulebook(rulebook, database_path)

    policy_path = rulebook / "policies" / "visa-13.1.json"
    policy = read_json(policy_path)
    policy["policy_id"] = "different-policy-id"
    write_json(policy_path, policy)

    metadata_path = rulebook / "metadata.json"
    metadata = read_json(metadata_path)
    policy_metadata = next(
        item
        for item in metadata["policies"]
        if item["path"] == "policies/visa-13.1.json"
    )
    policy_metadata["policy_id"] = "different-policy-id"
    metadata["policies"].sort(
        key=lambda item: item["path"] != "policies/visa-13.1.json"
    )
    write_json(metadata_path, metadata)
    refresh_policy_hash(rulebook)

    with pytest.raises(ConflictingPolicyKeyError):
        import_rulebook(rulebook, database_path)
