"""Tests for registering evidence files."""

from __future__ import annotations

from pathlib import Path

import pytest

from dispute_copilot.intake.evidence_registry import (
    MissingEvidenceError,
    UnsupportedEvidenceError,
    register_evidence,
)


def test_all_declared_files_exist_registration_succeeds(
    tmp_path: Path,
) -> None:
    first_file = tmp_path / "first.pdf"
    second_file = tmp_path / "second.png"
    first_file.write_bytes(b"first")
    second_file.write_bytes(b"second")

    artifacts = register_evidence(
        ["first.pdf", "second.png"],
        tmp_path,
    )

    assert [artifact.filename for artifact in artifacts] == [
        "first.pdf",
        "second.png",
    ]
    assert [artifact.mime_type for artifact in artifacts] == [
        "application/pdf",
        "image/png",
    ]
    assert all(len(artifact.sha256) == 64 for artifact in artifacts)


def test_every_file_receives_a_64_character_sha256(
    tmp_path: Path,
) -> None:
    filenames = ["evidence.pdf", "photo.png", "image.jpg"]
    for filename in filenames:
        (tmp_path / filename).write_bytes(filename.encode())

    artifacts = register_evidence(filenames, tmp_path)

    assert all(len(artifact.sha256) == 64 for artifact in artifacts)
    assert all(
        set(artifact.sha256) <= set("0123456789abcdef")
        for artifact in artifacts
    )


def test_one_declared_file_missing_raises_clear_error(
    tmp_path: Path,
) -> None:
    with pytest.raises(MissingEvidenceError) as caught:
        register_evidence(["missing.pdf"], tmp_path)

    assert caught.value.missing_filenames == ["missing.pdf"]
    assert str(caught.value) == "Missing evidence files: missing.pdf"


def test_empty_evidence_list_returns_empty_result(tmp_path: Path) -> None:
    assert register_evidence([], tmp_path) == []


def test_directory_with_document_name_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "evidence.pdf").mkdir()

    with pytest.raises(
        MissingEvidenceError,
        match=r"Missing evidence files: evidence\.pdf",
    ):
        register_evidence(["evidence.pdf"], tmp_path)


def test_registered_size_matches_actual_file_size(
    tmp_path: Path,
) -> None:
    evidence_file = tmp_path / "evidence.pdf"
    contents = b"evidence-file-contents"
    evidence_file.write_bytes(contents)

    artifact = register_evidence(["evidence.pdf"], tmp_path)[0]

    assert artifact.size_bytes == evidence_file.stat().st_size
    assert artifact.size_bytes == len(contents)


def test_unsupported_evidence_type_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("not supported")

    with pytest.raises(UnsupportedEvidenceError) as caught:
        register_evidence(["notes.txt"], tmp_path)

    assert caught.value.filename == "notes.txt"
    assert caught.value.mime_type == "text/plain"
    assert str(caught.value) == (
        "Unsupported evidence type for 'notes.txt': text/plain"
    )


@pytest.mark.parametrize(
    ("filename", "expected_mime_type"),
    [
        ("document.pdf", "application/pdf"),
        ("screenshot.png", "image/png"),
        ("photo.jpeg", "image/jpeg"),
    ],
)
def test_supported_file_types_are_accepted(
    tmp_path: Path,
    filename: str,
    expected_mime_type: str,
) -> None:
    (tmp_path / filename).write_bytes(b"evidence")

    artifact = register_evidence([filename], tmp_path)[0]

    assert artifact.mime_type == expected_mime_type
