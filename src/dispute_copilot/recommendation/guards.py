"""Authoritative evaluation and workflow guards."""

from __future__ import annotations

from dispute_copilot.domain.policy_selection import PolicySelection
from dispute_copilot.evaluation.models import (
    OverallEvaluationStatus,
    RequirementEvaluationResult,
    RequirementStatus,
    calculate_evaluation_content_hash,
)
from dispute_copilot.recommendation.errors import RecommendationInputError


def validate_recommendation_input(
    *,
    evaluation: RequirementEvaluationResult,
    selection: PolicySelection,
    workflow_status: str | None,
) -> OverallEvaluationStatus:
    """Revalidate lineage and aggregate without loading facts."""
    if workflow_status != "READY_FOR_RECOMMENDATION":
        raise RecommendationInputError(
            "Authoritative workflow is not READY_FOR_RECOMMENDATION"
        )
    if evaluation.status != "READY_FOR_RECOMMENDATION":
        raise RecommendationInputError(
            "Evaluation does not emit READY_FOR_RECOMMENDATION"
        )
    if (
        evaluation.run_id != selection.run_id
        or evaluation.selection_id != selection.selection_id
    ):
        raise RecommendationInputError(
            "Evaluation run/selection lineage does not match"
        )
    if (
        evaluation.policy_id != selection.policy_id
        or evaluation.rulebook_version != selection.rulebook_version
        or evaluation.policy_sha256 != selection.policy_sha256
    ):
        raise RecommendationInputError(
            "Evaluation policy lineage does not match frozen selection"
        )
    if evaluation.content_hash != calculate_evaluation_content_hash(
        evaluation
    ):
        raise RecommendationInputError(
            "Evaluation content hash is invalid"
        )

    expected_requirements = tuple(
        requirement.requirement_id
        for requirement in selection.policy_snapshot.requirements
    )
    actual_requirements = tuple(
        item.requirement_id
        for item in evaluation.ordered_requirement_results
    )
    if actual_requirements != expected_requirements:
        raise RecommendationInputError(
            "Evaluation requirement IDs/order do not match frozen policy"
        )

    recomputed = recompute_overall_status(evaluation)
    if evaluation.overall_status is not recomputed:
        raise RecommendationInputError(
            "Stored evaluation aggregate does not match requirement "
            "outcomes"
        )
    return recomputed


def recompute_overall_status(
    evaluation: RequirementEvaluationResult,
) -> OverallEvaluationStatus:
    """Apply the fixed requirement aggregation precedence again."""
    statuses = {
        item.status for item in evaluation.ordered_requirement_results
    }
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
    if statuses == {RequirementStatus.SATISFIED}:
        return OverallEvaluationStatus.ALL_REQUIREMENTS_SATISFIED
    raise RecommendationInputError(
        "Evaluation contains no supported aggregate outcome"
    )
