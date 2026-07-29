"""Immutable intake snapshot for a dispute processing run."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from dispute_copilot.domain.case import Case
from dispute_copilot.domain.evidence import EvidenceArtifact


class RunStatus(StrEnum):
    """Statuses available when an intake run is created."""

    READY_FOR_POLICY_SELECTION = "READY_FOR_POLICY_SELECTION"


class CaseRun(BaseModel):
    """An immutable, complete snapshot of successful case intake."""

    model_config = ConfigDict(frozen=True)

    run_id: UUID
    created_at: datetime
    status: RunStatus

    case_snapshot: Case
    evidence_snapshot: tuple[EvidenceArtifact, ...]

    software_version: str
