"""Offline orchestration for deterministic requirement evaluation."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from dispute_copilot.evaluation.errors import EvaluationInputError
from dispute_copilot.evaluation.evaluator import evaluate_requirements
from dispute_copilot.evaluation.guards import validate_evaluation_inputs
from dispute_copilot.evaluation.models import RequirementEvaluationResult
from dispute_copilot.evaluation.predicate_catalogue import (
    DEFAULT_RULEBOOK_ROOT,
    load_predicate_manifest,
    select_and_validate_policy_predicates,
)
from dispute_copilot.evaluation.repository import (
    SQLiteEvaluationRepository,
)
from dispute_copilot.extraction.bundle_loader import DEFAULT_DATABASE_PATH
from dispute_copilot.extraction.fact_resolution_repository import (
    SQLiteFactResolutionRepository,
)
from dispute_copilot.policy.fact_models import FactCatalogue
from dispute_copilot.policy.repository import (
    SQLitePolicyRepository,
    SQLitePolicySelectionRepository,
)


def evaluate_selection_requirements(
    *,
    selection_id: UUID,
    database_path: Path = DEFAULT_DATABASE_PATH,
    rulebook_root: Path = DEFAULT_RULEBOOK_ROOT,
) -> RequirementEvaluationResult:
    """Evaluate an authoritative fact result with zero evidence/model access."""
    evaluation_repository = SQLiteEvaluationRepository(database_path)
    existing = evaluation_repository.get_for_selection(selection_id)
    if existing is not None:
        return existing

    selection = SQLitePolicySelectionRepository(database_path).get(
        selection_id
    )
    if selection is None:
        raise EvaluationInputError(
            f"Policy selection '{selection_id}' was not found"
        )
    resolution = SQLiteFactResolutionRepository(
        database_path
    ).get_for_selection(selection_id)
    if resolution is None:
        raise EvaluationInputError(
            "No authoritative FactResolutionResult exists for selection "
            f"'{selection_id}'. Re-run fact resolution to persist it."
        )

    policy_repository = SQLitePolicyRepository(database_path)
    imported_policy = policy_repository.get_imported(
        selection.rulebook_version,
        selection.scheme,
        selection.reason_code,
    )
    if imported_policy is None:
        raise EvaluationInputError(
            "The exact imported policy is unavailable"
        )
    catalogue = FactCatalogue(
        rulebook_version=selection.rulebook_version,
        facts=policy_repository.list_fact_definitions(
            selection.rulebook_version
        ),
    )
    validate_evaluation_inputs(
        selection=selection,
        resolution=resolution,
        imported_policy=imported_policy,
        catalogue=catalogue,
    )
    manifest, manifest_sha256 = load_predicate_manifest(
        rulebook_version=selection.rulebook_version,
        rulebook_root=rulebook_root,
    )
    predicates = select_and_validate_policy_predicates(
        manifest=manifest,
        policy=selection.policy_snapshot,
        catalogue=catalogue,
    )
    result = evaluate_requirements(
        selection=selection,
        resolution=resolution,
        predicates=predicates,
        predicate_manifest_sha256=manifest_sha256,
        predicate_schema_version=manifest.predicate_schema_version,
    )
    return evaluation_repository.save_with_progression(result)
