"""Deterministic predicate and executable-policy evaluation tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from dispute_copilot.evaluation.interpreter import PredicateInterpreter
from dispute_copilot.evaluation.models import TruthValue
from dispute_copilot.evaluation.predicate_catalogue import (
    load_predicate_manifest,
    select_and_validate_policy_predicates,
)
from dispute_copilot.evaluation.predicate_models import PredicateNode
from dispute_copilot.extraction.fact_resolution_models import (
    CaseMetadataProvenance,
    ResolvedFact,
    ResolvedValue,
)
from dispute_copilot.policy.fact_models import FactCatalogue
from dispute_copilot.policy.rule_models import PolicyRecord

PROJECT_ROOT = Path(__file__).parents[1]
RULEBOOK_ROOT = PROJECT_ROOT / "data" / "rulebooks"
RULEBOOK = RULEBOOK_ROOT / "take-home-v2"
RUN_ID = uuid4()


def found(name: str, value: object) -> ResolvedFact:
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


def unresolved(name: str, status: str = "NOT_FOUND") -> ResolvedFact:
    values = ()
    if status in {"AMBIGUOUS", "CONFLICTING"}:
        count = 1 if status == "AMBIGUOUS" else 2
        values = tuple(
            ResolvedValue(
                value=f"candidate-{index}",
                provenance=(
                    CaseMetadataProvenance(
                        source_type="CASE_METADATA",
                        case_run_id=RUN_ID,
                        field_path=f"candidate.{index}",
                    ),
                ),
            )
            for index in range(count)
        )
    # Constructing an unresolved metadata fact is intentionally bypassed:
    # metadata routing forbids ambiguity, while the interpreter must still
    # defend the generic FactResolutionResult contract.
    return ResolvedFact.model_construct(
        fact_name=name,
        status=status,
        resolved_values=values,
        resolver_type="EVIDENCE_EXTRACTION",
        confidence=None,
    )


def leaf_for(result: TruthValue, name: str) -> tuple[
    PredicateNode,
    ResolvedFact,
]:
    if result is TruthValue.TRUE:
        return (
            PredicateNode(
                operator="fact_is_found",
                fact_name=name,
            ),
            found(name, "yes"),
        )
    if result is TruthValue.FALSE:
        return (
            PredicateNode(
                operator="fact_equals",
                fact_name=name,
                expected_value="expected",
            ),
            found(name, "different"),
        )
    return (
        PredicateNode(
            operator="fact_is_found",
            fact_name=name,
        ),
        unresolved(name),
    )


@pytest.mark.parametrize(
    ("operator", "left", "right", "expected"),
    [
        ("all_of", TruthValue.FALSE, TruthValue.UNKNOWN, TruthValue.FALSE),
        ("all_of", TruthValue.TRUE, TruthValue.UNKNOWN, TruthValue.UNKNOWN),
        ("all_of", TruthValue.TRUE, TruthValue.TRUE, TruthValue.TRUE),
        ("any_of", TruthValue.TRUE, TruthValue.UNKNOWN, TruthValue.TRUE),
        ("any_of", TruthValue.FALSE, TruthValue.UNKNOWN, TruthValue.UNKNOWN),
        ("any_of", TruthValue.FALSE, TruthValue.FALSE, TruthValue.FALSE),
    ],
)
def test_three_valued_composite_truth_tables(
    operator: str,
    left: TruthValue,
    right: TruthValue,
    expected: TruthValue,
) -> None:
    left_node, left_fact = leaf_for(left, "left_fact")
    right_node, right_fact = leaf_for(right, "right_fact")
    predicate = PredicateNode(
        operator=operator,
        predicates=(left_node, right_node),
    )

    outcome = PredicateInterpreter().evaluate(
        predicate,
        {
            left_fact.fact_name: left_fact,
            right_fact.fact_name: right_fact,
        },
    )

    assert outcome.result is expected


@pytest.mark.parametrize(
    "status",
    ["NOT_FOUND", "AMBIGUOUS", "CONFLICTING"],
)
def test_unresolved_fact_statuses_are_unknown_not_false(
    status: str,
) -> None:
    predicate = PredicateNode(
        operator="fact_equals",
        fact_name="observed_value",
        expected_value="no",
    )

    outcome = PredicateInterpreter().evaluate(
        predicate,
        {"observed_value": unresolved("observed_value", status)},
    )

    assert outcome.result is TruthValue.UNKNOWN
    assert outcome.unresolved_facts == ("observed_value",)


def test_explicit_incompatible_value_is_false() -> None:
    predicate = PredicateNode(
        operator="fact_in",
        fact_name="delivery_status",
        expected_values=("delivered",),
    )

    outcome = PredicateInterpreter().evaluate(
        predicate,
        {"delivery_status": found("delivery_status", "returned")},
    )

    assert outcome.result is TruthValue.FALSE


def test_date_and_numeric_boundaries_are_inclusive() -> None:
    facts = {
        "delivery_date": found("delivery_date", date(2025, 4, 18)),
        "chargeback_date": found(
            "chargeback_date",
            date(2025, 4, 18),
        ),
        "count": found("count", 2),
    }
    date_predicate = PredicateNode(
        operator="date_on_or_before",
        fact_name="delivery_date",
        other_fact_name="chargeback_date",
    )
    numeric_predicate = PredicateNode(
        operator="numeric_at_least",
        fact_name="count",
        numeric_bound=Decimal("2"),
    )

    assert PredicateInterpreter().evaluate(
        date_predicate,
        facts,
    ).result is TruthValue.TRUE
    assert PredicateInterpreter().evaluate(
        numeric_predicate,
        facts,
    ).result is TruthValue.TRUE


def test_all_eight_policies_have_exact_executable_coverage() -> None:
    manifest, manifest_hash = load_predicate_manifest(
        rulebook_version="take-home-v2",
        rulebook_root=RULEBOOK_ROOT,
    )
    catalogue = FactCatalogue.model_validate_json(
        (RULEBOOK / "facts" / "catalogue.json").read_text()
    )
    policy_paths = sorted((RULEBOOK / "policies").glob("*.json"))

    validated = []
    for path in policy_paths:
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
    assert all(item.requirements for item in validated)


def test_unknown_operator_and_cross_operator_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PredicateNode.model_validate(
            {
                "operator": "python_eval",
                "fact_name": "anything",
            }
        )
    with pytest.raises(ValidationError, match="unsupported fields"):
        PredicateNode.model_validate(
            {
                "operator": "fact_is_found",
                "fact_name": "anything",
                "expected_values": ["smuggled"],
            }
        )
