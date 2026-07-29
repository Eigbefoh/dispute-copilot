"""Tests for the canonical rulebook transcription."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from dispute_copilot.intake.hashing import calculate_sha256

PROJECT_ROOT = Path(__file__).parents[1]
RULEBOOK_DIRECTORY = (
    PROJECT_ROOT / "data" / "rulebooks" / "take-home-v1"
)
TRANSCRIPT_PATH = (
    RULEBOOK_DIRECTORY / "transcript" / "canonical-transcript.json"
)


def load_transcript() -> dict:
    return json.loads(TRANSCRIPT_PATH.read_text(encoding="utf-8"))


def test_transcript_is_bound_to_immutable_source() -> None:
    transcript = load_transcript()
    source_path = RULEBOOK_DIRECTORY / "source" / "reason-codes.pdf"

    assert transcript["document_id"] == "reason-codes-take-home-v1"
    assert transcript["rulebook_version"] == "take-home-v1"
    assert transcript["source_file"] == source_path.name
    assert transcript["source_sha256"] == calculate_sha256(source_path)


def test_transcript_has_complete_unique_page_line_ids() -> None:
    transcript = load_transcript()
    lines = transcript["lines"]
    lines_by_page: dict[int, list[dict]] = defaultdict(list)
    for line in lines:
        lines_by_page[line["page"]].append(line)

    assert transcript["page_count"] == 7
    assert transcript["line_count"] == len(lines) == 111
    assert set(lines_by_page) == set(range(1, 8))
    assert len({line["line_id"] for line in lines}) == len(lines)

    for page, page_lines in lines_by_page.items():
        assert [line["line_id"] for line in page_lines] == [
            f"P{page:03d}-L{line_number:03d}"
            for line_number in range(1, len(page_lines) + 1)
        ]


def test_transcript_preserves_representative_source_content() -> None:
    lines = {
        line["line_id"]: line["text"]
        for line in load_transcript()["lines"]
    }

    assert lines["P002-L014"] == (
        "1. Proof of delivery: tracking number, carrier name, and "
        "confirmation of delivery to the cardholder's address"
    )
    assert lines["P006-L004"] == (
        "13.1 | Visa | Not received | Represent with delivery proof"
    )
    assert lines["P007-L002"] == (
        "4870 | Mastercard | Chip liability shift | "
        "Accept (card-present, terminal issue)"
    )


def test_transcript_lines_are_normalized_and_nonempty() -> None:
    for line in load_transcript()["lines"]:
        text = line["text"]
        assert text
        assert text == text.strip()
        assert "\n" not in text
        assert "\u200b" not in text
        assert "\ue09d" not in text


def test_rulebook_metadata_records_transcript_integrity() -> None:
    metadata = json.loads(
        (RULEBOOK_DIRECTORY / "metadata.json").read_text(
            encoding="utf-8"
        )
    )
    transcript_metadata = metadata["canonical_transcript"]

    assert (
        transcript_metadata["sha256"]
        == calculate_sha256(TRANSCRIPT_PATH)
    )
    assert transcript_metadata["source_sha256"] == metadata[
        "source_documents"
    ][0]["sha256"]
    assert transcript_metadata["page_count"] == 7
    assert transcript_metadata["line_count"] == 111

