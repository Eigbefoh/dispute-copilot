"""Domain models for registered evidence."""

from pathlib import Path

from pydantic import BaseModel, Field


class EvidenceArtifact(BaseModel):
    """The registered identity and integrity metadata of an evidence file."""

    filename: str
    path: Path
    size_bytes: int = Field(ge=0)
    mime_type: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
