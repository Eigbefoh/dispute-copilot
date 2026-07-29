"""Tests linking executable policies to canonical transcript lines."""

from __future__ import annotations

import json
from pathlib import Path

from dispute_copilot.intake.hashing import calculate_sha256

PROJECT_ROOT = Path(__file__).parents[1]
RULEBOOK_DIRECTORY = (
    PROJECT_ROOT / "data" / "rulebooks" / "take-home-v1"
)
TRANSCRIPT_PATH = (
    RULEBOOK_DIRECTORY / "transcript" / "canonical-transcript.json"
)
POLICY_PATH = RULEBOOK_DIRECTORY / "policies" / "visa-13.1.json"
POLICY_PATHS = sorted(
    (RULEBOOK_DIRECTORY / "policies").glob("*.json")
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_visa_13_1_policy_identity() -> None:
    policy = load_json(POLICY_PATH)

    assert policy["policy_id"] == "visa-13.1-take-home-v1"
    assert policy["scheme"] == "visa"
    assert policy["reason_code"] == "13.1"
    assert policy["rulebook_version"] == "take-home-v1"
    assert len(policy["requirements"]) == 4
    assert {
        requirement["requirement_id"]
        for requirement in policy["requirements"]
    } == {"V13.1-R1", "V13.1-R2", "V13.1-R3", "V13.1-R4"}


def test_every_policy_citation_resolves_to_exact_transcript_text() -> None:
    transcript = load_json(TRANSCRIPT_PATH)
    transcript_lines = {
        line["line_id"]: line for line in transcript["lines"]
    }

    for policy_path in POLICY_PATHS:
        policy = load_json(policy_path)
        for requirement in policy["requirements"]:
            citation = requirement["source_citation"]
            start = transcript_lines[citation["start_line_id"]]
            end = transcript_lines[citation["end_line_id"]]

            assert citation["document_id"] == transcript["document_id"]
            assert start["page"] == citation["page"]
            assert end["page"] == citation["page"]

            cited_lines = [
                line["text"]
                for line in transcript["lines"]
                if line["page"] == citation["page"]
                and citation["start_line_id"]
                <= line["line_id"]
                <= citation["end_line_id"]
            ]
            assert " ".join(cited_lines) == citation["exact_quote"]


def test_policy_required_facts_are_nonempty_and_unique() -> None:
    for policy_path in POLICY_PATHS:
        policy = load_json(policy_path)
        for requirement in policy["requirements"]:
            required_facts = requirement["required_facts"]
            assert required_facts
            assert len(required_facts) == len(set(required_facts))
            assert all(required_fact for required_fact in required_facts)


def test_rulebook_metadata_records_policy_integrity() -> None:
    metadata = load_json(RULEBOOK_DIRECTORY / "metadata.json")
    metadata_by_path = {
        policy_metadata["path"]: policy_metadata
        for policy_metadata in metadata["policies"]
    }

    assert set(metadata_by_path) == {
        str(policy_path.relative_to(RULEBOOK_DIRECTORY))
        for policy_path in POLICY_PATHS
    }
    for policy_path in POLICY_PATHS:
        relative_path = str(
            policy_path.relative_to(RULEBOOK_DIRECTORY)
        )
        policy = load_json(policy_path)
        policy_metadata = metadata_by_path[relative_path]
        assert policy_metadata["policy_id"] == policy["policy_id"]
        assert policy_metadata["sha256"] == calculate_sha256(policy_path)
        assert policy_metadata["transcript_sha256"] == metadata[
            "canonical_transcript"
        ]["sha256"]
