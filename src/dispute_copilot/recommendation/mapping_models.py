"""Frozen declarative recommendation-mapping contracts."""

from __future__ import annotations

from enum import StrEnum
from hashlib import sha256
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dispute_copilot.evaluation.models import OverallEvaluationStatus
from dispute_copilot.evaluation.models import RequirementStatus


class Recommendation(StrEnum):
    RECOMMEND_REPRESENT = "RECOMMEND_REPRESENT"
    RECOMMEND_DO_NOT_REPRESENT = "RECOMMEND_DO_NOT_REPRESENT"
    INSUFFICIENT_EVIDENCE_TO_REPRESENT = (
        "INSUFFICIENT_EVIDENCE_TO_REPRESENT"
    )


class RecommendationBasisCode(StrEnum):
    ALL_MANDATORY_REQUIREMENTS_SATISFIED = (
        "ALL_MANDATORY_REQUIREMENTS_SATISFIED"
    )
    MANDATORY_REQUIREMENT_NOT_SATISFIED = (
        "MANDATORY_REQUIREMENT_NOT_SATISFIED"
    )
    MANDATORY_REQUIREMENT_NOT_ESTABLISHED = (
        "MANDATORY_REQUIREMENT_NOT_ESTABLISHED"
    )
    MISCODING_EXCEPTION_ESTABLISHED = (
        "MISCODING_EXCEPTION_ESTABLISHED"
    )
    DEFAULT_ACCEPT_LIABILITY_UNLESS_EXCEPTION_ESTABLISHED = (
        "DEFAULT_ACCEPT_LIABILITY_UNLESS_EXCEPTION_ESTABLISHED"
    )


class RecommendationMappingRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    priority: int = Field(ge=1)
    when_overall_evaluation_status: OverallEvaluationStatus
    produce: Recommendation
    basis_code: RecommendationBasisCode
    when_policy_ids: tuple[str, ...] = ()
    when_rulebook_versions: tuple[str, ...] = ()
    causal_requirement_statuses: tuple[RequirementStatus, ...] = ()

    @model_validator(mode="after")
    def scope_is_complete(self) -> "RecommendationMappingRule":
        if bool(self.when_policy_ids) != bool(
            self.when_rulebook_versions
        ):
            raise ValueError(
                "policy-scoped mapping rules require both policy IDs "
                "and rulebook versions"
            )
        if len(self.when_policy_ids) != len(set(self.when_policy_ids)):
            raise ValueError("mapping policy IDs must be unique")
        if len(self.when_rulebook_versions) != len(
            set(self.when_rulebook_versions)
        ):
            raise ValueError("mapping rulebook versions must be unique")
        if len(self.causal_requirement_statuses) != len(
            set(self.causal_requirement_statuses)
        ):
            raise ValueError(
                "causal requirement statuses must be unique"
            )
        return self


class RecommendationMappingManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mapping_key: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    mapping_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
    )
    mapping_schema_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
    )
    ordered_rules: tuple[RecommendationMappingRule, ...] = Field(
        min_length=1
    )
    integrity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def rules_are_complete_ordered_and_unique(
        self,
    ) -> "RecommendationMappingManifest":
        priorities = tuple(rule.priority for rule in self.ordered_rules)
        if priorities != tuple(range(1, len(self.ordered_rules) + 1)):
            raise ValueError(
                "mapping priorities must be contiguous and ordered"
            )
        fallback = tuple(
            rule
            for rule in self.ordered_rules
            if not rule.when_policy_ids
        )
        statuses = tuple(
            rule.when_overall_evaluation_status for rule in fallback
        )
        if len(statuses) != len(set(statuses)):
            raise ValueError(
                "fallback mapping aggregate conditions must be unique"
            )
        if set(statuses) != set(OverallEvaluationStatus):
            raise ValueError(
                "fallback mapping rules must cover every supported "
                "evaluation aggregate"
            )
        scoped_keys = tuple(
            (
                rule.when_overall_evaluation_status,
                rule.when_policy_ids,
                rule.when_rulebook_versions,
            )
            for rule in self.ordered_rules
            if rule.when_policy_ids
        )
        if len(scoped_keys) != len(set(scoped_keys)):
            raise ValueError("policy-scoped mapping rules must be unique")
        if self.integrity_hash != calculate_mapping_integrity_hash(self):
            raise ValueError(
                "integrity_hash does not match canonical mapping content"
            )
        return self


def calculate_mapping_integrity_hash(
    manifest: RecommendationMappingManifest,
) -> str:
    """Hash canonical manifest content without its circular hash field."""
    payload = manifest.model_dump(
        mode="json",
        exclude={"integrity_hash"},
        exclude_defaults=True,
    )
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
