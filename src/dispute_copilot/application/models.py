"""Immutable DTOs exposed by the analyst-demo application boundary."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dispute_copilot.analyst_review.models import AnalystReviewPackage
from dispute_copilot.domain.case import Case
from dispute_copilot.extraction.fact_resolution_models import (
    FactResolutionResult,
)
from dispute_copilot.human_review.display_models import EvidenceDisplayTarget


class FrozenApplicationModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class ExecutionMode(StrEnum):
    LIVE = "LIVE"
    CACHED_VIEW = "CACHED_VIEW"


class ExecutionStatus(StrEnum):
    NOT_RUN = "NOT_RUN"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BatchStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_FAILURES = "COMPLETED_WITH_FAILURES"


class BatchExecution(FrozenApplicationModel):
    batch_id: UUID
    execution_mode: ExecutionMode
    status: BatchStatus
    case_count: int = Field(ge=1)
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    created_at: datetime
    started_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def counts_and_times_are_consistent(self) -> "BatchExecution":
        for value in (self.created_at, self.started_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("batch timestamps must be timezone-aware")
        if self.completed_at is not None and (
            self.completed_at.tzinfo is None
            or self.completed_at.utcoffset() is None
        ):
            raise ValueError("completed_at must be timezone-aware")
        if self.completed_count + self.failed_count > self.case_count:
            raise ValueError("batch outcome counts exceed case_count")
        if self.status is BatchStatus.RUNNING and self.completed_at is not None:
            raise ValueError("a running batch cannot have completed_at")
        if self.status is not BatchStatus.RUNNING:
            if self.completed_at is None:
                raise ValueError("a completed batch requires completed_at")
            if self.completed_count + self.failed_count != self.case_count:
                raise ValueError("a completed batch requires all outcomes")
        return self


class CaseExecutionAttempt(FrozenApplicationModel):
    attempt_id: UUID
    batch_id: UUID
    case_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    status: ExecutionStatus
    execution_mode: ExecutionMode
    queued_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    run_id: UUID | None = None
    selection_id: UUID | None = None
    resolution_id: UUID | None = None
    evaluation_id: UUID | None = None
    recommendation_id: UUID | None = None
    review_package_id: UUID | None = None
    sanitized_error_type: str | None = None
    sanitized_error_message: str | None = None
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def state_shape_is_valid(self) -> "CaseExecutionAttempt":
        timestamps = (self.queued_at, self.started_at, self.completed_at)
        for value in timestamps:
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise ValueError("attempt timestamps must be timezone-aware")
        if self.status is ExecutionStatus.NOT_RUN:
            raise ValueError("NOT_RUN is a derived queue state, not an attempt")
        if self.status is ExecutionStatus.QUEUED:
            if self.started_at is not None or self.completed_at is not None:
                raise ValueError("queued attempt cannot have execution times")
        if self.status is ExecutionStatus.RUNNING:
            if self.started_at is None or self.completed_at is not None:
                raise ValueError("running attempt requires only started_at")
        if self.status in {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
        } and (self.started_at is None or self.completed_at is None):
            raise ValueError("terminal attempt requires execution times")
        if self.status is ExecutionStatus.COMPLETED:
            lineage = (
                self.run_id,
                self.selection_id,
                self.resolution_id,
                self.evaluation_id,
                self.recommendation_id,
                self.review_package_id,
            )
            if any(value is None for value in lineage):
                raise ValueError(
                    "completed attempt requires complete immutable lineage"
                )
            if (
                self.sanitized_error_type is not None
                or self.sanitized_error_message is not None
            ):
                raise ValueError("completed attempt cannot contain an error")
        if self.status is ExecutionStatus.FAILED:
            if (
                not self.sanitized_error_type
                or not self.sanitized_error_message
            ):
                raise ValueError("failed attempt requires a sanitized error")
            if self.review_package_id is not None:
                raise ValueError("failed attempt cannot claim a review package")
        expected_hash = calculate_attempt_content_hash(self)
        if self.content_hash != expected_hash:
            raise ValueError("attempt content_hash does not match content")
        return self


def calculate_attempt_content_hash(
    attempt: CaseExecutionAttempt,
) -> str:
    payload = attempt.model_dump(
        mode="json",
        exclude={"content_hash"},
    )
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


class CaseQueueItem(FrozenApplicationModel):
    case_id: str = Field(min_length=1)
    card_scheme: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    reason_code_label: str = Field(min_length=1)
    declared_evidence_count: int = Field(ge=0)
    status: ExecutionStatus
    execution_mode: ExecutionMode | None = None
    latest_attempt_id: UUID | None = None
    review_package_id: UUID | None = None
    error_message: str | None = None


class CaseProgressEvent(FrozenApplicationModel):
    batch_id: UUID
    attempt_id: UUID
    case_id: str = Field(min_length=1)
    status: ExecutionStatus
    occurred_at: datetime
    review_package_id: UUID | None = None
    error_message: str | None = None


class TechnicalLineage(FrozenApplicationModel):
    run_id: UUID
    selection_id: UUID
    resolution_id: UUID
    evaluation_id: UUID
    recommendation_id: UUID
    review_package_id: UUID
    rulebook_version: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    provider: str
    model: str
    adapter: str
    prompt_version: str
    schema_version: str
    parameters_json: str
    request_latency: str = "NOT RECORDED"
    token_usage: str = "NOT RECORDED"
    estimated_cost: str = "NOT RECORDED"


class AnalystCaseView(FrozenApplicationModel):
    case: Case
    status: ExecutionStatus
    execution_mode: ExecutionMode | None
    attempt: CaseExecutionAttempt | None = None
    package: AnalystReviewPackage | None = None
    resolution: FactResolutionResult | None = None
    evidence_targets: tuple[EvidenceDisplayTarget, ...] = ()
    technical_lineage: TechnicalLineage | None = None
    status_message: str

    @model_validator(mode="after")
    def detail_is_present_only_for_completed_cases(
        self,
    ) -> "AnalystCaseView":
        details = (
            self.package,
            self.resolution,
            self.technical_lineage,
        )
        if self.status is ExecutionStatus.COMPLETED:
            if any(value is None for value in details):
                raise ValueError(
                    "completed view requires package, resolution and lineage"
                )
        elif any(value is not None for value in details) or self.evidence_targets:
            raise ValueError(
                "non-completed view cannot expose prior case result details"
            )
        return self


class CaseExecutionResult(FrozenApplicationModel):
    attempt: CaseExecutionAttempt
    package: AnalystReviewPackage | None = None

    @model_validator(mode="after")
    def package_matches_outcome(self) -> "CaseExecutionResult":
        if (
            self.attempt.status is ExecutionStatus.COMPLETED
        ) != (self.package is not None):
            raise ValueError(
                "package is required exactly for completed execution"
            )
        return self
