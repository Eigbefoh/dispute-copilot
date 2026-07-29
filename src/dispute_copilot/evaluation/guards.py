"""Lineage and integrity guards executed before any predicate."""

from __future__ import annotations

from dispute_copilot.domain.policy_selection import PolicySelection
from dispute_copilot.evaluation.errors import EvaluationInputError
from dispute_copilot.extraction.fact_resolution_models import (
    FactResolutionResult,
    calculate_fact_resolution_content_hash,
)
from dispute_copilot.extraction.fact_resolution_validation import (
    selected_policy_fact_names,
)
from dispute_copilot.policy.fact_models import FactCatalogue
from dispute_copilot.policy.repository import ImportedPolicy


def validate_evaluation_inputs(
    *,
    selection: PolicySelection,
    resolution: FactResolutionResult,
    imported_policy: ImportedPolicy,
    catalogue: FactCatalogue,
) -> None:
    """Fail before predicate execution on any lineage inconsistency."""
    if resolution.status != "READY_FOR_EVALUATION":
        raise EvaluationInputError(
            "Fact resolution is not READY_FOR_EVALUATION"
        )
    if (
        resolution.selection_id != selection.selection_id
        or resolution.run_id != selection.run_id
    ):
        raise EvaluationInputError(
            "Fact resolution run/selection lineage does not match"
        )
    if (
        resolution.policy_id != selection.policy_id
        or resolution.rulebook_version != selection.rulebook_version
        or resolution.policy_sha256 != selection.policy_sha256
    ):
        raise EvaluationInputError(
            "Fact resolution policy lineage does not match selection"
        )
    if resolution.content_hash != (
        calculate_fact_resolution_content_hash(resolution)
    ):
        raise EvaluationInputError(
            "Fact resolution content hash is invalid"
        )
    if imported_policy.policy_sha256 != selection.policy_sha256:
        raise EvaluationInputError(
            "Imported policy hash does not match frozen selection"
        )
    if imported_policy.policy != selection.policy_snapshot:
        raise EvaluationInputError(
            "Imported policy differs from frozen policy snapshot"
        )
    if catalogue.rulebook_version != selection.rulebook_version:
        raise EvaluationInputError(
            "Fact catalogue version does not match selection"
        )
    expected_facts = selected_policy_fact_names(selection)
    if (
        resolution.requested_facts != expected_facts
        or tuple(fact.fact_name for fact in resolution.facts)
        != expected_facts
    ):
        raise EvaluationInputError(
            "Fact resolution does not exactly cover frozen policy facts"
        )
