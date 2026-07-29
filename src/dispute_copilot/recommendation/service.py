"""Offline orchestration for deterministic recommendation mapping."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from dispute_copilot.evaluation.repository import (
    SQLiteEvaluationRepository,
)
from dispute_copilot.extraction.bundle_loader import DEFAULT_DATABASE_PATH
from dispute_copilot.policy.repository import (
    SQLitePolicySelectionRepository,
)
from dispute_copilot.recommendation.errors import (
    RecommendationInputError,
    RecommendationPersistenceConflictError,
)
from dispute_copilot.recommendation.guards import (
    validate_recommendation_input,
)
from dispute_copilot.recommendation.mapper import map_recommendation
from dispute_copilot.recommendation.models import RecommendationResult
from dispute_copilot.recommendation.registry import (
    DEFAULT_MAPPING_ROOT,
    load_recommendation_mapping,
)
from dispute_copilot.recommendation.repository import (
    SQLiteRecommendationRepository,
)


def create_recommendation(
    *,
    mapping_key: str,
    mapping_version: str,
    selection_id: UUID | None = None,
    evaluation_id: UUID | None = None,
    database_path: Path = DEFAULT_DATABASE_PATH,
    mapping_root: Path = DEFAULT_MAPPING_ROOT,
) -> RecommendationResult:
    """Map one authoritative evaluation without earlier-stage access."""
    if (selection_id is None) == (evaluation_id is None):
        raise RecommendationInputError(
            "Provide exactly one of selection_id or evaluation_id"
        )

    evaluation_repository = SQLiteEvaluationRepository(database_path)
    evaluation = (
        evaluation_repository.get_for_selection(selection_id)
        if selection_id is not None
        else evaluation_repository.get(evaluation_id)  # type: ignore[arg-type]
    )
    if evaluation is None:
        raise RecommendationInputError(
            "Authoritative RequirementEvaluationResult was not found"
        )

    recommendation_repository = SQLiteRecommendationRepository(
        database_path
    )
    existing = recommendation_repository.get_for_evaluation(
        evaluation.evaluation_id
    )
    if existing is not None:
        if (
            existing.mapping_key == mapping_key
            and existing.mapping_version == mapping_version
        ):
            return existing
        raise RecommendationPersistenceConflictError(
            "Evaluation already has a recommendation under a different "
            "mapping identity"
        )

    selection = SQLitePolicySelectionRepository(database_path).get(
        evaluation.selection_id
    )
    if selection is None:
        raise RecommendationInputError(
            "Evaluation references a missing PolicySelection"
        )
    aggregate = validate_recommendation_input(
        evaluation=evaluation,
        selection=selection,
        workflow_status=evaluation_repository.effective_status(
            evaluation.selection_id
        ),
    )
    manifest = load_recommendation_mapping(
        mapping_key=mapping_key,
        mapping_version=mapping_version,
        mapping_root=mapping_root,
    )
    result = map_recommendation(
        evaluation=evaluation,
        aggregate=aggregate,
        manifest=manifest,
    )
    return recommendation_repository.save_with_progression(result)
