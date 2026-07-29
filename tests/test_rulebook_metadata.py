"""Tests for versioned rulebook source integrity."""

from __future__ import annotations

import json
from pathlib import Path

from dispute_copilot.intake.hashing import calculate_sha256

PROJECT_ROOT = Path(__file__).parents[1]
RULEBOOK_DIRECTORY = (
    PROJECT_ROOT / "data" / "rulebooks" / "take-home-v1"
)


def test_rulebook_source_matches_recorded_integrity_metadata() -> None:
    metadata_path = RULEBOOK_DIRECTORY / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    source_metadata = metadata["source_documents"][0]
    source_path = RULEBOOK_DIRECTORY / source_metadata["path"]

    assert metadata["rulebook_id"] == "take-home-v1"
    assert source_path.is_file()
    assert source_metadata["mime_type"] == "application/pdf"
    assert source_metadata["size_bytes"] == source_path.stat().st_size
    assert source_metadata["sha256"] == calculate_sha256(source_path)
    assert source_metadata["read_only"] is True
    assert source_metadata["parse_during_case_run"] is False

