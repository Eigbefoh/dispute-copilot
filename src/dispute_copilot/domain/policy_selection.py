"""Immutable policy-selection snapshots and workflow state."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from dispute_copilot.policy.rule_models import PolicyRecord


class PolicySelectionStatus(StrEnum):
    """Workflow state emitted by successful policy selection."""

    READY_FOR_EXTRACTION = "READY_FOR_EXTRACTION"


class PolicySelection(BaseModel):
    """Frozen record linking one case run to one exact policy snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    selection_id: UUID
    run_id: UUID
    selected_at: datetime
    status: PolicySelectionStatus

    rulebook_version: str = Field(min_length=1)
    scheme: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_snapshot: PolicyRecord

