"""Deterministic validation and persistence of final analyst actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dispute_copilot.extraction.bundle_loader import DEFAULT_DATABASE_PATH
from dispute_copilot.human_review.context import load_human_review_context
from dispute_copilot.human_review.decision_models import (
    ANALYST_DECISION_SCHEMA_VERSION,
    AnalystAction,
    AnalystDecision,
    AnalystDecisionCommand,
    AnalystDecisionLineage,
    AnalystOutcome,
    AnalystWorkflowStatus,
    ConfirmDecisionCommand,
    OverrideDecisionCommand,
    RequestEvidenceDecisionCommand,
    TrustedAnalystIdentity,
    calculate_analyst_decision_content_hash,
)
from dispute_copilot.human_review.decision_repository import (
    SQLiteAnalystDecisionRepository,
)
from dispute_copilot.human_review.decision_rules import (
    DEFAULT_DECISION_RULES_KEY,
    DEFAULT_DECISION_RULES_ROOT,
    DEFAULT_DECISION_RULES_VERSION,
    DecisionRulesManifest,
    load_decision_rules,
)
from dispute_copilot.human_review.errors import (
    DecisionValidationError,
    StaleReviewPackageError,
)


@dataclass(frozen=True)
class AnalystDecisionPreview:
    """Non-persisted, deterministic summary used for explicit UI review."""

    action: AnalystAction
    selected_outcome: AnalystOutcome
    next_workflow_status: str
    selected_gap_ids: tuple[str, ...]
    reason_code: str | None
    analyst_comment: str | None


@dataclass(frozen=True)
class _ResolvedDecisionFields:
    selected_outcome: AnalystOutcome
    next_workflow_status: AnalystWorkflowStatus
    selected_gap_ids: tuple[str, ...]
    reason_code: str | None
    analyst_comment: str | None


def preview_analyst_decision(
    *,
    command: AnalystDecisionCommand,
    database_path: Path = DEFAULT_DATABASE_PATH,
    decision_rules_key: str = DEFAULT_DECISION_RULES_KEY,
    decision_rules_version: str = DEFAULT_DECISION_RULES_VERSION,
    decision_rules_root: Path = DEFAULT_DECISION_RULES_ROOT,
) -> AnalystDecisionPreview:
    """Validate a pending command without writing a decision or event."""
    context = load_human_review_context(
        review_package_id=command.review_package_id,
        database_path=database_path,
    )
    if (
        command.expected_review_package_hash
        != context.package.content_hash
    ):
        raise StaleReviewPackageError(
            "The displayed review package is stale; reload before deciding"
        )
    rules = load_decision_rules(
        decision_rules_key=decision_rules_key,
        decision_rules_version=decision_rules_version,
        decision_rules_root=decision_rules_root,
    )
    fields = _resolve_decision_fields(
        command=command,
        package=context.package,
        rules=rules,
    )
    return AnalystDecisionPreview(
        action=AnalystAction(command.action),
        selected_outcome=fields.selected_outcome,
        next_workflow_status=fields.next_workflow_status.value,
        selected_gap_ids=fields.selected_gap_ids,
        reason_code=fields.reason_code,
        analyst_comment=fields.analyst_comment,
    )


def record_analyst_decision(
    *,
    command: AnalystDecisionCommand,
    analyst_identity: TrustedAnalystIdentity,
    database_path: Path = DEFAULT_DATABASE_PATH,
    decision_rules_key: str = DEFAULT_DECISION_RULES_KEY,
    decision_rules_version: str = DEFAULT_DECISION_RULES_VERSION,
    decision_rules_root: Path = DEFAULT_DECISION_RULES_ROOT,
) -> AnalystDecision:
    """Validate against authoritative state, then atomically commit."""
    context = load_human_review_context(
        review_package_id=command.review_package_id,
        database_path=database_path,
    )
    package = context.package
    if command.expected_review_package_hash != package.content_hash:
        raise StaleReviewPackageError(
            "The displayed review package is stale; reload before deciding"
        )
    rules = load_decision_rules(
        decision_rules_key=decision_rules_key,
        decision_rules_version=decision_rules_version,
        decision_rules_root=decision_rules_root,
    )
    decision = _build_decision(
        command=command,
        analyst_identity=analyst_identity,
        package=package,
        rules=rules,
    )
    return SQLiteAnalystDecisionRepository(
        database_path
    ).save_with_transition(decision)


def _build_decision(
    *,
    command: AnalystDecisionCommand,
    analyst_identity: TrustedAnalystIdentity,
    package,
    rules: DecisionRulesManifest,
) -> AnalystDecision:
    resolved = _resolve_decision_fields(
        command=command,
        package=package,
        rules=rules,
    )
    decided_at = datetime.now(timezone.utc)
    fields = {
        "analyst_decision_id": uuid4(),
        "review_package_id": package.review_package_id,
        "review_package_hash": package.content_hash,
        "recommendation_id": package.lineage.recommendation_id,
        "system_recommendation": package.recommendation.decision,
        "action": AnalystAction(command.action),
        "selected_outcome": resolved.selected_outcome,
        "reason_code": resolved.reason_code,
        "analyst_comment": resolved.analyst_comment,
        "selected_gap_ids": resolved.selected_gap_ids,
        "decided_by": analyst_identity.subject,
        "decided_at": decided_at,
        "decision_rules_key": rules.decision_rules_key,
        "decision_rules_version": rules.decision_rules_version,
        "decision_rules_integrity_hash": rules.integrity_hash,
        "idempotency_key": command.idempotency_key,
        "next_workflow_status": resolved.next_workflow_status,
        "lineage": AnalystDecisionLineage(
            case_run_id=package.lineage.run_id,
            policy_selection_id=package.lineage.selection_id,
            fact_resolution_id=package.lineage.fact_resolution_id,
            evaluation_id=package.lineage.evaluation_id,
            recommendation_id=package.lineage.recommendation_id,
            review_package_id=package.review_package_id,
        ),
        "schema_version": ANALYST_DECISION_SCHEMA_VERSION,
    }
    draft = AnalystDecision.model_construct(
        **fields,
        content_hash="0" * 64,
    )
    return AnalystDecision(
        **fields,
        content_hash=calculate_analyst_decision_content_hash(draft),
    )


def _resolve_decision_fields(
    *,
    command: AnalystDecisionCommand,
    package,
    rules: DecisionRulesManifest,
) -> _ResolvedDecisionFields:
    implied_outcome = next(
        (
            item.confirmed_outcome
            for item in rules.recommendation_outcomes
            if item.recommendation == package.recommendation.decision
        ),
        None,
    )
    if implied_outcome is None:
        raise DecisionValidationError(
            "The recommendation has no configured human outcome"
        )

    comment = _sanitise_comment(command.analyst_comment)
    reason_code: str | None = None
    selected_gap_ids: tuple[str, ...] = ()
    if isinstance(command, ConfirmDecisionCommand):
        selected_outcome = implied_outcome
    elif isinstance(command, OverrideDecisionCommand):
        selected_outcome = command.selected_outcome
        if selected_outcome == implied_outcome:
            raise DecisionValidationError(
                "An override must differ from the system-implied outcome"
            )
        permitted_reasons = {
            reason.reason_code for reason in rules.override_reasons
        }
        if command.override_reason_code not in permitted_reasons:
            raise DecisionValidationError(
                "The override reason is not permitted by these rules"
            )
        if comment is None or len(comment) < 10:
            raise DecisionValidationError(
                "An override requires an explanation of at least 10 "
                "characters"
            )
        reason_code = command.override_reason_code
    elif isinstance(command, RequestEvidenceDecisionCommand):
        selected_outcome = AnalystOutcome.REQUEST_MORE_EVIDENCE
        package_gap_ids = tuple(
            gap.gap_id for gap in package.evidence_gaps
        )
        unknown = set(command.selected_gap_ids) - set(package_gap_ids)
        if unknown:
            raise DecisionValidationError(
                "Selected evidence gaps are not unresolved in this package"
            )
        selected = set(command.selected_gap_ids)
        selected_gap_ids = tuple(
            gap_id for gap_id in package_gap_ids if gap_id in selected
        )
    else:
        raise DecisionValidationError("Unsupported analyst action")

    if selected_outcome not in rules.permitted_outcomes:
        raise DecisionValidationError(
            "The selected outcome is not permitted by these rules"
        )
    next_status = next(
        (
            transition.next_status
            for transition in rules.workflow_transitions
            if transition.action == command.action
            and transition.outcome == selected_outcome
        ),
        None,
    )
    if next_status is None:
        raise DecisionValidationError(
            "No workflow transition exists for this analyst action"
        )
    return _ResolvedDecisionFields(
        selected_outcome=selected_outcome,
        next_workflow_status=next_status,
        selected_gap_ids=selected_gap_ids,
        reason_code=reason_code,
        analyst_comment=comment,
    )


def _sanitise_comment(comment: str | None) -> str | None:
    if comment is None:
        return None
    normalised = comment.strip()
    if not normalised:
        return None
    if any(
        ord(character) < 32
        and character not in {"\n", "\r", "\t"}
        for character in normalised
    ):
        raise DecisionValidationError(
            "Analyst comments cannot contain control characters"
        )
    return normalised
