"""Strict contracts for canonical transcripts and executable policies."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

SHA256_PATTERN = r"^[0-9a-f]{64}$"
LINE_ID_PATTERN = re.compile(r"^P(?P<page>\d{3})-L(?P<line>\d{3})$")


class StrictFrozenModel(BaseModel):
    """Base model for immutable rulebook artifacts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class TranscriptLine(StrictFrozenModel):
    """One stable logical line in a canonical transcript."""

    line_id: str = Field(pattern=LINE_ID_PATTERN.pattern)
    page: int = Field(ge=1)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def line_id_matches_page(self) -> TranscriptLine:
        match = LINE_ID_PATTERN.fullmatch(self.line_id)
        if match is None or int(match.group("page")) != self.page:
            raise ValueError("line_id page must match page")
        if self.text != self.text.strip():
            raise ValueError("transcript line text must be trimmed")
        return self


class CanonicalTranscript(StrictFrozenModel):
    """Complete line-addressable transcription of a source document."""

    document_id: str = Field(min_length=1)
    rulebook_version: str = Field(min_length=1)
    source_file: str = Field(min_length=1)
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    page_count: int = Field(ge=1)
    line_count: int = Field(ge=1)
    line_id_convention: str = Field(min_length=1)
    lines: tuple[TranscriptLine, ...]

    @model_validator(mode="after")
    def validate_line_collection(self) -> CanonicalTranscript:
        if self.line_count != len(self.lines):
            raise ValueError("line_count must match the number of lines")

        seen_ids: set[str] = set()
        expected_by_page: dict[int, int] = {}
        previous_page = 0
        for line in self.lines:
            if line.line_id in seen_ids:
                raise ValueError(f"duplicate transcript line: {line.line_id}")
            seen_ids.add(line.line_id)

            if line.page < previous_page:
                raise ValueError("transcript lines must be ordered by page")
            previous_page = line.page

            expected_line = expected_by_page.get(line.page, 1)
            expected_id = f"P{line.page:03d}-L{expected_line:03d}"
            if line.line_id != expected_id:
                raise ValueError(
                    f"expected transcript line {expected_id}, "
                    f"found {line.line_id}"
                )
            expected_by_page[line.page] = expected_line + 1

        if set(expected_by_page) != set(range(1, self.page_count + 1)):
            raise ValueError("transcript must contain every declared page")
        return self


class SourceCitation(StrictFrozenModel):
    """An exact citation into a canonical transcript."""

    document_id: str = Field(min_length=1)
    page: int = Field(ge=1)
    start_line_id: str = Field(pattern=LINE_ID_PATTERN.pattern)
    end_line_id: str = Field(pattern=LINE_ID_PATTERN.pattern)
    exact_quote: str = Field(min_length=1)


class PolicyRequirement(StrictFrozenModel):
    """One deterministic evidence requirement."""

    requirement_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required_facts: tuple[str, ...] = Field(min_length=1)
    source_citation: SourceCitation

    @model_validator(mode="after")
    def required_facts_are_unique(self) -> PolicyRequirement:
        if len(self.required_facts) != len(set(self.required_facts)):
            raise ValueError("required_facts must be unique")
        if any(not fact.strip() for fact in self.required_facts):
            raise ValueError("required_facts cannot contain blank values")
        return self


class PolicyRecord(StrictFrozenModel):
    """A versioned executable reason-code policy."""

    policy_id: str = Field(min_length=1)
    scheme: str = Field(min_length=1, pattern=r"^[a-z]+$")
    reason_code: str = Field(min_length=1)
    rulebook_version: str = Field(min_length=1)
    requirements: tuple[PolicyRequirement, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def requirement_ids_are_unique(self) -> PolicyRecord:
        requirement_ids = [
            requirement.requirement_id
            for requirement in self.requirements
        ]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("requirement_id values must be unique")
        return self


class SourceDocumentMetadata(StrictFrozenModel):
    """Integrity metadata for an immutable source document."""

    filename: str = Field(min_length=1)
    path: Path
    mime_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=SHA256_PATTERN)
    read_only: bool
    parse_during_case_run: bool

    @model_validator(mode="after")
    def source_path_is_portable(self) -> SourceDocumentMetadata:
        _require_safe_relative_path(self.path)
        return self


class TranscriptMetadata(StrictFrozenModel):
    """Integrity metadata for the canonical transcript."""

    path: Path
    sha256: str = Field(pattern=SHA256_PATTERN)
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    page_count: int = Field(ge=1)
    line_count: int = Field(ge=1)

    @model_validator(mode="after")
    def transcript_path_is_portable(self) -> TranscriptMetadata:
        _require_safe_relative_path(self.path)
        return self


class PolicyMetadata(StrictFrozenModel):
    """Integrity metadata for one executable policy artifact."""

    policy_id: str = Field(min_length=1)
    path: Path
    sha256: str = Field(pattern=SHA256_PATTERN)
    transcript_sha256: str = Field(pattern=SHA256_PATTERN)
    fact_catalogue_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )

    @model_validator(mode="after")
    def policy_path_is_portable(self) -> PolicyMetadata:
        _require_safe_relative_path(self.path)
        return self


class FactCatalogueMetadata(StrictFrozenModel):
    """Integrity metadata for a reviewed fact catalogue."""

    path: Path
    sha256: str = Field(pattern=SHA256_PATTERN)
    fact_count: int = Field(ge=1)

    @model_validator(mode="after")
    def catalogue_path_is_portable(self) -> FactCatalogueMetadata:
        _require_safe_relative_path(self.path)
        return self


class FactReviewMetadata(StrictFrozenModel):
    """Integrity metadata for the completed semantic audit."""

    path: Path
    sha256: str = Field(pattern=SHA256_PATTERN)
    row_count: int = Field(ge=1)

    @model_validator(mode="after")
    def review_path_is_portable(self) -> FactReviewMetadata:
        _require_safe_relative_path(self.path)
        return self


class RulebookMetadata(StrictFrozenModel):
    """Manifest for one immutable rulebook version."""

    rulebook_id: str = Field(min_length=1)
    source_documents: tuple[SourceDocumentMetadata, ...] = Field(
        min_length=1
    )
    canonical_transcript: TranscriptMetadata
    policies: tuple[PolicyMetadata, ...] = Field(min_length=1)
    fact_catalogue: FactCatalogueMetadata | None = None
    fact_review: FactReviewMetadata | None = None

    @model_validator(mode="after")
    def reviewed_fact_artifacts_are_paired(self) -> RulebookMetadata:
        if (self.fact_catalogue is None) != (self.fact_review is None):
            raise ValueError(
                "fact_catalogue and fact_review must be declared together"
            )
        return self


def _require_safe_relative_path(path: Path) -> None:
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("rulebook artifact paths must be safe and relative")


def validate_policy_citations(
    policy: PolicyRecord,
    transcript: CanonicalTranscript,
) -> None:
    """Require every policy quote to resolve exactly in the transcript."""
    indexed_lines = {
        line.line_id: (index, line)
        for index, line in enumerate(transcript.lines)
    }

    for requirement in policy.requirements:
        citation = requirement.source_citation
        if citation.document_id != transcript.document_id:
            raise ValueError(
                f"{requirement.requirement_id} cites unknown document "
                f"{citation.document_id}"
            )
        if citation.start_line_id not in indexed_lines:
            raise ValueError(
                f"{requirement.requirement_id} cites missing start line "
                f"{citation.start_line_id}"
            )
        if citation.end_line_id not in indexed_lines:
            raise ValueError(
                f"{requirement.requirement_id} cites missing end line "
                f"{citation.end_line_id}"
            )

        start_index, start_line = indexed_lines[citation.start_line_id]
        end_index, end_line = indexed_lines[citation.end_line_id]
        if start_index > end_index:
            raise ValueError(
                f"{requirement.requirement_id} citation range is reversed"
            )

        cited_lines = transcript.lines[start_index : end_index + 1]
        if (
            start_line.page != citation.page
            or end_line.page != citation.page
            or any(line.page != citation.page for line in cited_lines)
        ):
            raise ValueError(
                f"{requirement.requirement_id} citation page does not match"
            )

        resolved_quote = " ".join(line.text for line in cited_lines)
        if resolved_quote != citation.exact_quote:
            raise ValueError(
                f"{requirement.requirement_id} exact_quote does not match "
                "the canonical transcript"
            )
