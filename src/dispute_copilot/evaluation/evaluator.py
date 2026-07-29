"""Ordered deterministic requirement evaluation and aggregation."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from dispute_copilot.evaluation.interpreter import PredicateInterpreter
from dispute_copilot.evaluation.models import (
    OverallEvaluationStatus,
    RequirementEvaluation,
    RequirementEvaluationResult,
    RequirementStatus,
    TruthValue,
    calculate_evaluation_content_hash,
)
from dispute_copilot.evaluation.predicate_models import PolicyPredicates
from dispute_copilot.extraction.fact_resolution_models import (
    FactResolutionResult,
)
from dispute_copilot.domain.policy_selection import PolicySelection

EVALUATOR_VERSION = "requirement-evaluator/0.1.0"


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
        outcome = interpreter.evaluate(executable.predicate, facts)
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

    overall = _aggregate(tuple(evaluations))
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


def _aggregate(
    results: tuple[RequirementEvaluation, ...],
) -> OverallEvaluationStatus:
    statuses = {item.status for item in results}
    if RequirementStatus.NOT_SATISFIED in statuses:
        return (
            OverallEvaluationStatus
            .ONE_OR_MORE_REQUIREMENTS_NOT_SATISFIED
        )
    if RequirementStatus.NOT_ESTABLISHED in statuses:
        return (
            OverallEvaluationStatus
            .ONE_OR_MORE_REQUIREMENTS_NOT_ESTABLISHED
        )
    return OverallEvaluationStatus.ALL_REQUIREMENTS_SATISFIED
