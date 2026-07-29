"""Ordered deterministic requirement evaluation and aggregation."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from dispute_copilot.evaluation.interpreter import PredicateInterpreter
from dispute_copilot.evaluation.models import (
    AggregationSummary,
    OverallEvaluationStatus,
    RequirementEvaluation,
    RequirementEvaluationResult,
    RequirementStatus,
    TruthValue,
    calculate_evaluation_content_hash,
)
from dispute_copilot.evaluation.predicate_models import PolicyPredicates
from dispute_copilot.evaluation.predicate_models import AggregationMode
from dispute_copilot.extraction.fact_resolution_models import (
    FactResolutionResult,
)
from dispute_copilot.domain.policy_selection import PolicySelection

EVALUATOR_VERSION = "requirement-evaluator/0.2.0"


def evaluate_requirements(
    *,
    selection: PolicySelection,
    resolution: FactResolutionResult,
    predicates: PolicyPredicates,
    predicate_manifest_sha256: str,
    predicate_schema_version: str,
    evaluation_id: UUID | None = None,
    created_at: datetime | None = None,
) -> RequirementEvaluationResult:
    """Evaluate every frozen requirement in policy order."""
    facts = {fact.fact_name: fact for fact in resolution.facts}
    interpreter = PredicateInterpreter()
    evaluations: list[RequirementEvaluation] = []
    status_mapping = {
        TruthValue.TRUE: RequirementStatus.SATISFIED,
        TruthValue.FALSE: RequirementStatus.NOT_SATISFIED,
        TruthValue.UNKNOWN: RequirementStatus.NOT_ESTABLISHED,
    }
    for order, (requirement, executable) in enumerate(
        zip(
            selection.policy_snapshot.requirements,
            predicates.requirements,
            strict=True,
        ),
        start=1,
    ):
        applicability = (
            interpreter.evaluate(
                executable.applicability_predicate,
                facts,
            )
            if executable.applicability_predicate is not None
            else None
        )
        if applicability is not None and applicability.result is TruthValue.FALSE:
            evaluations.append(
                RequirementEvaluation(
                    requirement_id=requirement.requirement_id,
                    requirement_order=order,
                    status=RequirementStatus.NOT_APPLICABLE,
                    predicate_result=TruthValue.NOT_APPLICABLE,
                    basis_codes=("APPLICABILITY_PREDICATE_FALSE",),
                    fact_bindings=applicability.bindings,
                    missing_or_unresolved_facts=(),
                )
            )
            continue
        if applicability is not None and applicability.result is TruthValue.UNKNOWN:
            evaluations.append(
                RequirementEvaluation(
                    requirement_id=requirement.requirement_id,
                    requirement_order=order,
                    status=RequirementStatus.NOT_ESTABLISHED,
                    predicate_result=TruthValue.UNKNOWN,
                    basis_codes=(
                        "APPLICABILITY_NOT_ESTABLISHED",
                        *applicability.basis_codes,
                    ),
                    fact_bindings=applicability.bindings,
                    missing_or_unresolved_facts=(
                        applicability.unresolved_facts
                    ),
                )
            )
            continue
        outcome = interpreter.evaluate(executable.predicate, facts)
        if applicability is not None:
            outcome = _combine_applicability(applicability, outcome)
        evaluations.append(
            RequirementEvaluation(
                requirement_id=requirement.requirement_id,
                requirement_order=order,
                status=status_mapping[outcome.result],
                predicate_result=outcome.result,
                basis_codes=outcome.basis_codes,
                fact_bindings=outcome.bindings,
                missing_or_unresolved_facts=(
                    outcome.unresolved_facts
                ),
            )
        )

    overall, aggregation_summary = aggregate_requirements(
        tuple(evaluations),
        mode=predicates.aggregation.mode,
    )
    result_fields = {
        "evaluation_id": evaluation_id or uuid4(),
        "run_id": resolution.run_id,
        "selection_id": selection.selection_id,
        "resolution_id": resolution.resolution_id,
        "policy_id": selection.policy_id,
        "rulebook_version": selection.rulebook_version,
        "policy_sha256": selection.policy_sha256,
        "fact_resolution_content_hash": resolution.content_hash,
        "predicate_manifest_sha256": predicate_manifest_sha256,
        "evaluator_version": EVALUATOR_VERSION,
        "predicate_schema_version": predicate_schema_version,
        "ordered_requirement_results": tuple(evaluations),
        "aggregation_summary": aggregation_summary,
        "overall_status": overall,
        "status": "READY_FOR_RECOMMENDATION",
        "created_at": created_at or datetime.now(timezone.utc),
    }
    draft = RequirementEvaluationResult.model_construct(
        **result_fields,
        content_hash="0" * 64,
    )
    return RequirementEvaluationResult(
        **result_fields,
        content_hash=calculate_evaluation_content_hash(draft),
    )


def aggregate_requirements(
    results: tuple[RequirementEvaluation, ...],
    *,
    mode: AggregationMode,
) -> tuple[OverallEvaluationStatus, AggregationSummary]:
    """Aggregate applicable outcomes without consulting facts or evidence."""
    satisfied = sum(
        item.status is RequirementStatus.SATISFIED for item in results
    )
    not_satisfied = sum(
        item.status is RequirementStatus.NOT_SATISFIED for item in results
    )
    unresolved = sum(
        item.status is RequirementStatus.NOT_ESTABLISHED for item in results
    )
    not_applicable = sum(
        item.status is RequirementStatus.NOT_APPLICABLE for item in results
    )
    applicable = len(results) - not_applicable
    minimum_required = applicable if mode is AggregationMode.ALL_APPLICABLE else 2
    if applicable == 0:
        raise ValueError("policy has no applicable requirements")

    maximum_possible = satisfied + unresolved
    summary = AggregationSummary(
        mode=mode.value,
        satisfied_count=satisfied,
        not_satisfied_count=not_satisfied,
        unresolved_count=unresolved,
        not_applicable_count=not_applicable,
        applicable_count=applicable,
        minimum_required=minimum_required,
        maximum_possible_satisfied=maximum_possible,
    )
    if mode is AggregationMode.ALL_APPLICABLE:
        if not_satisfied:
            overall = (
                OverallEvaluationStatus
                .ONE_OR_MORE_REQUIREMENTS_NOT_SATISFIED
            )
        elif unresolved:
            overall = (
                OverallEvaluationStatus
                .ONE_OR_MORE_REQUIREMENTS_NOT_ESTABLISHED
            )
        else:
            overall = OverallEvaluationStatus.ALL_REQUIREMENTS_SATISFIED
        return overall, summary

    if satisfied >= 2:
        return OverallEvaluationStatus.ALL_REQUIREMENTS_SATISFIED, summary
    if maximum_possible < 2:
        return (
            OverallEvaluationStatus.ONE_OR_MORE_REQUIREMENTS_NOT_SATISFIED,
            summary,
        )
    return (
        OverallEvaluationStatus.ONE_OR_MORE_REQUIREMENTS_NOT_ESTABLISHED,
        summary,
    )


def _combine_applicability(applicability, outcome):
    """Retain applicability bindings without changing the predicate result."""
    from dispute_copilot.evaluation.interpreter import PredicateOutcome

    bindings = {
        binding.fact_name: binding
        for binding in (*applicability.bindings, *outcome.bindings)
    }
    return PredicateOutcome(
        result=outcome.result,
        basis_codes=outcome.basis_codes,
        bindings=tuple(bindings.values()),
        unresolved_facts=outcome.unresolved_facts,
    )
