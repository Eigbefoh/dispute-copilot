"""Unit and contract tests for generic cross-case policy semantics."""

from __future__ import annotations

from pathlib import Path
import json
from uuid import uuid4

import pytest

from dispute_copilot.evaluation.evaluator import aggregate_requirements
from dispute_copilot.evaluation.interpreter import PredicateInterpreter
from dispute_copilot.evaluation.models import (
    OverallEvaluationStatus,
    RequirementEvaluation,
    RequirementStatus,
    TruthValue,
)
from dispute_copilot.evaluation.predicate_catalogue import (
    load_predicate_manifest,
    select_and_validate_policy_predicates,
)
from dispute_copilot.evaluation.errors import PredicateContractError
from dispute_copilot.evaluation.predicate_models import (
    AggregationMode,
    PredicateManifest,
    PredicateNode,
)
from dispute_copilot.extraction.fact_resolution_models import (
    CaseMetadataProvenance,
    ResolvedFact,
    ResolvedValue,
)
from dispute_copilot.intake.case_loader import load_case
from dispute_copilot.domain.case_run import CaseRun, RunStatus
from dispute_copilot.extraction.metadata_resolver import resolve_case_metadata
from dispute_copilot.policy.fact_models import FactCatalogue, FactDefinition
from dispute_copilot.policy.rule_models import PolicyRecord

PROJECT_ROOT = Path(__file__).parents[1]
RULEBOOK_ROOT = PROJECT_ROOT / "data" / "rulebooks"
RULEBOOK = RULEBOOK_ROOT / "take-home-v3"
RUN_ID = uuid4()


def _found(name: str, value: object) -> ResolvedFact:
    return ResolvedFact(
        fact_name=name,
        status="FOUND",
        resolved_values=(
            ResolvedValue(
                value=value,
                provenance=(
                    CaseMetadataProvenance(
                        source_type="CASE_METADATA",
                        case_run_id=RUN_ID,
                        field_path=f"case_snapshot.{name}",
                    ),
                ),
            ),
        ),
        resolver_type="CASE_METADATA",
        confidence=None,
    )


def _requirement(
    status: RequirementStatus,
    order: int,
) -> RequirementEvaluation:
    truth = {
        RequirementStatus.SATISFIED: TruthValue.TRUE,
        RequirementStatus.NOT_SATISFIED: TruthValue.FALSE,
        RequirementStatus.NOT_ESTABLISHED: TruthValue.UNKNOWN,
        RequirementStatus.NOT_APPLICABLE: TruthValue.NOT_APPLICABLE,
    }[status]
    return RequirementEvaluation(
        requirement_id=f"R{order}",
        requirement_order=order,
        status=status,
        predicate_result=truth,
        basis_codes=("TEST",),
        fact_bindings=(),
        missing_or_unresolved_facts=(),
    )


def test_material_address_comparison_is_deterministic_and_explicit() -> None:
    facts = {
        "delivery": _found(
            "delivery",
            "22 Cavendish Court, London SW4 7QR",
        ),
        "purchase": _found("purchase", "SW4 7QR"),
    }
    predicate = PredicateNode(
        operator="material_address_match",
        fact_name="delivery",
        other_fact_name="purchase",
    )
    assert PredicateInterpreter().evaluate(
        predicate,
        facts,
    ).result is TruthValue.TRUE

    conflict = PredicateNode(
        operator="material_address_match",
        fact_name="delivery",
        other_fact_name="purchase",
        mismatch_result="UNKNOWN",
    )
    facts["purchase"] = _found("purchase", "M14 5RT")
    outcome = PredicateInterpreter().evaluate(conflict, facts)
    assert outcome.result is TruthValue.UNKNOWN
    assert outcome.basis_codes == (
        "MATERIAL_ADDRESS_ATTRIBUTION_CONFLICT",
    )


def test_explicit_first_transaction_can_negate_prior_history_route() -> None:
    predicate = PredicateNode(
        operator="not",
        predicates=(
            PredicateNode(
                operator="fact_equals",
                fact_name="first_transaction",
                expected_value=True,
            ),
        ),
    )
    outcome = PredicateInterpreter().evaluate(
        predicate,
        {"first_transaction": _found("first_transaction", True)},
    )
    assert outcome.result is TruthValue.FALSE


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (
            (
                RequirementStatus.SATISFIED,
                RequirementStatus.NOT_APPLICABLE,
                RequirementStatus.SATISFIED,
            ),
            OverallEvaluationStatus.ALL_REQUIREMENTS_SATISFIED,
        ),
        (
            (
                RequirementStatus.SATISFIED,
                RequirementStatus.NOT_APPLICABLE,
                RequirementStatus.NOT_ESTABLISHED,
            ),
            (
                OverallEvaluationStatus
                .ONE_OR_MORE_REQUIREMENTS_NOT_ESTABLISHED
            ),
        ),
        (
            (
                RequirementStatus.SATISFIED,
                RequirementStatus.NOT_APPLICABLE,
                RequirementStatus.NOT_SATISFIED,
            ),
            (
                OverallEvaluationStatus
                .ONE_OR_MORE_REQUIREMENTS_NOT_SATISFIED
            ),
        ),
    ],
)
def test_all_applicable_truth_table(
    statuses: tuple[RequirementStatus, ...],
    expected: OverallEvaluationStatus,
) -> None:
    overall, summary = aggregate_requirements(
        tuple(
            _requirement(status, order)
            for order, status in enumerate(statuses, start=1)
        ),
        mode=AggregationMode.ALL_APPLICABLE,
    )
    assert overall is expected
    assert summary.not_applicable_count == statuses.count(
        RequirementStatus.NOT_APPLICABLE
    )


@pytest.mark.parametrize(
    ("statuses", "expected", "maximum"),
    [
        (
            (
                RequirementStatus.SATISFIED,
                RequirementStatus.SATISFIED,
                RequirementStatus.NOT_ESTABLISHED,
            ),
            OverallEvaluationStatus.ALL_REQUIREMENTS_SATISFIED,
            3,
        ),
        (
            (
                RequirementStatus.SATISFIED,
                RequirementStatus.NOT_ESTABLISHED,
                RequirementStatus.NOT_SATISFIED,
            ),
            (
                OverallEvaluationStatus
                .ONE_OR_MORE_REQUIREMENTS_NOT_ESTABLISHED
            ),
            2,
        ),
        (
            (
                RequirementStatus.NOT_SATISFIED,
                RequirementStatus.NOT_SATISFIED,
                RequirementStatus.NOT_ESTABLISHED,
            ),
            (
                OverallEvaluationStatus
                .ONE_OR_MORE_REQUIREMENTS_NOT_SATISFIED
            ),
            1,
        ),
    ],
)
def test_any_two_uses_maximum_possible_satisfied(
    statuses: tuple[RequirementStatus, ...],
    expected: OverallEvaluationStatus,
    maximum: int,
) -> None:
    overall, summary = aggregate_requirements(
        tuple(
            _requirement(status, order)
            for order, status in enumerate(statuses, start=1)
        ),
        mode=AggregationMode.ANY_TWO,
    )
    assert overall is expected
    assert summary.minimum_required == 2
    assert summary.maximum_possible_satisfied == maximum


def test_purchase_type_is_resolved_from_mcc_not_case_id() -> None:
    case = load_case(
        "CB-2025-0001",
        PROJECT_ROOT / "data" / "cases.json",
    )
    run = CaseRun(
        run_id=RUN_ID,
        created_at=case.transaction.transaction_date,
        status=RunStatus.READY_FOR_POLICY_SELECTION,
        case_snapshot=case,
        evidence_snapshot=(),
        software_version="test",
    )
    definition = FactDefinition(
        fact_name="case_recorded_purchase_type",
        description="Purchase type.",
        value_type="STRING",
        source_type="CASE_METADATA",
    )
    resolved = resolve_case_metadata(
        run=run,
        definitions=(definition,),
    )
    assert resolved[0].value == "MERCHANDISE"


def test_take_home_v3_all_policies_have_exact_executable_coverage() -> None:
    manifest, manifest_hash = load_predicate_manifest(
        rulebook_version="take-home-v3",
        rulebook_root=RULEBOOK_ROOT,
    )
    catalogue = FactCatalogue.model_validate_json(
        (RULEBOOK / "facts" / "catalogue.json").read_text()
    )
    validated = []
    for path in sorted((RULEBOOK / "policies").glob("*.json")):
        policy = PolicyRecord.model_validate_json(path.read_text())
        validated.append(
            select_and_validate_policy_predicates(
                manifest=manifest,
                policy=policy,
                catalogue=catalogue,
            )
        )
    assert len(validated) == 8
    assert len(manifest_hash) == 64
    assert next(
        item for item in validated if item.policy_id.startswith("visa-13.1")
    ).aggregation.mode is AggregationMode.ALL_APPLICABLE
    assert next(
        item
        for item in validated
        if item.policy_id.startswith("mastercard-4837")
    ).aggregation.mode is AggregationMode.ANY_TWO


def test_v3_policy_without_explicit_aggregation_fails_closed() -> None:
    raw = json.loads(
        (RULEBOOK / "evaluation" / "predicates.json").read_text()
    )
    raw["policies"][0].pop("aggregation")
    manifest = PredicateManifest.model_validate(raw)
    catalogue = FactCatalogue.model_validate_json(
        (RULEBOOK / "facts" / "catalogue.json").read_text()
    )
    policy = PolicyRecord.model_validate_json(
        (RULEBOOK / "policies" / "mastercard-4837.json").read_text()
    )
    with pytest.raises(PredicateContractError, match="aggregation"):
        select_and_validate_policy_predicates(
            manifest=manifest,
            policy=policy,
            catalogue=catalogue,
        )


def test_production_code_contains_no_target_case_branches() -> None:
    target_ids = tuple(f"CB-2025-000{number}" for number in range(1, 5))
    source_root = PROJECT_ROOT / "src" / "dispute_copilot"
    for path in source_root.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        assert not any(case_id in content for case_id in target_ids), path
