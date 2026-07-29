"""Tests for loading and validating dispute cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from dispute_copilot.domain.case import Case
from dispute_copilot.intake.case_loader import CaseNotFoundError, load_case

CASES_PATH = Path(__file__).parents[1] / "data" / "cases.json"


def test_load_case_returns_validated_case() -> None:
    case = load_case("CB-2025-0001", CASES_PATH)

    assert isinstance(case, Case)
    assert case.case_id == "CB-2025-0001"
    assert case.transaction.transaction_id == "txn_8821AB"


def test_load_case_raises_clear_error_for_unknown_id() -> None:
    with pytest.raises(
        CaseNotFoundError,
        match=r"Case 'CB-DOES-NOT-EXIST' was not found",
    ):
        load_case("CB-DOES-NOT-EXIST", CASES_PATH)

