"""Authoritative loading and revalidation of one human-review lineage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from dispute_copilot.analyst_review.guards import (
    AnalystReviewInputs,
    validate_analyst_review_inputs,
)
from dispute_copilot.analyst_review.errors import (
    AnalystReviewPersistenceConflictError,
)
from dispute_copilot.analyst_review.models import (
    AnalystReviewPackage,
    calculate_review_package_content_hash,
)
from dispute_copilot.analyst_review.repository import (
    SQLiteAnalystReviewPackageRepository,
)
from dispute_copilot.evaluation.repository import SQLiteEvaluationRepository
from dispute_copilot.extraction.bundle_loader import DEFAULT_DATABASE_PATH
from dispute_copilot.extraction.fact_resolution_repository import (
    SQLiteFactResolutionRepository,
)
from dispute_copilot.human_review.errors import ReviewContextError
from dispute_copilot.policy.repository import (
    SQLitePolicySelectionRepository,
)
from dispute_copilot.recommendation.repository import (
    SQLiteRecommendationRepository,
)
from dispute_copilot.repositories.sqlite import SQLiteCaseRunRepository


@dataclass(frozen=True)
class HumanReviewContext:
    package: AnalystReviewPackage
    inputs: AnalystReviewInputs
    effective_workflow_status: str | None


def load_human_review_context(
    *,
    review_package_id: UUID,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> HumanReviewContext:
    """Load and revalidate the exact immutable machine decision chain."""
    try:
        package = SQLiteAnalystReviewPackageRepository(database_path).get(
            review_package_id
        )
    except (
        AnalystReviewPersistenceConflictError,
        TypeError,
        ValueError,
    ) as error:
        raise ReviewContextError(
            "The persisted review package failed integrity validation"
        ) from error
    if package is None:
        raise ReviewContextError(
            f"Analyst review package '{review_package_id}' was not found"
        )
    if package.content_hash != calculate_review_package_content_hash(
        package
    ):
        raise ReviewContextError("Review-package content hash is invalid")

    recommendation_repository = SQLiteRecommendationRepository(
        database_path
    )
    try:
        recommendation = recommendation_repository.get(
            package.lineage.recommendation_id
        )
        evaluation = SQLiteEvaluationRepository(database_path).get(
            package.lineage.evaluation_id
        )
        resolution = SQLiteFactResolutionRepository(database_path).get(
            package.lineage.fact_resolution_id
        )
        selection = SQLitePolicySelectionRepository(database_path).get(
            package.lineage.selection_id
        )
        case_run = SQLiteCaseRunRepository(database_path).get(
            package.lineage.run_id
        )
    except (TypeError, ValueError) as error:
        raise ReviewContextError(
            "A linked human-review record failed contract validation"
        ) from error
    missing = tuple(
        name
        for name, value in (
            ("RecommendationResult", recommendation),
            ("RequirementEvaluationResult", evaluation),
            ("FactResolutionResult", resolution),
            ("PolicySelection", selection),
            ("CaseRun", case_run),
        )
        if value is None
    )
    if missing:
        raise ReviewContextError(
            "Missing authoritative review lineage: " + ", ".join(missing)
        )

    # The upstream guard validates the historical phase-entry state embedded
    # in the immutable package. Current workflow state is loaded separately
    # so the same package remains inspectable after a human decision.
    try:
        inputs = validate_analyst_review_inputs(
            recommendation=recommendation,
            evaluation=evaluation,
            resolution=resolution,
            selection=selection,
            case_run=case_run,
            workflow_status="READY_FOR_ANALYST_REVIEW",
        )
    except ValueError as error:
        raise ReviewContextError(
            "The review package has invalid upstream lineage"
        ) from error
    _validate_package_lineage(package=package, inputs=inputs)
    return HumanReviewContext(
        package=package,
        inputs=inputs,
        effective_workflow_status=(
            recommendation_repository.effective_status(
                package.lineage.selection_id
            )
        ),
    )


def _validate_package_lineage(
    *,
    package: AnalystReviewPackage,
    inputs: AnalystReviewInputs,
) -> None:
    lineage = package.lineage
    expected = (
        inputs.case_run.run_id,
        inputs.selection.selection_id,
        inputs.resolution.resolution_id,
        inputs.evaluation.evaluation_id,
        inputs.recommendation.recommendation_id,
        inputs.case_run_content_hash,
        inputs.policy_selection_content_hash,
        inputs.selection.policy_sha256,
        inputs.resolution.content_hash,
        inputs.evaluation.content_hash,
        inputs.recommendation.content_hash,
    )
    actual = (
        lineage.run_id,
        lineage.selection_id,
        lineage.fact_resolution_id,
        lineage.evaluation_id,
        lineage.recommendation_id,
        lineage.case_run_content_hash,
        lineage.policy_selection_content_hash,
        lineage.policy_integrity_hash,
        lineage.fact_resolution_content_hash,
        lineage.evaluation_content_hash,
        lineage.recommendation_content_hash,
    )
    if actual != expected:
        raise ReviewContextError(
            "Review-package lineage or integrity hashes do not match "
            "authoritative records"
        )
