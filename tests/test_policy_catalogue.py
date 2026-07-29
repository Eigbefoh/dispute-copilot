"""Coverage gate for supplied cases and provisioned policies."""

from __future__ import annotations

import json
from pathlib import Path

from dispute_copilot.domain.case import Case
from dispute_copilot.policy.import_rulebook import import_rulebook
from dispute_copilot.policy.repository import SQLitePolicyRepository

PROJECT_ROOT = Path(__file__).parents[1]
CASES_PATH = PROJECT_ROOT / "data" / "cases.json"
RULEBOOK_DIRECTORY = (
    PROJECT_ROOT / "data" / "rulebooks" / "take-home-v1"
)


def test_every_supplied_case_has_an_exact_policy(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    import_rulebook(RULEBOOK_DIRECTORY, database_path)
    repository = SQLitePolicyRepository(database_path)
    cases = [
        Case.model_validate(case_data)
        for case_data in json.loads(CASES_PATH.read_text())
    ]

    for case in cases:
        assert repository.get_imported(
            "take-home-v1",
            case.scheme,
            case.reason_code,
        ) is not None, (
            f"Missing take-home-v1 policy for "
            f"{case.scheme}/{case.reason_code}"
        )


def test_catalogue_covers_all_unique_supplied_case_keys() -> None:
    cases = json.loads(CASES_PATH.read_text())
    case_keys = {
        (case["scheme"], case["reason_code"]) for case in cases
    }
    policy_keys = {
        (policy["scheme"], policy["reason_code"])
        for policy_path in (
            RULEBOOK_DIRECTORY / "policies"
        ).glob("*.json")
        for policy in [
            json.loads(policy_path.read_text(encoding="utf-8"))
        ]
    }

    assert len(case_keys) == 8
    assert policy_keys == case_keys

