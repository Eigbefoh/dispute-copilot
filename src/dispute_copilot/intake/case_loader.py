"""Load and validate dispute cases from JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dispute_copilot.domain.case import Case

DEFAULT_CASES_PATH = Path("data/cases.json")


class CaseNotFoundError(LookupError):
    """Raised when a requested case ID is absent from the case data."""


def load_case(
    case_id: str,
    cases_path: str | Path = DEFAULT_CASES_PATH,
) -> Case:
    """Load a case by ID and validate it against the domain model."""
    path = Path(cases_path)

    with path.open(encoding="utf-8") as cases_file:
        records: Any = json.load(cases_file)

    if not isinstance(records, list):
        raise ValueError(f"Expected a JSON list in {path}")

    for record in records:
        if isinstance(record, dict) and record.get("case_id") == case_id:
            return Case.model_validate(record)

    raise CaseNotFoundError(f"Case '{case_id}' was not found in {path}")

