"""Versioned recommendation/action mapping and override taxonomy."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from dispute_copilot.human_review.decision_models import (
    AnalystAction,
    AnalystOutcome,
    AnalystWorkflowStatus,
)
from dispute_copilot.human_review.errors import DecisionValidationError
from dispute_copilot.recommendation.mapping_models import Recommendation

DEFAULT_DECISION_RULES_ROOT = Path("data/human-review-rules")
DEFAULT_DECISION_RULES_KEY = "human-review-v1"
DEFAULT_DECISION_RULES_VERSION = "1.0.0"
SUPPORTED_DECISION_RULES_SCHEMA_VERSION = "1.0.0"


class FrozenRulesModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RecommendationOutcomeRule(FrozenRulesModel):
    recommendation: Recommendation
    confirmed_outcome: AnalystOutcome


class OverrideReason(FrozenRulesModel):
    reason_code: str = Field(
        min_length=1,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    display_label: str = Field(min_length=1)


class WorkflowTransitionRule(FrozenRulesModel):
    action: AnalystAction
    outcome: AnalystOutcome
    next_status: AnalystWorkflowStatus


class DecisionRulesManifest(FrozenRulesModel):
    decision_rules_key: str = Field(
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    decision_rules_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$"
    )
    decision_rules_schema_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$"
    )
    recommendation_outcomes: tuple[
        RecommendationOutcomeRule,
        ...,
    ] = Field(min_length=1)
    permitted_outcomes: tuple[AnalystOutcome, ...] = Field(min_length=1)
    override_reasons: tuple[OverrideReason, ...] = Field(min_length=1)
    workflow_transitions: tuple[WorkflowTransitionRule, ...] = Field(
        min_length=1
    )
    integrity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def rules_are_complete_and_unique(self) -> "DecisionRulesManifest":
        recommendations = tuple(
            item.recommendation for item in self.recommendation_outcomes
        )
        if (
            len(recommendations) != len(set(recommendations))
            or set(recommendations) != set(Recommendation)
        ):
            raise ValueError(
                "recommendation outcomes must cover every recommendation"
            )
        if (
            len(self.permitted_outcomes)
            != len(set(self.permitted_outcomes))
            or set(self.permitted_outcomes) != set(AnalystOutcome)
        ):
            raise ValueError(
                "permitted outcomes must cover every analyst outcome"
            )
        reason_codes = tuple(
            item.reason_code for item in self.override_reasons
        )
        if len(reason_codes) != len(set(reason_codes)):
            raise ValueError("override reason codes must be unique")
        transition_keys = tuple(
            (item.action, item.outcome)
            for item in self.workflow_transitions
        )
        if len(transition_keys) != len(set(transition_keys)):
            raise ValueError("workflow transition keys must be unique")
        expected_transitions = {
            (AnalystAction.CONFIRM, AnalystOutcome.REPRESENT),
            (AnalystAction.CONFIRM, AnalystOutcome.ACCEPT_LIABILITY),
            (
                AnalystAction.CONFIRM,
                AnalystOutcome.REQUEST_MORE_EVIDENCE,
            ),
            (AnalystAction.OVERRIDE, AnalystOutcome.REPRESENT),
            (AnalystAction.OVERRIDE, AnalystOutcome.ACCEPT_LIABILITY),
            (
                AnalystAction.OVERRIDE,
                AnalystOutcome.REQUEST_MORE_EVIDENCE,
            ),
            (
                AnalystAction.REQUEST_MORE_EVIDENCE,
                AnalystOutcome.REQUEST_MORE_EVIDENCE,
            ),
        }
        if set(transition_keys) != expected_transitions:
            raise ValueError(
                "workflow transitions do not cover the permitted matrix"
            )
        if self.integrity_hash != calculate_decision_rules_integrity_hash(
            self
        ):
            raise ValueError(
                "integrity_hash does not match canonical decision rules"
            )
        return self


def calculate_decision_rules_integrity_hash(
    manifest: DecisionRulesManifest,
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


def load_decision_rules(
    *,
    decision_rules_key: str,
    decision_rules_version: str,
    decision_rules_root: Path = DEFAULT_DECISION_RULES_ROOT,
) -> DecisionRulesManifest:
    path = (
        decision_rules_root
        / decision_rules_key
        / decision_rules_version
        / "manifest.json"
    )
    try:
        content = path.read_bytes()
    except OSError as error:
        raise DecisionValidationError(
            f"Decision rules are unavailable: {path}"
        ) from error
    try:
        manifest = DecisionRulesManifest.model_validate_json(content)
    except ValidationError as error:
        raise DecisionValidationError(
            f"Decision rules failed validation: {error}"
        ) from error
    if (
        manifest.decision_rules_key != decision_rules_key
        or manifest.decision_rules_version != decision_rules_version
    ):
        raise DecisionValidationError(
            "Resolved decision-rule identity differs from explicit request"
        )
    if (
        manifest.decision_rules_schema_version
        != SUPPORTED_DECISION_RULES_SCHEMA_VERSION
    ):
        raise DecisionValidationError(
            "Unsupported decision-rules schema version"
        )
    return manifest
