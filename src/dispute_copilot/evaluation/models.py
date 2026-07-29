"""Immutable output contracts for deterministic requirement evaluation."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dispute_copilot.extraction.fact_resolution_models import FactProvenance


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class TruthValue(StrEnum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RequirementStatus(StrEnum):
    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class OverallEvaluationStatus(StrEnum):
    ALL_REQUIREMENTS_SATISFIED = "ALL_REQUIREMENTS_SATISFIED"
    ONE_OR_MORE_REQUIREMENTS_NOT_SATISFIED = (
        "ONE_OR_MORE_REQUIREMENTS_NOT_SATISFIED"
    )
    ONE_OR_MORE_REQUIREMENTS_NOT_ESTABLISHED = (
        "ONE_OR_MORE_REQUIREMENTS_NOT_ESTABLISHED"
    )


class AggregationSummary(StrictFrozenModel):
    mode: Literal["ALL_APPLICABLE", "ANY_TWO"]
    satisfied_count: int = Field(ge=0)
    not_satisfied_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    not_applicable_count: int = Field(ge=0)
    applicable_count: int = Field(ge=0)
    minimum_required: int = Field(ge=1)
    maximum_possible_satisfied: int = Field(ge=0)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> "AggregationSummary":
        if (
            self.satisfied_count
            + self.not_satisfied_count
            + self.unresolved_count
            != self.applicable_count
        ):
            raise ValueError("applicable aggregation counts are inconsistent")
        if self.maximum_possible_satisfied != (
            self.satisfied_count + self.unresolved_count
        ):
            raise ValueError(
                "maximum_possible_satisfied must equal satisfied plus "
                "unresolved"
            )
        return self


class FactBinding(StrictFrozenModel):
    fact_name: str = Field(min_length=1)
    fact_status: Literal[
        "FOUND",
        "NOT_FOUND",
        "AMBIGUOUS",
        "CONFLICTING",
    ]
    value_indexes: tuple[int, ...]
    provenance_references: tuple[FactProvenance, ...]

    @model_validator(mode="after")
    def indexes_are_unique_and_ordered(self) -> "FactBinding":
        if self.value_indexes != tuple(sorted(set(self.value_indexes))):
            raise ValueError(
                "value_indexes must be unique and ascending"
            )
        if any(index < 0 for index in self.value_indexes):
            raise ValueError("value indexes cannot be negative")
        return self


class RequirementEvaluation(StrictFrozenModel):
    requirement_id: str = Field(min_length=1)
    requirement_order: int = Field(ge=1)
    status: RequirementStatus
    predicate_result: TruthValue
    basis_codes: tuple[str, ...] = Field(min_length=1)
    fact_bindings: tuple[FactBinding, ...]
    missing_or_unresolved_facts: tuple[str, ...]


class RequirementEvaluationResult(StrictFrozenModel):
    evaluation_id: UUID
    run_id: UUID
    selection_id: UUID
    resolution_id: UUID
    policy_id: str = Field(min_length=1)
    rulebook_version: str = Field(min_length=1)
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fact_resolution_content_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    predicate_manifest_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    evaluator_version: str = Field(min_length=1)
    predicate_schema_version: str = Field(min_length=1)
    ordered_requirement_results: tuple[
        RequirementEvaluation,
        ...,
    ] = Field(min_length=1)
    aggregation_summary: AggregationSummary | None = None
    overall_status: OverallEvaluationStatus
    status: Literal["READY_FOR_RECOMMENDATION"]
    created_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_complete_result(self) -> "RequirementEvaluationResult":
        orders = tuple(
            item.requirement_order
            for item in self.ordered_requirement_results
        )
        if orders != tuple(
            range(1, len(self.ordered_requirement_results) + 1)
        ):
            raise ValueError(
                "requirement results must be contiguous and ordered"
            )
        ids = tuple(
            item.requirement_id
            for item in self.ordered_requirement_results
        )
        if len(ids) != len(set(ids)):
            raise ValueError("requirement result IDs must be unique")
        if (
            self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise ValueError("created_at must be timezone-aware")
        if self.content_hash != calculate_evaluation_content_hash(self):
            raise ValueError(
                "content_hash does not match evaluation content"
            )
        return self


def calculate_evaluation_content_hash(
    result: RequirementEvaluationResult,
) -> str:
    """Hash semantic output independently of generated ID and time."""
    payload = result.model_dump(
        mode="json",
        exclude={"evaluation_id", "created_at", "content_hash"},
    )
    if payload.get("aggregation_summary") is None:
        payload.pop("aggregation_summary", None)
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
