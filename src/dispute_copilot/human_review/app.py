"""Local split-screen Gradio analyst-review interface."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
from uuid import UUID

import gradio as gr
from pydantic import TypeAdapter, ValidationError

from dispute_copilot.extraction.bundle_loader import DEFAULT_DATABASE_PATH
from dispute_copilot.human_review.controller import (
    DisplaySelection,
    HumanReviewController,
)
from dispute_copilot.human_review.decision_models import (
    AnalystDecisionCommand,
    AnalystOutcome,
)
from dispute_copilot.human_review.errors import HumanReviewError
from dispute_copilot.human_review.identity import (
    resolve_trusted_analyst_identity,
)

_COMMAND_ADAPTER = TypeAdapter(AnalystDecisionCommand)

ACTION_LABELS = {
    "Confirm recommendation": "CONFIRM",
    "Override recommendation": "OVERRIDE",
    "Request more evidence": "REQUEST_MORE_EVIDENCE",
    "Close without deciding": "DEFER_OR_CLOSE",
}


def create_app(
    *,
    review_package_id: UUID,
    database_path: Path = DEFAULT_DATABASE_PATH,
    project_root: Path = Path("."),
    analyst_identity=None,
) -> gr.Blocks:
    """Build a UI over one frozen review package."""
    identity = analyst_identity or resolve_trusted_analyst_identity()
    controller = HumanReviewController(
        review_package_id=review_package_id,
        analyst_identity=identity,
        database_path=database_path,
        project_root=project_root,
    )
    context = controller.load_context()
    package = context.package
    decision = controller.existing_decision()
    rules = controller.decision_rules()
    case = context.inputs.case_run.case_snapshot
    summary = _case_summary_markdown(package, case)
    recommendation = _recommendation_markdown(package)
    aggregation = _aggregation_markdown(package)
    rationale = "\n".join(
        f"- {sentence}" for sentence in package.rationale.sentences
    )
    gap_choices = [
        (f"{gap.requirement_id}: {gap.display_label}", gap.gap_id)
        for gap in package.evidence_gaps
    ]
    implied_outcome = next(
        item.confirmed_outcome
        for item in rules.recommendation_outcomes
        if item.recommendation == package.recommendation.decision
    )
    override_outcomes = [
        (outcome.value.replace("_", " ").title(), outcome.value)
        for outcome in rules.permitted_outcomes
        if outcome != implied_outcome
    ]
    reason_choices = [
        (item.display_label, item.reason_code)
        for item in rules.override_reasons
    ]
    initially_locked = decision is not None
    review_targets = []

    with gr.Blocks(
        title=f"Dispute review — {package.case_summary.case_id}",
        analytics_enabled=False,
    ) as app:
        selected_display = gr.State(value=None)
        prepared_command = gr.State(value=None)
        gr.Markdown("# Dispute Copilot — analyst review")
        with gr.Row(equal_height=False):
            with gr.Column(scale=1, min_width=420):
                gr.Markdown(summary)
                gr.Markdown(recommendation)
                if aggregation:
                    gr.Markdown(aggregation)
                gr.Markdown("### Deterministic rationale\n" + rationale)
                gr.Markdown("### Policy requirements")
                for requirement in package.requirement_checklist:
                    _build_requirement_accordion(
                        requirement=requirement,
                        package=package,
                        targets=review_targets,
                    )
                gr.Markdown("### Final analyst action")
                action = gr.Radio(
                    choices=list(ACTION_LABELS),
                    value=None,
                    label="Action",
                    info="No action is preselected.",
                    interactive=not initially_locked,
                )
                override_outcome = gr.Dropdown(
                    choices=override_outcomes,
                    value=None,
                    label="Override outcome",
                    visible=False,
                    interactive=not initially_locked,
                )
                override_reason = gr.Dropdown(
                    choices=reason_choices,
                    value=None,
                    label="Override reason",
                    visible=False,
                    interactive=not initially_locked,
                )
                selected_gaps = gr.CheckboxGroup(
                    choices=gap_choices,
                    value=[],
                    label="Unresolved evidence gaps",
                    visible=False,
                    interactive=not initially_locked,
                )
                analyst_comment = gr.Textbox(
                    label="Analyst comment",
                    lines=4,
                    max_lines=8,
                    interactive=not initially_locked,
                )
                with gr.Row():
                    review_action = gr.Button(
                        "Review action",
                        variant="primary",
                        interactive=not initially_locked,
                    )
                    close_action = gr.Button(
                        "Close without deciding",
                        interactive=not initially_locked,
                    )
                confirmation = gr.Markdown(
                    _decision_markdown(decision)
                    if decision is not None
                    else (
                        "Select an action, then review it before final "
                        "submission."
                    )
                )
                submit_action = gr.Button(
                    "Submit final decision",
                    variant="stop",
                    interactive=False,
                )
            with gr.Column(scale=1, min_width=520):
                gr.Markdown("## Source evidence")
                evidence_image = gr.Image(
                    type="pil",
                    interactive=False,
                    label="Verified source page",
                    height=620,
                )
                evidence_metadata = gr.Markdown(
                    "Select a requirement, supporting source or evidence "
                    "gap to inspect it."
                )
                with gr.Row():
                    previous_page = gr.Button(
                        "Previous page",
                        interactive=False,
                    )
                    next_page = gr.Button(
                        "Next page",
                        interactive=False,
                    )

        # Every button resolves a server-defined package target. The browser
        # never supplies a source path.
        for button, selection in review_targets:
            button.click(
                fn=lambda chosen=selection: _inspect_callback(
                    controller,
                    chosen,
                ),
                outputs=[
                    evidence_image,
                    evidence_metadata,
                    selected_display,
                    previous_page,
                    next_page,
                ],
            )

        previous_page.click(
            fn=lambda state: _page_callback(controller, state, -1),
            inputs=[selected_display],
            outputs=[
                evidence_image,
                evidence_metadata,
                selected_display,
                previous_page,
                next_page,
            ],
        )
        next_page.click(
            fn=lambda state: _page_callback(controller, state, 1),
            inputs=[selected_display],
            outputs=[
                evidence_image,
                evidence_metadata,
                selected_display,
                previous_page,
                next_page,
            ],
        )
        action.change(
            fn=_action_visibility,
            inputs=[action],
            outputs=[
                override_outcome,
                override_reason,
                selected_gaps,
                confirmation,
                submit_action,
                prepared_command,
            ],
        )
        for form_input in (
            override_outcome,
            override_reason,
            selected_gaps,
            analyst_comment,
        ):
            form_input.change(
                fn=_invalidate_preparation,
                outputs=[
                    confirmation,
                    submit_action,
                    prepared_command,
                ],
            )
        review_action.click(
            fn=lambda *values: _prepare_callback(controller, *values),
            inputs=[
                action,
                override_outcome,
                override_reason,
                analyst_comment,
                selected_gaps,
            ],
            outputs=[
                confirmation,
                submit_action,
                prepared_command,
            ],
        )
        close_action.click(
            fn=_close_callback,
            outputs=[
                confirmation,
                submit_action,
                prepared_command,
                action,
            ],
        )
        submit_action.click(
            fn=lambda state: _submit_callback(controller, state),
            inputs=[prepared_command],
            outputs=[
                confirmation,
                submit_action,
                prepared_command,
                action,
                review_action,
                close_action,
                override_outcome,
                override_reason,
                selected_gaps,
                analyst_comment,
            ],
        )
    return app


def _build_requirement_accordion(
    *,
    requirement,
    package,
    targets,
) -> None:
    status_icon = {
        "SATISFIED": "✓",
        "NOT_SATISFIED": "✕",
        "NOT_ESTABLISHED": "?",
        "NOT_APPLICABLE": "—",
    }[requirement.status.value]
    with gr.Accordion(
        f"{status_icon} {requirement.requirement_id}: "
        f"{requirement.status.value}",
        open=requirement.requirement_order == 1,
    ):
        gr.Markdown(requirement.requirement_text)
        gr.Markdown(
            "**Conclusion basis:** "
            + ", ".join(requirement.evaluation_basis_codes)
        )
        requirement_button = gr.Button(
            "Inspect requirement conclusion",
            size="sm",
        )
        targets.append(
            (
                requirement_button,
                DisplaySelection(
                    kind="requirement",
                    item_id=requirement.requirement_id,
                ),
            )
        )
        for source_ref in requirement.source_refs:
            source = next(
                item
                for item in package.supporting_sources
                if item.source_ref_id == source_ref
            )
            button = gr.Button(
                f"Source: {source.fact_name} — "
                f"{source.support_type.value}",
                size="sm",
            )
            targets.append(
                (
                    button,
                    DisplaySelection(
                        kind="source",
                        item_id=source_ref,
                        requirement_id=requirement.requirement_id,
                    ),
                )
            )
        for gap in package.evidence_gaps:
            if gap.requirement_id != requirement.requirement_id:
                continue
            button = gr.Button(
                f"Gap: {gap.display_label} — {gap.resolution_status}",
                size="sm",
            )
            targets.append(
                (
                    button,
                    DisplaySelection(
                        kind="gap",
                        item_id=gap.gap_id,
                        requirement_id=requirement.requirement_id,
                    ),
                )
            )


def _inspect_callback(controller, selection):
    try:
        panel = controller.inspect(selection)
        return _panel_outputs(panel)
    except HumanReviewError as error:
        return (
            None,
            f"### Evidence unavailable\n\n{error}",
            None,
            gr.Button(interactive=False),
            gr.Button(interactive=False),
        )


def _page_callback(controller, state, delta):
    if not state:
        return (
            None,
            "Select a source before changing pages.",
            None,
            gr.Button(interactive=False),
            gr.Button(interactive=False),
        )
    try:
        selection = DisplaySelection(**state)
        panel = controller.change_page(selection, delta)
        return _panel_outputs(panel)
    except (HumanReviewError, ValueError, TypeError) as error:
        return (
            None,
            f"### Page unavailable\n\n{error}",
            state,
            gr.Button(interactive=False),
            gr.Button(interactive=False),
        )


def _panel_outputs(panel):
    return (
        panel.image,
        panel.metadata_markdown,
        {
            "kind": panel.selection.kind,
            "item_id": panel.selection.item_id,
            "requirement_id": panel.selection.requirement_id,
            "page_number": panel.selection.page_number,
        },
        gr.Button(interactive=panel.can_go_previous),
        gr.Button(interactive=panel.can_go_next),
    )


def _action_visibility(label):
    action = ACTION_LABELS.get(label)
    return (
        gr.Dropdown(visible=action == "OVERRIDE", value=None),
        gr.Dropdown(visible=action == "OVERRIDE", value=None),
        gr.CheckboxGroup(
            visible=action == "REQUEST_MORE_EVIDENCE",
            value=[],
        ),
        "Review this action before final submission.",
        gr.Button(interactive=False),
        None,
    )


def _invalidate_preparation():
    return (
        "Inputs changed. Review the action again before final submission.",
        gr.Button(interactive=False),
        None,
    )


def _prepare_callback(
    controller,
    action_label,
    selected_outcome,
    override_reason,
    analyst_comment,
    selected_gaps,
):
    action = ACTION_LABELS.get(action_label)
    if action == "DEFER_OR_CLOSE":
        return _close_callback()[:3]
    try:
        command, preview = controller.prepare_command(
            action=action or "",
            selected_outcome=selected_outcome,
            override_reason_code=override_reason,
            analyst_comment=analyst_comment,
            selected_gap_ids=tuple(selected_gaps or ()),
        )
    except (HumanReviewError, ValidationError, ValueError) as error:
        return (
            f"### Action not ready\n\n{error}",
            gr.Button(interactive=False),
            None,
        )
    command_payload = command.model_dump(mode="json")
    return (
        _preview_markdown(preview),
        gr.Button(interactive=True),
        command_payload,
    )


def _submit_callback(controller, state):
    if state is None:
        return _submit_error_outputs(
            "Review the action before final submission."
        )
    try:
        command = _COMMAND_ADAPTER.validate_json(
            json.dumps(state)
        )
        decision = controller.submit(command)
    except (HumanReviewError, ValidationError, ValueError) as error:
        return _submit_error_outputs(str(error))
    locked = gr.Button(interactive=False)
    locked_dropdown = gr.Dropdown(interactive=False)
    locked_gaps = gr.CheckboxGroup(interactive=False)
    locked_comment = gr.Textbox(interactive=False)
    return (
        _decision_markdown(decision),
        locked,
        None,
        gr.Radio(value=None, interactive=False),
        locked,
        locked,
        locked_dropdown,
        locked_dropdown,
        locked_gaps,
        locked_comment,
    )


def _submit_error_outputs(message):
    return (
        f"### Decision not recorded\n\n{message}",
        gr.Button(interactive=False),
        None,
        gr.Radio(),
        gr.Button(),
        gr.Button(),
        gr.Dropdown(),
        gr.Dropdown(),
        gr.CheckboxGroup(),
        gr.Textbox(),
    )


def _close_callback():
    return (
        "Closed without deciding. No AnalystDecision or workflow event "
        "was created.",
        gr.Button(interactive=False),
        None,
        gr.Radio(value=None),
    )


def _preview_markdown(preview) -> str:
    lines = [
        "### Review final action",
        f"**Action:** `{preview.action.value}`",
        f"**Outcome:** `{preview.selected_outcome.value}`",
        f"**Next workflow status:** `{preview.next_workflow_status}`",
    ]
    if preview.reason_code:
        lines.append(f"**Override reason:** `{preview.reason_code}`")
    if preview.selected_gap_ids:
        lines.append(
            "**Evidence gaps:** "
            + ", ".join(preview.selected_gap_ids)
        )
    if preview.analyst_comment:
        lines.append(f"**Comment:** {preview.analyst_comment}")
    lines.append(
        "\nSubmitting creates one immutable final decision. This cannot "
        "be edited in this workflow."
    )
    return "\n\n".join(lines)


def _case_summary_markdown(package, case) -> str:
    amount = case.chargeback_amount
    return "\n".join(
        (
            "## Case",
            f"- **Case ID:** `{package.case_summary.case_id}`",
            f"- **Merchant:** {case.transaction.merchant_name}",
            f"- **Amount:** {amount.currency} {amount.value}",
            f"- **Scheme / reason:** "
            f"{package.case_summary.card_scheme} / "
            f"{package.case_summary.reason_code}",
            f"- **Workflow:** "
            f"`{package.case_summary.current_workflow_status}`",
        )
    )


def _recommendation_markdown(package) -> str:
    blocking = ", ".join(
        package.recommendation.causal_requirement_ids
    )
    return "\n".join(
        (
            "## Advisory system recommendation",
            f"**{package.recommendation.display_label}**",
            f"`{package.recommendation.decision.value}`",
            f"Causal requirements: {blocking}",
            "",
            "_Advisory only. This is not the analyst's final decision._",
        )
    )


def _aggregation_markdown(package) -> str:
    summary = package.aggregation
    if summary is None:
        return ""
    if summary.mode == "ANY_TWO":
        explanation = (
            f"{summary.satisfied_count} qualifying routes are established; "
            f"{summary.maximum_possible_satisfied} could be established; "
            f"{summary.minimum_required} are required."
        )
    else:
        explanation = (
            f"{summary.satisfied_count} of {summary.applicable_count} "
            "applicable requirements are satisfied; "
            f"{summary.not_applicable_count} are not applicable."
        )
    return "\n".join(
        (
            "### Deterministic policy aggregation",
            f"**Mode:** `{summary.mode}`",
            explanation,
        )
    )


def _decision_markdown(decision) -> str:
    if decision is None:
        return ""
    return "\n".join(
        (
            "### Human decision recorded",
            f"**Decision ID:** `{decision.analyst_decision_id}`",
            f"**Outcome:** `{decision.selected_outcome.value}`",
            f"**Analyst:** {decision.decided_by}",
            f"**Decided at:** {decision.decided_at.isoformat()}",
            f"**Workflow:** `{decision.next_workflow_status.value}`",
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dispute_copilot.human_review.app",
        description="Launch the local human-in-the-loop review interface.",
    )
    parser.add_argument("--review-package-id", required=True)
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("."),
    )
    parser.add_argument("--server-name", default="127.0.0.1")
    parser.add_argument("--server-port", type=int, default=7860)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        package_id = UUID(args.review_package_id)
        app = create_app(
            review_package_id=package_id,
            database_path=args.database,
            project_root=args.project_root,
        )
    except (ValueError, HumanReviewError) as error:
        print(f"Human review UI failed: {error}")
        return 1
    app.launch(
        server_name=args.server_name,
        server_port=args.server_port,
        share=False,
        show_error=False,
        blocked_paths=[
            str((args.project_root / "data").resolve())
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
