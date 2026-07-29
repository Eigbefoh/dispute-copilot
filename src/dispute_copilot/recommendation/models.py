"""Immutable recommendation result contracts."""

from __future__ import annotations

from datetime import datetime
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
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


class CausalRequirementResult(StrictFrozenModel):
    requirement_id: str = Field(min_length=1)
    requirement_order: int = Field(ge=1)
    status: RequirementStatus
    evaluation_basis_codes: tuple[str, ...] = Field(min_length=1)


class RecommendationResult(StrictFrozenModel):
    recommendation_id: UUID
    run_id: UUID
    selection_id: UUID
    resolution_id: UUID
    evaluation_id: UUID
    policy_id: str = Field(min_length=1)
    rulebook_version: str = Field(min_length=1)
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_key: str = Field(min_length=1)
    mapping_version: str = Field(min_length=1)
    mapping_schema_version: str = Field(min_length=1)
    mapping_integrity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    recommendation: Recommendation
    basis_codes: tuple[RecommendationBasisCode, ...] = Field(
        min_length=1
    )
    causal_requirement_results: tuple[
        CausalRequirementResult,
        ...,
    ] = Field(min_length=1)
    status: Literal["READY_FOR_ANALYST_REVIEW"]
    created_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_complete_result(self) -> "RecommendationResult":
        orders = tuple(
            item.requirement_order
            for item in self.causal_requirement_results
        )
        if orders != tuple(sorted(set(orders))):
            raise ValueError(
                "causal requirements must be unique and policy ordered"
            )
        ids = tuple(
            item.requirement_id
            for item in self.causal_requirement_results
        )
        if len(ids) != len(set(ids)):
            raise ValueError("causal requirement IDs must be unique")
        if (
            self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise ValueError("created_at must be timezone-aware")
        if self.content_hash != calculate_recommendation_content_hash(self):
            raise ValueError(
                "content_hash does not match recommendation content"
            )
        return self


def calculate_recommendation_content_hash(
    result: RecommendationResult,
) -> str:
    """Hash semantic output independently of generated ID and time."""
    payload = result.model_dump(
        mode="json",
        exclude={"recommendation_id", "created_at", "content_hash"},
    )
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
