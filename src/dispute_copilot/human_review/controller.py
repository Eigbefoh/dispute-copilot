"""Thin application controller for the human-review interface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from PIL.Image import Image

from dispute_copilot.extraction.bundle_loader import DEFAULT_DATABASE_PATH
from dispute_copilot.human_review.context import load_human_review_context
from dispute_copilot.human_review.decision_models import (
    AnalystDecision,
    AnalystDecisionCommand,
    AnalystAction,
    AnalystOutcome,
    ConfirmDecisionCommand,
    OverrideDecisionCommand,
    RequestEvidenceDecisionCommand,
    TrustedAnalystIdentity,
)
from dispute_copilot.human_review.decision_repository import (
    SQLiteAnalystDecisionRepository,
)
from dispute_copilot.human_review.decision_rules import (
    DEFAULT_DECISION_RULES_KEY,
    DEFAULT_DECISION_RULES_ROOT,
    DEFAULT_DECISION_RULES_VERSION,
    load_decision_rules,
)
from dispute_copilot.human_review.decision_service import (
    AnalystDecisionPreview,
    preview_analyst_decision,
    record_analyst_decision,
)
from dispute_copilot.human_review.display_adapter import (
    EvidencePresentationAdapter,
    ResolvedDisplayTarget,
)
from dispute_copilot.human_review.renderer import EvidenceRenderer

DisplayKind = Literal["requirement", "source", "gap"]


@dataclass(frozen=True)
class DisplaySelection:
    kind: DisplayKind
    item_id: str
    requirement_id: str | None = None
    page_number: int | None = None


@dataclass(frozen=True)
class EvidencePanel:
    image: Image | None
    metadata_markdown: str
    selection: DisplaySelection
    can_go_previous: bool
    can_go_next: bool


class HumanReviewController:
    """Coordinate persisted contracts without embedding policy logic."""

    def __init__(
        self,
        *,
        review_package_id: UUID,
        analyst_identity: TrustedAnalystIdentity,
        database_path: Path = DEFAULT_DATABASE_PATH,
        project_root: Path = Path("."),
        decision_rules_root: Path = DEFAULT_DECISION_RULES_ROOT,
        decision_rules_key: str = DEFAULT_DECISION_RULES_KEY,
        decision_rules_version: str = DEFAULT_DECISION_RULES_VERSION,
    ) -> None:
        self.review_package_id = review_package_id
        self.analyst_identity = analyst_identity
        self.database_path = database_path
        self.decision_rules_root = decision_rules_root
        self.decision_rules_key = decision_rules_key
        self.decision_rules_version = decision_rules_version
        self.adapter = EvidencePresentationAdapter(
            database_path=database_path,
            project_root=project_root,
        )
        self.renderer = EvidenceRenderer()

    def load_context(self):
        return load_human_review_context(
            review_package_id=self.review_package_id,
            database_path=self.database_path,
        )

    def existing_decision(self) -> AnalystDecision | None:
        return SQLiteAnalystDecisionRepository(
            self.database_path
        ).get_for_review_package(self.review_package_id)

    def decision_rules(self):
        return load_decision_rules(
            decision_rules_key=self.decision_rules_key,
            decision_rules_version=self.decision_rules_version,
            decision_rules_root=self.decision_rules_root,
        )

    def inspect(self, selection: DisplaySelection) -> EvidencePanel:
        resolved = self._resolve_target(selection)
        target = resolved.target
        page_number = selection.page_number or target.page_number
        image = None
        can_go_previous = False
        can_go_next = False
        if resolved.source_path is not None and page_number is not None:
            rendered = self.renderer.render(
                target=target,
                source_path=resolved.source_path,
                page_number=page_number,
            )
            image = rendered.image
            can_go_previous = page_number > 1
            can_go_next = page_number < rendered.page_count
        normalized_selection = DisplaySelection(
            kind=selection.kind,
            item_id=selection.item_id,
            requirement_id=selection.requirement_id,
            page_number=page_number,
        )
        return EvidencePanel(
            image=image,
            metadata_markdown=_target_markdown(target, page_number),
            selection=normalized_selection,
            can_go_previous=can_go_previous,
            can_go_next=can_go_next,
        )

    def change_page(
        self,
        selection: DisplaySelection,
        delta: int,
    ) -> EvidencePanel:
        current_page = selection.page_number
        if current_page is None:
            return self.inspect(selection)
        return self.inspect(
            DisplaySelection(
                kind=selection.kind,
                item_id=selection.item_id,
                requirement_id=selection.requirement_id,
                page_number=current_page + delta,
            )
        )

    def prepare_command(
        self,
        *,
        action: str,
        selected_outcome: str | None,
        override_reason_code: str | None,
        analyst_comment: str | None,
        selected_gap_ids: tuple[str, ...],
    ) -> tuple[AnalystDecisionCommand, AnalystDecisionPreview]:
        package = self.load_context().package
        common = {
            "review_package_id": package.review_package_id,
            "expected_review_package_hash": package.content_hash,
            "analyst_comment": analyst_comment or None,
            "idempotency_key": f"ui:{uuid4()}",
        }
        if action == "CONFIRM":
            command: AnalystDecisionCommand = ConfirmDecisionCommand(
                action=AnalystAction.CONFIRM,
                **common,
            )
        elif action == "OVERRIDE":
            if selected_outcome is None:
                raise ValueError("Select an override outcome")
            if override_reason_code is None:
                raise ValueError("Select an override reason")
            command = OverrideDecisionCommand(
                action=AnalystAction.OVERRIDE,
                selected_outcome=AnalystOutcome(selected_outcome),
                override_reason_code=override_reason_code,
                **common,
            )
        elif action == "REQUEST_MORE_EVIDENCE":
            command = RequestEvidenceDecisionCommand(
                action=AnalystAction.REQUEST_MORE_EVIDENCE,
                selected_gap_ids=selected_gap_ids,
                **common,
            )
        else:
            raise ValueError("Select a final analyst action")
        preview = preview_analyst_decision(
            command=command,
            database_path=self.database_path,
            decision_rules_key=self.decision_rules_key,
            decision_rules_version=self.decision_rules_version,
            decision_rules_root=self.decision_rules_root,
        )
        return command, preview

    def submit(
        self,
        command: AnalystDecisionCommand,
    ) -> AnalystDecision:
        return record_analyst_decision(
            command=command,
            analyst_identity=self.analyst_identity,
            database_path=self.database_path,
            decision_rules_key=self.decision_rules_key,
            decision_rules_version=self.decision_rules_version,
            decision_rules_root=self.decision_rules_root,
        )

    def _resolve_target(
        self,
        selection: DisplaySelection,
    ) -> ResolvedDisplayTarget:
        if selection.kind == "source":
            return self.adapter.for_source(
                review_package_id=self.review_package_id,
                source_ref_id=selection.item_id,
                requirement_id=selection.requirement_id,
            )
        if selection.kind == "gap":
            return self.adapter.for_gap(
                review_package_id=self.review_package_id,
                gap_id=selection.item_id,
            )
        return self.adapter.for_requirement(
            review_package_id=self.review_package_id,
            requirement_id=selection.item_id,
        )


def _target_markdown(target, page_number: int | None) -> str:
    lines = [
        f"### {target.label}",
        f"**Evidence type:** `{target.evidence_type.value}`",
        f"**Highlight precision:** `{target.highlight_status.value}`",
    ]
    if target.source_file_name is not None:
        lines.append(f"**Source:** `{target.source_file_name}`")
    if page_number is not None and target.page_count is not None:
        lines.append(f"**Page:** {page_number} of {target.page_count}")
    if target.extracted_or_admitted_value is not None:
        lines.append(
            "**Admitted value:** "
            f"{target.extracted_or_admitted_value.value}"
        )
    if target.supporting_excerpt:
        lines.append(f"**Supporting content:** {target.supporting_excerpt}")
    lines.append(f"\n> {target.analyst_message}")
    if target.inspected_source_summaries:
        lines.append(
            "\n**Sources inspected during extraction:**\n"
            + "\n".join(
                f"- {summary}"
                for summary in target.inspected_source_summaries
            )
        )
    return "\n\n".join(lines)
