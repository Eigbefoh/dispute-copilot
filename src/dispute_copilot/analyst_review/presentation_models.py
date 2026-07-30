"""Frozen presentation rules and deterministic rationale templates."""

from __future__ import annotations

from hashlib import sha256
import json
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dispute_copilot.recommendation.mapping_models import Recommendation

PLACEHOLDER_PATTERN = re.compile(r"\{([a-z][a-z0-9_]*)\}")
ALLOWED_PLACEHOLDERS = {
    "aggregation_summary",
    "causal_requirement_verb",
    "policy_display_name",
    "satisfied_requirement_summary",
    "causal_requirement_list",
}


class FrozenPresentationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RationaleTemplate(FrozenPresentationModel):
    recommendation: Recommendation
    template_key: str = Field(min_length=1)
    template_version: str = Field(min_length=1)
    sentences: tuple[str, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def placeholders_are_allow_listed(self) -> "RationaleTemplate":
        placeholders = {
            placeholder
            for sentence in self.sentences
            for placeholder in PLACEHOLDER_PATTERN.findall(sentence)
        }
        unsupported = placeholders - ALLOWED_PLACEHOLDERS
        if unsupported:
            raise ValueError(
                "unsupported rationale placeholders: "
                + ", ".join(sorted(unsupported))
            )
        return self


class PresentationRulesManifest(FrozenPresentationModel):
    presentation_rules_key: str = Field(
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    presentation_rules_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$"
    )
    presentation_schema_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$"
    )
    recommendation_display_labels: dict[Recommendation, str]
    evidence_gap_display_labels: dict[str, str]
    rationale_templates: tuple[RationaleTemplate, ...] = Field(
        min_length=1
    )
    limitations: tuple[str, ...] = Field(min_length=1)
    integrity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def rules_are_complete_and_integral(
        self,
    ) -> "PresentationRulesManifest":
        if set(self.recommendation_display_labels) != set(Recommendation):
            raise ValueError(
                "display labels must cover every recommendation"
            )
        recommendations = tuple(
            template.recommendation for template in self.rationale_templates
        )
        if (
            len(recommendations) != len(set(recommendations))
            or set(recommendations) != set(Recommendation)
        ):
            raise ValueError(
                "exactly one rationale template is required per "
                "recommendation"
            )
        if any(not label.strip() for label in self.evidence_gap_display_labels.values()):
            raise ValueError("evidence-gap display labels cannot be blank")
        if self.integrity_hash != calculate_presentation_integrity_hash(
            self
        ):
            raise ValueError(
                "integrity_hash does not match canonical presentation rules"
            )
        return self


def calculate_presentation_integrity_hash(
    manifest: PresentationRulesManifest,
) -> str:
    payload = manifest.model_dump(
        mode="json",
        exclude={"integrity_hash"},
    )
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
