"""Offline orchestration for deterministic review-package assembly."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from dispute_copilot.analyst_review.assembler import (
    assemble_analyst_review_package,
)
from dispute_copilot.analyst_review.errors import (
    AnalystReviewInputError,
)
from dispute_copilot.analyst_review.guards import (
    validate_analyst_review_inputs,
)
from dispute_copilot.analyst_review.models import AnalystReviewPackage
from dispute_copilot.analyst_review.registry import (
    DEFAULT_PRESENTATION_RULES_ROOT,
    load_presentation_rules,
)
from dispute_copilot.analyst_review.repository import (
    SQLiteAnalystReviewPackageRepository,
)
from dispute_copilot.evaluation.repository import (
    SQLiteEvaluationRepository,
)
from dispute_copilot.extraction.bundle_loader import DEFAULT_DATABASE_PATH
from dispute_copilot.extraction.fact_resolution_repository import (
    SQLiteFactResolutionRepository,
)
from dispute_copilot.policy.repository import (
    SQLitePolicySelectionRepository,
)
from dispute_copilot.recommendation.repository import (
    SQLiteRecommendationRepository,
)
from dispute_copilot.repositories.sqlite import SQLiteCaseRunRepository


def create_analyst_review_package(
    *,
    presentation_rules_key: str,
    presentation_rules_version: str,
    recommendation_id: UUID | None = None,
    selection_id: UUID | None = None,
    database_path: Path = DEFAULT_DATABASE_PATH,
    presentation_rules_root: Path = DEFAULT_PRESENTATION_RULES_ROOT,
) -> AnalystReviewPackage:
    """Create one immutable UI snapshot without changing workflow state."""
    if (recommendation_id is None) == (selection_id is None):
        raise AnalystReviewInputError(
            "Provide exactly one of recommendation_id or selection_id"
        )

    try:
        recommendation_repository = SQLiteRecommendationRepository(
            database_path
        )
        recommendation = (
            recommendation_repository.get(recommendation_id)
            if recommendation_id is not None
            else recommendation_repository.get_for_selection(
                selection_id  # type: ignore[arg-type]
            )
        )
    except (TypeError, ValueError) as error:
        raise AnalystReviewInputError(
            "The persisted recommendation failed contract validation"
        ) from error
    if recommendation is None:
        raise AnalystReviewInputError(
            "Authoritative RecommendationResult was not found"
        )

    try:
        evaluation = SQLiteEvaluationRepository(database_path).get(
            recommendation.evaluation_id
        )
        resolution = SQLiteFactResolutionRepository(database_path).get(
            recommendation.resolution_id
        )
        selection = SQLitePolicySelectionRepository(database_path).get(
            recommendation.selection_id
        )
        case_run = SQLiteCaseRunRepository(database_path).get(
            recommendation.run_id
        )
    except (TypeError, ValueError) as error:
        raise AnalystReviewInputError(
            "A linked persisted record failed contract validation"
        ) from error
    missing = tuple(
        name
        for name, value in (
            ("RequirementEvaluationResult", evaluation),
            ("FactResolutionResult", resolution),
            ("PolicySelection", selection),
            ("CaseRun", case_run),
        )
        if value is None
    )
    if missing:
        raise AnalystReviewInputError(
            "Missing authoritative linked records: " + ", ".join(missing)
        )
    inputs = validate_analyst_review_inputs(
        recommendation=recommendation,
        evaluation=evaluation,
        resolution=resolution,
        selection=selection,
        case_run=case_run,
        workflow_status=recommendation_repository.effective_status(
            recommendation.selection_id
        ),
    )
    presentation_rules = load_presentation_rules(
        presentation_rules_key=presentation_rules_key,
        presentation_rules_version=presentation_rules_version,
        presentation_rules_root=presentation_rules_root,
    )
    package = assemble_analyst_review_package(
        inputs=inputs,
        presentation_rules=presentation_rules,
    )
    return SQLiteAnalystReviewPackageRepository(database_path).save(package)
