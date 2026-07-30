"""Pure aggregate-to-recommendation mapping."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from dispute_copilot.evaluation.models import (
    OverallEvaluationStatus,
    RequirementEvaluationResult,
    RequirementStatus,
)
from dispute_copilot.recommendation.errors import MappingContractError
from dispute_copilot.recommendation.mapping_models import (
    Recommendation,
    RecommendationMappingManifest,
)
from dispute_copilot.recommendation.models import (
    CausalRequirementResult,
    RecommendationResult,
    calculate_recommendation_content_hash,
)


def map_recommendation(
    *,
    evaluation: RequirementEvaluationResult,
    aggregate: OverallEvaluationStatus,
    manifest: RecommendationMappingManifest,
    recommendation_id: UUID | None = None,
    created_at: datetime | None = None,
) -> RecommendationResult:
    """Apply exactly one manifest rule and copy causal outcomes."""
    scoped_matches = tuple(
        rule
        for rule in manifest.ordered_rules
        if rule.when_overall_evaluation_status is aggregate
        and rule.when_policy_ids
        and evaluation.policy_id in rule.when_policy_ids
        and evaluation.rulebook_version in rule.when_rulebook_versions
    )
    matches = scoped_matches or tuple(
        rule
        for rule in manifest.ordered_rules
        if rule.when_overall_evaluation_status is aggregate
        and not rule.when_policy_ids
    )
    if len(matches) != 1:
        raise MappingContractError(
            "Expected exactly one recommendation mapping rule for "
            f"{aggregate}; found {len(matches)}"
        )
    rule = matches[0]
    causal_statuses = rule.causal_requirement_statuses or (
        {
            Recommendation.RECOMMEND_REPRESENT: (
                RequirementStatus.SATISFIED,
            ),
            Recommendation.RECOMMEND_DO_NOT_REPRESENT: (
                RequirementStatus.NOT_SATISFIED,
            ),
            Recommendation.INSUFFICIENT_EVIDENCE_TO_REPRESENT: (
                RequirementStatus.NOT_ESTABLISHED,
            ),
        }[rule.produce]
    )
    causal = tuple(
        CausalRequirementResult(
            requirement_id=item.requirement_id,
            requirement_order=item.requirement_order,
            status=item.status,
            evaluation_basis_codes=item.basis_codes,
        )
        for item in evaluation.ordered_requirement_results
        if item.status in causal_statuses
    )
    if not causal:
        raise MappingContractError(
            "Mapping produced no causal requirement outcomes"
        )

    result_fields = {
        "recommendation_id": recommendation_id or uuid4(),
        "run_id": evaluation.run_id,
        "selection_id": evaluation.selection_id,
        "resolution_id": evaluation.resolution_id,
        "evaluation_id": evaluation.evaluation_id,
        "policy_id": evaluation.policy_id,
        "rulebook_version": evaluation.rulebook_version,
        "policy_sha256": evaluation.policy_sha256,
        "evaluation_content_hash": evaluation.content_hash,
        "mapping_key": manifest.mapping_key,
        "mapping_version": manifest.mapping_version,
        "mapping_schema_version": manifest.mapping_schema_version,
        "mapping_integrity_hash": manifest.integrity_hash,
        "recommendation": rule.produce,
        "basis_codes": (rule.basis_code,),
        "causal_requirement_results": causal,
        "status": "READY_FOR_ANALYST_REVIEW",
        "created_at": created_at or datetime.now(timezone.utc),
    }
    draft = RecommendationResult.model_construct(
        **result_fields,
        content_hash="0" * 64,
    )
    return RecommendationResult(
        **result_fields,
        content_hash=calculate_recommendation_content_hash(draft),
    )
