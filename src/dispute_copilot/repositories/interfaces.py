"""Persistence interfaces for immutable case runs."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from dispute_copilot.domain.case_run import CaseRun
from dispute_copilot.domain.rulebook import RulebookSource


class RunAlreadyExistsError(Exception):
    """Raised when attempting to overwrite an existing case run."""

    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        super().__init__(f"Case run '{run_id}' already exists")


class RulebookSourceAlreadyExistsError(Exception):
    """Raised when attempting to overwrite rulebook source provenance."""

    def __init__(self, rulebook_version: str) -> None:
        self.rulebook_version = rulebook_version
        super().__init__(
            f"Rulebook source '{rulebook_version}' already exists"
        )


class CaseRunRepository(Protocol):
    """Storage contract for immutable case runs."""

    def save(self, run: CaseRun) -> None:
        """Persist a new run without overwriting existing data."""
        ...

    def get(self, run_id: UUID) -> CaseRun | None:
        """Retrieve a run by ID, or return None when it does not exist."""
        ...


class RulebookSourceRepository(Protocol):
    """Storage contract for immutable rulebook source provenance."""

    def save(self, source: RulebookSource) -> None:
        """Persist a new source without overwriting an existing version."""
        ...

    def get(self, rulebook_version: str) -> RulebookSource | None:
        """Retrieve source provenance by rulebook version."""
        ...
