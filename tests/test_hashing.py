"""Tests for evidence hashing."""

from __future__ import annotations

from pathlib import Path

from dispute_copilot.intake.hashing import calculate_sha256


def test_calculate_sha256_returns_expected_digest(tmp_path: Path) -> None:
    evidence_file = tmp_path / "evidence.pdf"
    evidence_file.write_bytes(b"abc")

    assert calculate_sha256(evidence_file) == (
        "ba7816bf8f01cfea414140de5dae2223"
        "b00361a396177a9cb410ff61f20015ad"
    )


def test_hashing_unchanged_file_returns_same_result(
    tmp_path: Path,
) -> None:
    evidence_file = tmp_path / "evidence.pdf"
    evidence_file.write_bytes(b"unchanged evidence")

    first_hash = calculate_sha256(evidence_file)
    second_hash = calculate_sha256(evidence_file)

    assert first_hash == second_hash


def test_changing_file_changes_its_hash(tmp_path: Path) -> None:
    evidence_file = tmp_path / "evidence.pdf"
    evidence_file.write_bytes(b"original evidence")
    original_hash = calculate_sha256(evidence_file)

    evidence_file.write_bytes(b"changed evidence")
    changed_hash = calculate_sha256(evidence_file)

    assert changed_hash != original_hash
