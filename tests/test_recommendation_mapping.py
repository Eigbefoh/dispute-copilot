"""Tests for pure deterministic recommendation mapping."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from dispute_copilot.evaluation.models import (
    OverallEvaluationStatus,
    RequirementEvaluation,
    RequirementEvaluationResult,
    RequirementStatus,
    TruthValue,
    calculate_evaluation_content_hash,
)
from dispute_copilot.recommendation.errors import (
    MappingContractError,
    RecommendationInputError,
)
from dispute_copilot.recommendation.guards import recompute_overall_status
from dispute_copilot.recommendation.mapper import map_recommendation
from dispute_copilot.recommendation.mapping_models import (
    Recommendation,
    RecommendationMappingManifest,
)
from dispute_copilot.recommendation.registry import (
    load_recommendation_mapping,
)

PROJECT_ROOT = Path(__file__).parents[1]
MAPPING_ROOT = PROJECT_ROOT / "data" / "recommendation-mappings"
MAPPING_KEY = "default-representment-eligibility-v1"
MAPPING_VERSION = "1.0.0"


def evaluation(
    statuses: tuple[RequirementStatus, ...],
    *,
    overall: OverallEvaluationStatus | None = None,
    policy_id: str = "test-policy",
    rulebook_version: str = "test-v1",
) -> RequirementEvaluationResult:
    results = tuple(
        RequirementEvaluation(
            requirement_id=f"R{order}",
            requirement_order=order,
            status=status,
            predicate_result={
                RequirementStatus.SATISFIED: TruthValue.TRUE,
                RequirementStatus.NOT_SATISFIED: TruthValue.FALSE,
                RequirementStatus.NOT_ESTABLISHED: TruthValue.UNKNOWN,
            }[status],
            basis_codes=("TEST_BASIS",),
            fact_bindings=(),
            missing_or_unresolved_facts=(
                (f"fact_{order}",)
                if status is RequirementStatus.NOT_ESTABLISHED
                else ()
            ),
        )
        for order, status in enumerate(statuses, start=1)
    )
    aggregate = overall
    if aggregate is None:
        if RequirementStatus.NOT_SATISFIED in statuses:
            aggregate = (
                OverallEvaluationStatus
                .ONE_OR_MORE_REQUIREMENTS_NOT_SATISFIED
            )
        elif RequirementStatus.NOT_ESTABLISHED in statuses:
            aggregate = (
                OverallEvaluationStatus
                .ONE_OR_MORE_REQUIREMENTS_NOT_ESTABLISHED
            )
        else:
            aggregate = (
                OverallEvaluationStatus.ALL_REQUIREMENTS_SATISFIED
            )
    fields = {
        "evaluation_id": uuid4(),
        "run_id": uuid4(),
        "selection_id": uuid4(),
        "resolution_id": uuid4(),
        "policy_id": policy_id,
        "rulebook_version": rulebook_version,
        "policy_sha256": "a" * 64,
        "fact_resolution_content_hash": "b" * 64,
        "predicate_manifest_sha256": "c" * 64,
        "evaluator_version": "test-evaluator/1",
        "predicate_schema_version": "test-predicates/1",
        "ordered_requirement_results": results,
        "overall_status": aggregate,
        "status": "READY_FOR_RECOMMENDATION",
        "created_at": datetime.now(timezone.utc),
    }
    draft = RequirementEvaluationResult.model_construct(
        **fields,
        content_hash="0" * 64,
    )
    return RequirementEvaluationResult(
        **fields,
        content_hash=calculate_evaluation_content_hash(draft),
    )


@pytest.fixture
def manifest() -> RecommendationMappingManifest:
    return load_recommendation_mapping(
        mapping_key=MAPPING_KEY,
        mapping_version=MAPPING_VERSION,
        mapping_root=MAPPING_ROOT,
    )


@pytest.mark.parametrize(
    ("statuses", "expected", "causal_ids"),
    [
        (
            (
                RequirementStatus.SATISFIED,
                RequirementStatus.SATISFIED,
            ),
            Recommendation.RECOMMEND_REPRESENT,
            ("R1", "R2"),
        ),
        (
            (
                RequirementStatus.SATISFIED,
                RequirementStatus.NOT_SATISFIED,
                RequirementStatus.NOT_ESTABLISHED,
            ),
            Recommendation.RECOMMEND_DO_NOT_REPRESENT,
            ("R2",),
        ),
        (
            (
                RequirementStatus.SATISFIED,
                RequirementStatus.NOT_ESTABLISHED,
                RequirementStatus.NOT_ESTABLISHED,
            ),
            Recommendation.INSUFFICIENT_EVIDENCE_TO_REPRESENT,
            ("R2", "R3"),
        ),
    ],
)
def test_all_aggregates_map_exactly_and_preserve_causal_order(
    manifest: RecommendationMappingManifest,
    statuses: tuple[RequirementStatus, ...],
    expected: Recommendation,
    causal_ids: tuple[str, ...],
) -> None:
    source = evaluation(statuses)
    aggregate = recompute_overall_status(source)

    result = map_recommendation(
        evaluation=source,
        aggregate=aggregate,
        manifest=manifest,
    )

    assert result.recommendation is expected
    assert tuple(
        item.requirement_id
        for item in result.causal_requirement_results
    ) == causal_ids
    assert result.evaluation_id == source.evaluation_id
    assert result.evaluation_content_hash == source.content_hash
    assert result.status == "READY_FOR_ANALYST_REVIEW"


def test_not_satisfied_precedes_not_established() -> None:
    source = evaluation(
        (
            RequirementStatus.NOT_ESTABLISHED,
            RequirementStatus.NOT_SATISFIED,
        )
    )

    assert recompute_overall_status(source) is (
        OverallEvaluationStatus
        .ONE_OR_MORE_REQUIREMENTS_NOT_SATISFIED
    )


def test_recomputed_aggregate_rejects_empty_or_inconsistent_state() -> None:
    source = evaluation((RequirementStatus.SATISFIED,))
    inconsistent = source.model_copy(
        update={
            "overall_status": (
                OverallEvaluationStatus
                .ONE_OR_MORE_REQUIREMENTS_NOT_ESTABLISHED
            )
        }
    )

    assert recompute_overall_status(inconsistent) is (
        OverallEvaluationStatus.ALL_REQUIREMENTS_SATISFIED
    )

    empty = source.model_copy(
        update={"ordered_requirement_results": ()}
    )
    with pytest.raises(RecommendationInputError):
        recompute_overall_status(empty)


def test_mapping_manifest_hash_and_identity_fail_closed(
    tmp_path: Path,
) -> None:
    source = (
        MAPPING_ROOT
        / MAPPING_KEY
        / MAPPING_VERSION
        / "manifest.json"
    )
    payload = json.loads(source.read_text())
    payload["ordered_rules"][0]["produce"] = "RECOMMEND_REPRESENT"
    destination = (
        tmp_path
        / MAPPING_KEY
        / MAPPING_VERSION
        / "manifest.json"
    )
    destination.parent.mkdir(parents=True)
    destination.write_text(json.dumps(payload))

    with pytest.raises(
        MappingContractError,
        match="integrity_hash|failed validation",
    ):
        load_recommendation_mapping(
            mapping_key=MAPPING_KEY,
            mapping_version=MAPPING_VERSION,
            mapping_root=tmp_path,
        )


def test_duplicate_mapping_condition_is_rejected() -> None:
    source = (
        MAPPING_ROOT
        / MAPPING_KEY
        / MAPPING_VERSION
        / "manifest.json"
    )
    payload = json.loads(source.read_text())
    payload["ordered_rules"][1][
        "when_overall_evaluation_status"
    ] = payload["ordered_rules"][0][
        "when_overall_evaluation_status"
    ]

    with pytest.raises(ValidationError, match="unique|cover"):
        RecommendationMappingManifest.model_validate(payload)


def test_semantic_recommendation_hash_ignores_generated_identity(
    manifest: RecommendationMappingManifest,
) -> None:
    source = evaluation((RequirementStatus.SATISFIED,))
    aggregate = recompute_overall_status(source)

    first = map_recommendation(
        evaluation=source,
        aggregate=aggregate,
        manifest=manifest,
    )
    second = map_recommendation(
        evaluation=source,
        aggregate=aggregate,
        manifest=manifest,
    )

    assert first.recommendation_id != second.recommendation_id
    assert first.created_at != second.created_at
    assert first.content_hash == second.content_hash


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (
            RequirementStatus.SATISFIED,
            Recommendation.RECOMMEND_REPRESENT,
        ),
        (
            RequirementStatus.NOT_SATISFIED,
            Recommendation.RECOMMEND_DO_NOT_REPRESENT,
        ),
        (
            RequirementStatus.NOT_ESTABLISHED,
            Recommendation.RECOMMEND_DO_NOT_REPRESENT,
        ),
    ],
)
def test_visa_10_5_v4_uses_default_liability_unless_exception_is_proven(
    status: RequirementStatus,
    expected: Recommendation,
) -> None:
    manifest = load_recommendation_mapping(
        mapping_key="policy-aware-representment-eligibility-v2",
        mapping_version="2.0.0",
        mapping_root=MAPPING_ROOT,
    )
    source = evaluation(
        (status,),
        policy_id="visa-10.5-take-home-v4",
        rulebook_version="take-home-v4",
    )

    result = map_recommendation(
        evaluation=source,
        aggregate=recompute_overall_status(source),
        manifest=manifest,
    )

    assert result.recommendation is expected
