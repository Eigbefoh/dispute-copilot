"""Register and verify evidence files declared by a dispute case."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from dispute_copilot.domain.evidence import EvidenceArtifact
from dispute_copilot.intake.hashing import calculate_sha256

SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
}


class MissingEvidenceError(Exception):
    """Raised when one or more declared evidence files are missing."""

    def __init__(self, missing_filenames: list[str]) -> None:
        self.missing_filenames = missing_filenames
        super().__init__(
            f"Missing evidence files: {', '.join(missing_filenames)}"
        )


class UnsupportedEvidenceError(Exception):
    """Raised when a declared evidence file has an unsupported MIME type."""

    def __init__(self, filename: str, mime_type: str) -> None:
        self.filename = filename
        self.mime_type = mime_type
        super().__init__(
            f"Unsupported evidence type for '{filename}': {mime_type}"
        )


def register_evidence(
    filenames: list[str],
    evidence_directory: Path,
) -> list[EvidenceArtifact]:
    """Register evidence files and capture their integrity metadata."""
    missing = [
        filename
        for filename in filenames
        if not (evidence_directory / filename).is_file()
    ]

    if missing:
        raise MissingEvidenceError(missing)

    artifacts = []

    for filename in filenames:
        path = evidence_directory / filename
        mime_type, _ = mimetypes.guess_type(path)
        detected_mime_type = mime_type or "application/octet-stream"

        if detected_mime_type not in SUPPORTED_MIME_TYPES:
            raise UnsupportedEvidenceError(filename, detected_mime_type)

        artifacts.append(
            EvidenceArtifact(
                filename=filename,
                path=path,
                size_bytes=path.stat().st_size,
                mime_type=detected_mime_type,
                sha256=calculate_sha256(path),
            )
        )

    return artifacts
