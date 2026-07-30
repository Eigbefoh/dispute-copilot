"""Complete lineage and integrity validation before review assembly."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from pydantic import BaseModel

from dispute_copilot.analyst_review.errors import (
    AnalystReviewInputError,
)
from dispute_copilot.domain.case_run import CaseRun
from dispute_copilot.domain.policy_selection import PolicySelection
from dispute_copilot.evaluation.models import (
    RequirementEvaluationResult,
    RequirementStatus,
    calculate_evaluation_content_hash,
)
from dispute_copilot.extraction.fact_resolution_models import (
    FactResolutionResult,
    calculate_fact_resolution_content_hash,
)
from dispute_copilot.extraction.fact_resolution_validation import (
    selected_policy_fact_names,
)
from dispute_copilot.recommendation.mapping_models import (
    Recommendation,
    RecommendationBasisCode,
)
from dispute_copilot.recommendation.models import (
    CausalRequirementResult,
    RecommendationResult,
    calculate_recommendation_content_hash,
)


@dataclass(frozen=True)
class AnalystReviewInputs:
    recommendation: RecommendationResult
    evaluation: RequirementEvaluationResult
    resolution: FactResolutionResult
    selection: PolicySelection
    case_run: CaseRun
    case_run_content_hash: str
    policy_selection_content_hash: str


def validate_analyst_review_inputs(
    *,
    recommendation: RecommendationResult,
    evaluation: RequirementEvaluationResult,
    resolution: FactResolutionResult,
    selection: PolicySelection,
    case_run: CaseRun,
    workflow_status: str | None,
) -> AnalystReviewInputs:
    """Validate one unbroken, authoritative persisted decision chain."""
    if workflow_status != "READY_FOR_ANALYST_REVIEW":
        raise AnalystReviewInputError(
            "Authoritative workflow is not READY_FOR_ANALYST_REVIEW"
        )
    if recommendation.status != "READY_FOR_ANALYST_REVIEW":
        raise AnalystReviewInputError(
            "Recommendation does not emit READY_FOR_ANALYST_REVIEW"
        )
    if evaluation.status != "READY_FOR_RECOMMENDATION":
        raise AnalystReviewInputError(
            "Linked evaluation has an invalid output status"
        )
    if resolution.status != "READY_FOR_EVALUATION":
        raise AnalystReviewInputError(
            "Linked fact resolution has an invalid output status"
        )
    if (
        recommendation.content_hash
        != calculate_recommendation_content_hash(recommendation)
    ):
        raise AnalystReviewInputError(
            "Recommendation content hash is invalid"
        )
    if evaluation.content_hash != calculate_evaluation_content_hash(
        evaluation
    ):
        raise AnalystReviewInputError("Evaluation content hash is invalid")
    if (
        resolution.content_hash
        != calculate_fact_resolution_content_hash(resolution)
    ):
        raise AnalystReviewInputError(
            "Fact-resolution content hash is invalid"
        )

    expected_ids = (
        recommendation.run_id,
        recommendation.selection_id,
        recommendation.resolution_id,
        recommendation.evaluation_id,
    )
    linked_ids = (
        case_run.run_id,
        selection.selection_id,
        resolution.resolution_id,
        evaluation.evaluation_id,
    )
    if expected_ids != linked_ids:
        raise AnalystReviewInputError(
            "Recommendation-linked record identities do not match"
        )
    if (
        evaluation.run_id != case_run.run_id
        or evaluation.selection_id != selection.selection_id
        or evaluation.resolution_id != resolution.resolution_id
        or resolution.run_id != case_run.run_id
        or resolution.selection_id != selection.selection_id
        or selection.run_id != case_run.run_id
    ):
        raise AnalystReviewInputError(
            "Run-to-recommendation lineage is not an unbroken chain"
        )
    if recommendation.evaluation_content_hash != evaluation.content_hash:
        raise AnalystReviewInputError(
            "Recommendation does not bind the exact evaluation content"
        )
    if (
        evaluation.fact_resolution_content_hash
        != resolution.content_hash
    ):
        raise AnalystReviewInputError(
            "Evaluation does not bind the exact fact-resolution content"
        )

    policy_identity = (
        selection.policy_id,
        selection.rulebook_version,
        selection.policy_sha256,
    )
    if any(
        identity != policy_identity
        for identity in (
            (
                resolution.policy_id,
                resolution.rulebook_version,
                resolution.policy_sha256,
            ),
            (
                evaluation.policy_id,
                evaluation.rulebook_version,
                evaluation.policy_sha256,
            ),
            (
                recommendation.policy_id,
                recommendation.rulebook_version,
                recommendation.policy_sha256,
            ),
        )
    ):
        raise AnalystReviewInputError(
            "Policy identity or integrity hash differs across lineage"
        )
    policy = selection.policy_snapshot
    if (
        policy.policy_id != selection.policy_id
        or policy.rulebook_version != selection.rulebook_version
        or policy.scheme != selection.scheme
        or policy.reason_code != selection.reason_code
    ):
        raise AnalystReviewInputError(
            "Frozen selected-policy snapshot identity is inconsistent"
        )
    if (
        case_run.case_snapshot.scheme != selection.scheme
        or case_run.case_snapshot.reason_code != selection.reason_code
    ):
        raise AnalystReviewInputError(
            "Case scheme/reason code differs from frozen policy selection"
        )
    if calculate_policy_snapshot_sha256(policy) != selection.policy_sha256:
        raise AnalystReviewInputError(
            "Frozen policy snapshot integrity hash is invalid"
        )

    expected_requirements = tuple(
        requirement.requirement_id for requirement in policy.requirements
    )
    evaluated_requirements = tuple(
        item.requirement_id
        for item in evaluation.ordered_requirement_results
    )
    if evaluated_requirements != expected_requirements:
        raise AnalystReviewInputError(
            "Evaluation requirement identity/order differs from policy"
        )
    expected_facts = selected_policy_fact_names(selection)
    if (
        resolution.requested_facts != expected_facts
        or tuple(fact.fact_name for fact in resolution.facts)
        != expected_facts
    ):
        raise AnalystReviewInputError(
            "Fact resolution does not exactly cover selected-policy facts"
        )
    _validate_evaluation_fact_references(evaluation, resolution)
    _validate_recommendation_causes(recommendation, evaluation)

    return AnalystReviewInputs(
        recommendation=recommendation,
        evaluation=evaluation,
        resolution=resolution,
        selection=selection,
        case_run=case_run,
        case_run_content_hash=calculate_snapshot_content_hash(case_run),
        policy_selection_content_hash=calculate_snapshot_content_hash(
            selection
        ),
    )


def calculate_snapshot_content_hash(model: BaseModel) -> str:
    """Hash a complete immutable snapshot, including identity and time."""
    canonical = json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def calculate_policy_snapshot_sha256(policy: BaseModel) -> str:
    """Reproduce the controlled importer hash from a frozen policy model."""
    canonical_source = policy.model_dump_json(indent=2) + "\n"
    return sha256(canonical_source.encode("utf-8")).hexdigest()


def _validate_evaluation_fact_references(
    evaluation: RequirementEvaluationResult,
    resolution: FactResolutionResult,
) -> None:
    indexed_facts = {
        fact.fact_name: (index, fact)
        for index, fact in enumerate(resolution.facts)
    }
    for requirement in evaluation.ordered_requirement_results:
        binding_names: list[str] = []
        for binding in requirement.fact_bindings:
            if binding.fact_name in binding_names:
                raise AnalystReviewInputError(
                    "Evaluation contains a duplicated fact binding"
                )
            binding_names.append(binding.fact_name)
            indexed = indexed_facts.get(binding.fact_name)
            if indexed is None:
                raise AnalystReviewInputError(
                    "Evaluation references a fact absent from resolution: "
                    f"{binding.fact_name}"
                )
            _, fact = indexed
            expected_indexes = tuple(range(len(fact.resolved_values)))
            expected_provenance = tuple(
                provenance
                for value in fact.resolved_values
                for provenance in value.provenance
            )
            if (
                binding.fact_status != fact.status
                or binding.value_indexes != expected_indexes
                or binding.provenance_references != expected_provenance
            ):
                raise AnalystReviewInputError(
                    "Evaluation fact binding differs from authoritative "
                    f"resolution fact '{binding.fact_name}'"
                )
        unresolved = tuple(
            binding.fact_name
            for binding in requirement.fact_bindings
            if binding.fact_status != "FOUND"
        )
        if unresolved != requirement.missing_or_unresolved_facts:
            raise AnalystReviewInputError(
                "Evaluation unresolved-fact references are inconsistent "
                f"for requirement '{requirement.requirement_id}'"
            )


def _validate_recommendation_causes(
    recommendation: RecommendationResult,
    evaluation: RequirementEvaluationResult,
) -> None:
    expected_basis = recommendation.basis_codes[0]
    causal_statuses, expected_recommendation = {
        RecommendationBasisCode.ALL_MANDATORY_REQUIREMENTS_SATISFIED: (
            (RequirementStatus.SATISFIED,),
            Recommendation.RECOMMEND_REPRESENT,
        ),
        RecommendationBasisCode.MISCODING_EXCEPTION_ESTABLISHED: (
            (RequirementStatus.SATISFIED,),
            Recommendation.RECOMMEND_REPRESENT,
        ),
        RecommendationBasisCode.MANDATORY_REQUIREMENT_NOT_SATISFIED: (
            (RequirementStatus.NOT_SATISFIED,),
            Recommendation.RECOMMEND_DO_NOT_REPRESENT,
        ),
        RecommendationBasisCode.DEFAULT_ACCEPT_LIABILITY_UNLESS_EXCEPTION_ESTABLISHED: (
            (
                RequirementStatus.NOT_SATISFIED,
                RequirementStatus.NOT_ESTABLISHED,
            ),
            Recommendation.RECOMMEND_DO_NOT_REPRESENT,
        ),
        RecommendationBasisCode.MANDATORY_REQUIREMENT_NOT_ESTABLISHED: (
            (RequirementStatus.NOT_ESTABLISHED,),
            Recommendation.INSUFFICIENT_EVIDENCE_TO_REPRESENT,
        ),
    }[expected_basis]
    if recommendation.recommendation is not expected_recommendation:
        raise AnalystReviewInputError(
            "Recommendation basis disagrees with its stored decision"
        )
    represented_statuses = tuple(
        dict.fromkeys(
            item.status
            for item in recommendation.causal_requirement_results
        )
    )
    if (
        not represented_statuses
        or any(status not in causal_statuses for status in represented_statuses)
    ):
        raise AnalystReviewInputError(
            "Recommendation causes use statuses not allowed by its basis"
        )
    expected_causes = tuple(
        CausalRequirementResult(
            requirement_id=item.requirement_id,
            requirement_order=item.requirement_order,
            status=item.status,
            evaluation_basis_codes=item.basis_codes,
        )
        for item in evaluation.ordered_requirement_results
        if item.status in represented_statuses
    )
    if recommendation.causal_requirement_results != expected_causes:
        raise AnalystReviewInputError(
            "Recommendation causes disagree with evaluation outcomes"
        )
