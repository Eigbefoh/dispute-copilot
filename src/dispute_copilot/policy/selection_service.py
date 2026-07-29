"""Deterministic runtime selection of imported executable policies."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from dispute_copilot.domain.case_run import CaseRun, RunStatus
from dispute_copilot.domain.policy_selection import (
    PolicySelection,
    PolicySelectionStatus,
)
from dispute_copilot.policy.repository import ImportedPolicy

DEFAULT_DATABASE_PATH = Path("data/dispute_copilot.db")


class CaseRunReader(Protocol):
    """Read-only access required by policy selection."""

    def get(self, run_id: UUID) -> CaseRun | None:
        """Retrieve an immutable case run."""
        ...


class ImportedPolicyReader(Protocol):
    """Exact imported-policy lookup required by selection."""

    def get_imported(
        self,
        rulebook_version: str,
        scheme: str,
        reason_code: str,
    ) -> ImportedPolicy | None:
        """Retrieve an exact policy key with its integrity metadata."""
        ...


class PolicySelectionWriter(Protocol):
    """Append-only selection persistence required by selection."""

    def save(self, selection: PolicySelection) -> None:
        """Persist one immutable selection."""
        ...


class CaseRunNotFoundError(Exception):
    """Raised when the requested case run does not exist."""


class CaseRunNotReadyForPolicySelectionError(Exception):
    """Raised when a run is not at the policy-selection boundary."""


class ExactPolicyNotFoundError(Exception):
    """Raised when the exact version, scheme, and reason code is absent."""


def select_policy_for_run(
    run_id: UUID,
    rulebook_version: str,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> PolicySelection:
    """Select a policy using only a run ID and rulebook version."""
    from dispute_copilot.policy.repository import (
        SQLitePolicyRepository,
        SQLitePolicySelectionRepository,
    )
    from dispute_copilot.repositories.sqlite import (
        SQLiteCaseRunRepository,
    )

    return select_policy_for_run_with_repositories(
        run_id=run_id,
        rulebook_version=rulebook_version,
        case_run_repository=SQLiteCaseRunRepository(database_path),
        policy_repository=SQLitePolicyRepository(database_path),
        selection_repository=SQLitePolicySelectionRepository(
            database_path
        ),
    )


def select_policy_for_run_with_repositories(
    run_id: UUID,
    rulebook_version: str,
    case_run_repository: CaseRunReader,
    policy_repository: ImportedPolicyReader,
    selection_repository: PolicySelectionWriter,
) -> PolicySelection:
    """Internal dependency-injected selection implementation."""
    run = case_run_repository.get(run_id)
    if run is None:
        raise CaseRunNotFoundError(f"Case run '{run_id}' was not found")
    if run.status is not RunStatus.READY_FOR_POLICY_SELECTION:
        raise CaseRunNotReadyForPolicySelectionError(
            f"Case run '{run_id}' has status {run.status}; expected "
            f"{RunStatus.READY_FOR_POLICY_SELECTION}"
        )

    scheme = run.case_snapshot.scheme
    reason_code = run.case_snapshot.reason_code
    imported_policy = policy_repository.get_imported(
        rulebook_version,
        scheme,
        reason_code,
    )
    if imported_policy is None:
        raise ExactPolicyNotFoundError(
            "No exact policy for "
            f"{rulebook_version}/{scheme}/{reason_code}"
        )

    policy = imported_policy.policy
    selection = PolicySelection(
        selection_id=uuid4(),
        run_id=run.run_id,
        selected_at=datetime.now(timezone.utc),
        status=PolicySelectionStatus.READY_FOR_EXTRACTION,
        rulebook_version=policy.rulebook_version,
        scheme=policy.scheme,
        reason_code=policy.reason_code,
        policy_id=policy.policy_id,
        policy_sha256=imported_policy.policy_sha256,
        policy_snapshot=policy,
    )
    selection_repository.save(selection)
    return selection
