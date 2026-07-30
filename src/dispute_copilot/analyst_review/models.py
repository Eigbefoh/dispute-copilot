"""Frozen, UI-ready contracts for analyst review."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dispute_copilot.evaluation.models import RequirementStatus
from dispute_copilot.recommendation.mapping_models import (
    Recommendation,
    RecommendationBasisCode,
)


class StrictFrozenModel(BaseModel):
    """Strict immutable base for persisted review content."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class SupportType(StrEnum):
    SOURCE_TEXT_ASSERTION = "SOURCE_TEXT_ASSERTION"
    VISUAL_MODEL_OBSERVATION = "VISUAL_MODEL_OBSERVATION"
    STRUCTURED_CASE_DATA = "STRUCTURED_CASE_DATA"


class CaseSummary(StrictFrozenModel):
    case_id: str = Field(min_length=1)
    card_scheme: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    policy_key: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    current_workflow_status: Literal["READY_FOR_ANALYST_REVIEW"]


class RecommendationDisplay(StrictFrozenModel):
    recommendation_id: UUID
    decision: Recommendation
    display_label: str = Field(min_length=1)
    basis_codes: tuple[RecommendationBasisCode, ...] = Field(min_length=1)
    causal_requirement_ids: tuple[str, ...] = Field(min_length=1)
    advisory_only: Literal[True]


class AggregationDisplay(StrictFrozenModel):
    mode: Literal["ALL_APPLICABLE", "ANY_TWO"]
    satisfied_count: int = Field(ge=0)
    not_satisfied_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    not_applicable_count: int = Field(ge=0)
    applicable_count: int = Field(ge=0)
    minimum_required: int = Field(ge=1)
    maximum_possible_satisfied: int = Field(ge=0)


class DeterministicRationale(StrictFrozenModel):
    template_key: str = Field(min_length=1)
    template_version: str = Field(min_length=1)
    sentences: tuple[str, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def sentences_are_complete(self) -> "DeterministicRationale":
        if any(not sentence.strip() for sentence in self.sentences):
            raise ValueError("rationale sentences cannot be blank")
        return self


class DisplayFactValue(StrictFrozenModel):
    """A stable display rendering that retains the upstream runtime type."""

    value_type: Literal[
        "STRING",
        "INTEGER",
        "DECIMAL",
        "BOOLEAN",
        "DATE",
        "DATETIME",
    ]
    value: str


class FactDisplayReference(StrictFrozenModel):
    fact_name: str = Field(min_length=1)
    resolution_fact_index: int = Field(ge=0)
    resolution_status: Literal[
        "FOUND",
        "NOT_FOUND",
        "AMBIGUOUS",
        "CONFLICTING",
    ]
    value_indexes: tuple[int, ...]
    values: tuple[DisplayFactValue, ...]


class RequirementChecklistItem(StrictFrozenModel):
    requirement_id: str = Field(min_length=1)
    requirement_order: int = Field(ge=1)
    requirement_text: str = Field(min_length=1)
    status: RequirementStatus
    evaluation_basis_codes: tuple[str, ...] = Field(min_length=1)
    supporting_fact_refs: tuple[FactDisplayReference, ...]
    unresolved_fact_refs: tuple[FactDisplayReference, ...]
    source_refs: tuple[str, ...]


class EvidenceGapItem(StrictFrozenModel):
    gap_id: str = Field(min_length=1)
    requirement_id: str = Field(min_length=1)
    fact_name: str = Field(min_length=1)
    display_label: str = Field(min_length=1)
    resolution_status: Literal[
        "NOT_FOUND",
        "AMBIGUOUS",
        "CONFLICTING",
    ]
    blocking_effect: str = Field(min_length=1)


class SupportingSourceItem(StrictFrozenModel):
    source_ref_id: str = Field(min_length=1)
    fact_name: str = Field(min_length=1)
    fact_value_index: int = Field(ge=0)
    source_document: str | None
    page_number: int | None = Field(default=None, ge=1)
    extraction_route: Literal[
        "CASE_METADATA",
        "EVIDENCE_EXTRACTION",
    ]
    extraction_method: Literal[
        "DETERMINISTIC_METADATA",
        "NATIVE_TEXT",
        "OCR",
        "VISION",
    ]
    support_type: SupportType
    supporting_content: str = Field(min_length=1)

    @model_validator(mode="after")
    def source_shape_matches_type(self) -> "SupportingSourceItem":
        if self.support_type is SupportType.STRUCTURED_CASE_DATA:
            if self.source_document is not None or self.page_number is not None:
                raise ValueError(
                    "structured case data cannot cite a document page"
                )
        elif self.source_document is None or self.page_number is None:
            raise ValueError(
                "evidence support requires a source document and page"
            )
        return self


class ReviewLineage(StrictFrozenModel):
    run_id: UUID
    selection_id: UUID
    fact_resolution_id: UUID
    evaluation_id: UUID
    recommendation_id: UUID
    case_run_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_selection_content_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    policy_integrity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    fact_resolution_content_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    evaluation_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    recommendation_content_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    presentation_rules_integrity_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )


class AnalystReviewPackage(StrictFrozenModel):
    review_package_id: UUID
    schema_version: Literal["1.0.0"]
    presentation_rules_key: str = Field(min_length=1)
    presentation_rules_version: str = Field(min_length=1)
    case_summary: CaseSummary
    recommendation: RecommendationDisplay
    aggregation: AggregationDisplay | None = None
    rationale: DeterministicRationale
    requirement_checklist: tuple[RequirementChecklistItem, ...] = Field(
        min_length=1
    )
    evidence_gaps: tuple[EvidenceGapItem, ...]
    supporting_sources: tuple[SupportingSourceItem, ...]
    limitations: tuple[str, ...] = Field(min_length=1)
    lineage: ReviewLineage
    created_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_complete_package(self) -> "AnalystReviewPackage":
        orders = tuple(
            item.requirement_order for item in self.requirement_checklist
        )
        if orders != tuple(range(1, len(orders) + 1)):
            raise ValueError(
                "requirement checklist must be contiguous and ordered"
            )
        requirement_ids = tuple(
            item.requirement_id for item in self.requirement_checklist
        )
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("requirement checklist IDs must be unique")
        gap_ids = tuple(item.gap_id for item in self.evidence_gaps)
        if len(gap_ids) != len(set(gap_ids)):
            raise ValueError("evidence gap IDs must be unique")
        source_ids = tuple(
            item.source_ref_id for item in self.supporting_sources
        )
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("supporting source IDs must be unique")
        if (
            self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise ValueError("created_at must be timezone-aware")
        if self.content_hash != calculate_review_package_content_hash(self):
            raise ValueError(
                "content_hash does not match analyst-review package content"
            )
        return self


def calculate_review_package_content_hash(
    package: AnalystReviewPackage,
) -> str:
    """Hash semantic display content independently of generated ID/time."""
    payload = package.model_dump(
        mode="json",
        exclude={"review_package_id", "created_at", "content_hash"},
    )
    if payload.get("aggregation") is None:
        payload.pop("aggregation", None)
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
