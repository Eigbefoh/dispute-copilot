"""Immutable contracts for evidence loading and document conversion."""

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceFile(BaseModel):
    """One integrity-verified file in an extraction bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file_name: str = Field(min_length=1)
    file_path: Path
    media_type: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class EvidenceBundle(BaseModel):
    """Ordered immutable evidence admitted for one selected policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    selection_id: UUID
    policy_id: str = Field(min_length=1)
    evidence_files: tuple[EvidenceFile, ...]


class ExtractionMethod(StrEnum):
    """The source-preserving method used to populate a page."""

    NATIVE_TEXT = "NATIVE_TEXT"
    OCR = "OCR"
    VISION = "VISION"


class EvidencePage(BaseModel):
    """One converted page with its original page identity preserved."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    text: str = Field(min_length=1)
    extraction_method: ExtractionMethod
    image_width: int | None = Field(default=None, ge=1)
    image_height: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def image_dimensions_are_paired(self) -> "EvidencePage":
        """Reject partial image geometry."""
        if (self.image_width is None) != (self.image_height is None):
            raise ValueError(
                "image_width and image_height must be provided together"
            )
        return self


class EvidenceDocument(BaseModel):
    """Immutable source-preserving conversion of one evidence file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: UUID
    file_name: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=1)
    pages: tuple[EvidencePage, ...] = Field(min_length=1)
    converter_version: str = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def pages_are_ordered_and_contiguous(self) -> "EvidenceDocument":
        """Keep citations stable by requiring one-based page order."""
        page_numbers = [page.page_number for page in self.pages]
        expected = list(range(1, len(self.pages) + 1))
        if page_numbers != expected:
            raise ValueError(
                "pages must be ordered and numbered contiguously from 1"
            )
        return self
