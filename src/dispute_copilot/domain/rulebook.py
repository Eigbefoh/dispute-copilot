"""Domain records for versioned rulebook sources."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RulebookSource(BaseModel):
    """Immutable provenance for an original rulebook document."""

    model_config = ConfigDict(frozen=True)

    rulebook_version: str = Field(min_length=1)
    document_name: str = Field(min_length=1)
    document_path: Path
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    transcript_path: Path
    created_at: datetime

    @field_validator("document_path", "transcript_path")
    @classmethod
    def require_relative_path(cls, path: Path) -> Path:
        """Reject machine-specific absolute paths."""
        if path.is_absolute():
            raise ValueError("rulebook paths must be relative")
        return path
