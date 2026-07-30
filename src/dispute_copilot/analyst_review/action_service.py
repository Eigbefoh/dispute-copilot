"""Deterministically derive a separate analyst action from a frozen package."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dispute_copilot.analyst_review.action_models import (
    SuggestedActionType,
    SuggestedAnalystAction,
    calculate_suggested_action_hash,
)
from dispute_copilot.analyst_review.action_repository import (
    SQLiteSuggestedActionRepository,
)
from dispute_copilot.analyst_review.models import AnalystReviewPackage
from dispute_copilot.evaluation.repository import SQLiteEvaluationRepository

SUGGESTED_ACTION_RULES_VERSION = "evidence-gap-actions/1.0.0"
LIMITED_VIEW_BASIS = (
    "LIMITED_VIEW_CANNOT_ESTABLISH_MATERIAL_ITEM_CONDITION"
)


def create_suggested_analyst_action(
    *,
    package: AnalystReviewPackage,
    database_path: Path,
) -> SuggestedAnalystAction:
    repository = SQLiteSuggestedActionRepository(database_path)
    existing = repository.get_for_package(package.review_package_id)
    if existing is not None:
        return existing

    requested_items = [
        f"Provide {gap.display_label}."
        for gap in package.evidence_gaps
    ]
    causal_ids = [gap.requirement_id for gap in package.evidence_gaps]
    evaluation = SQLiteEvaluationRepository(database_path).get(
        package.lineage.evaluation_id
    )
    if evaluation is None:
        raise ValueError(
            "suggested action requires the linked persisted evaluation"
        )
    for requirement in evaluation.ordered_requirement_results:
        if LIMITED_VIEW_BASIS not in requirement.basis_codes:
            continue
        requested_items.append(
            "Provide a comprehensive condition assessment or targeted "
            "evidence covering the alleged product defect."
        )
        causal_ids.append(requirement.requirement_id)

    requested = tuple(dict.fromkeys(requested_items))
    causal = tuple(dict.fromkeys(causal_ids))
    action_type = (
        SuggestedActionType.REQUEST_SPECIFIC_EVIDENCE
        if requested
        else SuggestedActionType.NO_ADDITIONAL_ACTION
    )
    fields = {
        "action_id": uuid4(),
        "review_package_id": package.review_package_id,
        "action": action_type,
        "requested_items": requested,
        "causal_requirement_ids": causal,
        "generated_at": datetime.now(timezone.utc),
        "rules_version": SUGGESTED_ACTION_RULES_VERSION,
    }
    draft = SuggestedAnalystAction.model_construct(
        **fields,
        content_hash="0" * 64,
    )
    return repository.save(
        SuggestedAnalystAction(
            **fields,
            content_hash=calculate_suggested_action_hash(draft),
        )
    )
