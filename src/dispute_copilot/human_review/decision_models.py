"""Immutable analyst command and decision contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dispute_copilot.recommendation.mapping_models import Recommendation

ANALYST_DECISION_SCHEMA_VERSION = "1.0.0"
IDEMPOTENCY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$"


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class AnalystAction(StrEnum):
    CONFIRM = "CONFIRM"
    OVERRIDE = "OVERRIDE"
    REQUEST_MORE_EVIDENCE = "REQUEST_MORE_EVIDENCE"


class AnalystOutcome(StrEnum):
    REPRESENT = "REPRESENT"
    ACCEPT_LIABILITY = "ACCEPT_LIABILITY"
    REQUEST_MORE_EVIDENCE = "REQUEST_MORE_EVIDENCE"


class AnalystWorkflowStatus(StrEnum):
    ANALYST_CONFIRMED_REPRESENT = "ANALYST_CONFIRMED_REPRESENT"
    ANALYST_CONFIRMED_ACCEPT_LIABILITY = (
        "ANALYST_CONFIRMED_ACCEPT_LIABILITY"
    )
    ANALYST_OVERRIDDEN_TO_REPRESENT = (
        "ANALYST_OVERRIDDEN_TO_REPRESENT"
    )
    ANALYST_OVERRIDDEN_TO_ACCEPT_LIABILITY = (
        "ANALYST_OVERRIDDEN_TO_ACCEPT_LIABILITY"
    )
    MORE_EVIDENCE_REQUESTED = "MORE_EVIDENCE_REQUESTED"


class TrustedAnalystIdentity(StrictFrozenModel):
    """Server-resolved identity that is never accepted from a UI command."""

    subject: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def identity_is_trimmed(self) -> "TrustedAnalystIdentity":
        if self.subject != self.subject.strip():
            raise ValueError("analyst identity must be trimmed")
        return self


class ConfirmDecisionCommand(StrictFrozenModel):
    action: Literal[AnalystAction.CONFIRM]
    review_package_id: UUID
    expected_review_package_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    analyst_comment: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
    )
    idempotency_key: str = Field(pattern=IDEMPOTENCY_PATTERN)


class OverrideDecisionCommand(StrictFrozenModel):
    action: Literal[AnalystAction.OVERRIDE]
    review_package_id: UUID
    expected_review_package_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    selected_outcome: AnalystOutcome
    override_reason_code: str = Field(min_length=1, max_length=100)
    analyst_comment: str = Field(min_length=10, max_length=2000)
    idempotency_key: str = Field(pattern=IDEMPOTENCY_PATTERN)


class RequestEvidenceDecisionCommand(StrictFrozenModel):
    action: Literal[AnalystAction.REQUEST_MORE_EVIDENCE]
    review_package_id: UUID
    expected_review_package_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    selected_gap_ids: tuple[str, ...] = Field(min_length=1)
    analyst_comment: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
    )
    idempotency_key: str = Field(pattern=IDEMPOTENCY_PATTERN)

    @model_validator(mode="after")
    def gaps_are_unique(self) -> "RequestEvidenceDecisionCommand":
        if len(self.selected_gap_ids) != len(set(self.selected_gap_ids)):
            raise ValueError("selected gap IDs must be unique")
        return self


AnalystDecisionCommand = (
    ConfirmDecisionCommand
    | OverrideDecisionCommand
    | RequestEvidenceDecisionCommand
)


class AnalystDecisionLineage(StrictFrozenModel):
    case_run_id: UUID
    policy_selection_id: UUID
    fact_resolution_id: UUID
    evaluation_id: UUID
    recommendation_id: UUID
    review_package_id: UUID


class AnalystDecision(StrictFrozenModel):
    analyst_decision_id: UUID
    review_package_id: UUID
    review_package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    recommendation_id: UUID
    system_recommendation: Recommendation
    action: AnalystAction
    selected_outcome: AnalystOutcome
    reason_code: str | None
    analyst_comment: str | None
    selected_gap_ids: tuple[str, ...]
    decided_by: str = Field(min_length=1, max_length=200)
    decided_at: datetime
    decision_rules_key: str = Field(min_length=1)
    decision_rules_version: str = Field(min_length=1)
    decision_rules_integrity_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    idempotency_key: str = Field(pattern=IDEMPOTENCY_PATTERN)
    next_workflow_status: AnalystWorkflowStatus
    lineage: AnalystDecisionLineage
    schema_version: Literal["1.0.0"]
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_complete_decision(self) -> "AnalystDecision":
        if self.review_package_id != self.lineage.review_package_id:
            raise ValueError(
                "decision review-package identity differs from lineage"
            )
        if self.recommendation_id != self.lineage.recommendation_id:
            raise ValueError(
                "decision recommendation identity differs from lineage"
            )
        if self.action is AnalystAction.OVERRIDE:
            if self.reason_code is None or self.analyst_comment is None:
                raise ValueError(
                    "override requires reason code and analyst comment"
                )
        elif self.reason_code is not None:
            raise ValueError("reason_code is reserved for override")
        if self.action is AnalystAction.REQUEST_MORE_EVIDENCE:
            if not self.selected_gap_ids:
                raise ValueError(
                    "request-more-evidence requires selected gaps"
                )
        elif self.selected_gap_ids:
            raise ValueError(
                "selected gaps are reserved for request-more-evidence"
            )
        if len(self.selected_gap_ids) != len(set(self.selected_gap_ids)):
            raise ValueError("selected gap IDs must be unique")
        if self.decided_by != self.decided_by.strip():
            raise ValueError("decided_by must be trimmed")
        if (
            self.decided_at.tzinfo is None
            or self.decided_at.utcoffset() is None
        ):
            raise ValueError("decided_at must be timezone-aware")
        if self.content_hash != calculate_analyst_decision_content_hash(self):
            raise ValueError(
                "content_hash does not match analyst-decision content"
            )
        return self


def calculate_analyst_decision_content_hash(
    decision: AnalystDecision,
) -> str:
    payload = decision.model_dump(
        mode="json",
        exclude={
            "analyst_decision_id",
            "decided_at",
            "content_hash",
        },
    )
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
