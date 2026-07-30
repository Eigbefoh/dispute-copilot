"""Contract gate between the source PDF and future executable v4 policy."""

from __future__ import annotations

from pathlib import Path

from dispute_copilot.policy.semantic_contract_models import (
    ExampleTreatment,
    PolicySemanticContractManifest,
    SourceStructure,
)


PROJECT_ROOT = Path(__file__).parents[1]
CONTRACT_PATH = (
    PROJECT_ROOT
    / "data"
    / "policy-contracts"
    / "take-home-v4"
    / "source-derived-contracts.json"
)

EXPECTED_REQUIREMENTS = {
    "visa-10.5-take-home-v4": ("V10.5-R1",),
    "visa-12.6.1-take-home-v4": (
        "V12.6.1-R1",
        "V12.6.1-R2",
        "V12.6.1-R3",
    ),
    "visa-13.1-take-home-v4": (
        "V13.1-R1",
        "V13.1-R2",
        "V13.1-R3",
        "V13.1-R4",
    ),
    "visa-13.2-take-home-v4": (
        "V13.2-R1",
        "V13.2-R2",
        "V13.2-R3",
        "V13.2-R4",
    ),
    "visa-13.3-take-home-v4": (
        "V13.3-R1",
        "V13.3-R2",
        "V13.3-R3",
    ),
    "mastercard-4837-take-home-v4": (
        "MC4837-R1",
        "MC4837-R2",
        "MC4837-R3",
        "MC4837-R4",
    ),
    "mastercard-4855-take-home-v4": (
        "MC4855-R1",
        "MC4855-R2",
        "MC4855-R3",
    ),
    "mastercard-4859-take-home-v4": (
        "MC4859-R1",
        "MC4859-R2",
        "MC4859-R3",
        "MC4859-R4",
    ),
}


def load_contracts() -> PolicySemanticContractManifest:
    return PolicySemanticContractManifest.model_validate_json(
        CONTRACT_PATH.read_text(encoding="utf-8")
    )


def test_every_audited_policy_and_requirement_has_a_contract() -> None:
    manifest = load_contracts()
    actual = {
        policy.policy_id: tuple(
            requirement.requirement_id
            for requirement in policy.requirements
        )
        for policy in manifest.policies
    }

    assert actual == EXPECTED_REQUIREMENTS
    assert sum(len(ids) for ids in actual.values()) == 26


def test_every_requirement_has_true_false_and_unknown_contracts() -> None:
    manifest = load_contracts()
    for policy in manifest.policies:
        for requirement in policy.requirements:
            truths = {
                scenario.expected_truth
                for scenario in requirement.scenarios
            }
            assert truths == {"TRUE", "FALSE", "UNKNOWN"}


def test_source_examples_are_not_universally_mandatory() -> None:
    manifest = load_contracts()
    requirements = {
        requirement.requirement_id: requirement
        for policy in manifest.policies
        for requirement in policy.requirements
    }
    for requirement_id in (
        "V12.6.1-R1",
        "V12.6.1-R2",
        "V13.1-R2",
        "V13.3-R2",
        "MC4837-R2",
        "MC4855-R1",
    ):
        assert (
            requirements[requirement_id].example_treatment
            is ExampleTreatment.ILLUSTRATIVE
        )


def test_critical_alternative_routes_are_independent() -> None:
    manifest = load_contracts()
    requirements = {
        requirement.requirement_id: requirement
        for policy in manifest.policies
        for requirement in policy.requirements
    }
    expected_branch_counts = {
        "V13.3-R2": 3,
        "V13.3-R3": 2,
        "MC4859-R3": 2,
        "MC4855-R1": 2,
    }
    for requirement_id, count in expected_branch_counts.items():
        contract = requirements[requirement_id]
        assert contract.source_structure is SourceStructure.ALTERNATIVE
        assert len(contract.branches) == count


def test_unresolved_source_configuration_is_explicitly_blocked() -> None:
    manifest = load_contracts()
    requirements = {
        requirement.requirement_id: requirement
        for policy in manifest.policies
        for requirement in policy.requirements
    }
    assert requirements["V13.2-R2"].configuration_blockers == (
        "notification_transaction_threshold",
        "notification_lead_days",
    )
    assert "Visa 10.5 liability outcome vocabulary" in (
        manifest.unresolved_policy_owner_decisions
    )
