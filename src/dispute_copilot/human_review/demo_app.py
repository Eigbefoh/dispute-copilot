"""Multi-case shell around the existing analyst-review presentation."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import gradio as gr
from pydantic import TypeAdapter, ValidationError

from dispute_copilot.application.models import (
    AnalystCaseView,
    ExecutionMode,
    ExecutionStatus,
)
from dispute_copilot.application.service import DisputeCopilotApplication
from dispute_copilot.human_review.app import (
    ACTION_LABELS,
    _COMMAND_ADAPTER,
    _action_visibility,
    _aggregation_markdown,
    _case_summary_markdown,
    _close_callback,
    _decision_markdown,
    _invalidate_preparation,
    _panel_outputs,
    _preview_markdown,
    _recommendation_markdown,
    _submit_error_outputs,
)
from dispute_copilot.human_review.controller import (
    DisplaySelection,
    HumanReviewController,
)
from dispute_copilot.human_review.decision_models import (
    AnalystDecisionCommand,
    AnalystOutcome,
    TrustedAnalystIdentity,
)
from dispute_copilot.human_review.errors import HumanReviewError

_DECISION_ADAPTER = TypeAdapter(AnalystDecisionCommand)


def create_demo_app(
    *,
    application: DisputeCopilotApplication,
    analyst_identity: TrustedAnalystIdentity,
) -> gr.Blocks:
    """Build a thin case queue over persisted application DTOs."""
    queue = application.list_cases()
    case_choices = [
        (
            f"{item.case_id} · {item.card_scheme.title()} "
            f"{item.reason_code}",
            item.case_id,
        )
        for item in queue
    ]
    initial_case_id = queue[0].case_id
    rules = _controller_factory(
        application,
        analyst_identity,
        None,
    ).decision_rules()
    reason_choices = [
        (item.display_label, item.reason_code)
        for item in rules.override_reasons
    ]

    def run_selected_callback(case_id: str, token: float):
        yield from _run_selected(application, case_id, token)

    def run_all_callback(token: float):
        yield from _run_all(application, token)

    with gr.Blocks(
        title="Dispute Copilot — analyst demo",
        analytics_enabled=False,
    ) as app:
        selected_display = gr.State(value=None)
        evidence_selections = gr.State(value={})
        review_package_id = gr.State(value=None)
        prepared_command = gr.State(value=None)
        refresh_token = gr.Number(value=0, visible=False)

        gr.Markdown("# Dispute Copilot — analyst review")
        mode_label = (
            "LIVE EXECUTION"
            if application.execution_mode is ExecutionMode.LIVE
            else "CACHED RESULT VIEW"
        )
        gr.Markdown(
            f"**Mode:** `{mode_label}` · **Rulebook:** "
            f"`{application.rulebook_version}`\n\n"
            "_All recommendations are advisory. No external dispute action "
            "is taken by this interface._"
            + (
                "\n\n> `take-home-v3` is the preserved working pipeline and "
                "has documented critical policy-fidelity limitations. This "
                "demo is not proof of corrected all-case policy fidelity."
                if application.rulebook_version == "take-home-v3"
                else ""
            )
        )
        with gr.Row():
            with gr.Column(scale=2):
                case_selector = gr.Dropdown(
                    choices=case_choices,
                    value=initial_case_id,
                    label="Case queue",
                    interactive=True,
                )
            with gr.Column(scale=1):
                run_selected = gr.Button(
                    "Run Selected Case",
                    variant="primary",
                    interactive=(
                        application.execution_mode is ExecutionMode.LIVE
                    ),
                )
            with gr.Column(scale=1):
                run_all = gr.Button(
                    "Run All Cases",
                    variant="primary",
                    interactive=(
                        application.execution_mode is ExecutionMode.LIVE
                    ),
                )
        queue_display = gr.Markdown(_queue_markdown(queue))
        activity = gr.Markdown(
            "Select a case to inspect its current persisted state."
        )

        with gr.Row(equal_height=False):
            with gr.Column(scale=1, min_width=440):
                case_summary = gr.Markdown()
                recommendation = gr.Markdown()
                aggregation = gr.Markdown()
                rationale = gr.Markdown()
                requirements = gr.Markdown()
                gaps = gr.Markdown()
                facts = gr.Markdown()

                gr.Markdown("### Final analyst action")
                action = gr.Radio(
                    choices=list(ACTION_LABELS),
                    value=None,
                    label="Action",
                    info="No action is preselected.",
                    interactive=False,
                )
                override_outcome = gr.Dropdown(
                    choices=[],
                    value=None,
                    label="Override outcome",
                    visible=False,
                    interactive=False,
                )
                override_reason = gr.Dropdown(
                    choices=reason_choices,
                    value=None,
                    label="Override reason",
                    visible=False,
                    interactive=False,
                )
                selected_gaps = gr.CheckboxGroup(
                    choices=[],
                    value=[],
                    label="Unresolved evidence gaps",
                    visible=False,
                    interactive=False,
                )
                analyst_comment = gr.Textbox(
                    label="Analyst comment",
                    lines=4,
                    max_lines=8,
                    interactive=False,
                )
                with gr.Row():
                    review_action = gr.Button(
                        "Review action",
                        variant="primary",
                        interactive=False,
                    )
                    close_action = gr.Button(
                        "Close without deciding",
                        interactive=False,
                    )
                confirmation = gr.Markdown(
                    "A completed case is required before analyst action."
                )
                submit_action = gr.Button(
                    "Submit final decision",
                    variant="stop",
                    interactive=False,
                )
                with gr.Accordion(
                    "Technical lineage",
                    open=False,
                ):
                    technical_lineage = gr.Markdown()

            with gr.Column(scale=1, min_width=520):
                gr.Markdown("## Source evidence")
                evidence_selector = gr.Dropdown(
                    choices=[],
                    value=None,
                    label="Requirement source or evidence gap",
                    interactive=False,
                )
                evidence_image = gr.Image(
                    type="pil",
                    interactive=False,
                    label="Verified source page",
                    height=620,
                )
                evidence_metadata = gr.Markdown(
                    "Select a completed case and a source to inspect it."
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

        view_outputs = [
            case_summary,
            recommendation,
            aggregation,
            rationale,
            requirements,
            gaps,
            facts,
            technical_lineage,
            review_package_id,
            evidence_selector,
            evidence_selections,
            evidence_image,
            evidence_metadata,
            selected_display,
            previous_page,
            next_page,
            action,
            override_outcome,
            override_reason,
            selected_gaps,
            analyst_comment,
            review_action,
            close_action,
            confirmation,
            submit_action,
            prepared_command,
        ]

        # User-facing inputs use ``input`` so callback-driven component
        # updates do not recursively fire dependent handlers while a case
        # view is being replaced. The hidden refresh token intentionally
        # retains ``change`` because it is the programmatic refresh signal.
        case_selector.input(
            fn=lambda case_id: _load_case_outputs(
                application,
                analyst_identity,
                case_id,
            ),
            inputs=[case_selector],
            outputs=view_outputs,
        )
        refresh_token.change(
            fn=lambda _, case_id: _load_case_outputs(
                application,
                analyst_identity,
                case_id,
            ),
            inputs=[refresh_token, case_selector],
            outputs=view_outputs,
        )
        app.load(
            fn=lambda case_id: _load_case_outputs(
                application,
                analyst_identity,
                case_id,
            ),
            inputs=[case_selector],
            outputs=view_outputs,
        )

        run_selected.click(
            fn=run_selected_callback,
            inputs=[case_selector, refresh_token],
            outputs=[queue_display, activity, refresh_token],
        )
        run_all.click(
            fn=run_all_callback,
            inputs=[refresh_token],
            outputs=[queue_display, activity, refresh_token],
        )
        evidence_selector.input(
            fn=lambda package_id, selection_key, selections: (
                _inspect_evidence(
                    application,
                    analyst_identity,
                    package_id,
                    selection_key,
                    selections,
                )
            ),
            inputs=[
                review_package_id,
                evidence_selector,
                evidence_selections,
            ],
            outputs=[
                evidence_image,
                evidence_metadata,
                selected_display,
                previous_page,
                next_page,
            ],
        )
        previous_page.click(
            fn=lambda package_id, state: _change_page(
                application,
                analyst_identity,
                package_id,
                state,
                -1,
            ),
            inputs=[review_package_id, selected_display],
            outputs=[
                evidence_image,
                evidence_metadata,
                selected_display,
                previous_page,
                next_page,
            ],
        )
        next_page.click(
            fn=lambda package_id, state: _change_page(
                application,
                analyst_identity,
                package_id,
                state,
                1,
            ),
            inputs=[review_package_id, selected_display],
            outputs=[
                evidence_image,
                evidence_metadata,
                selected_display,
                previous_page,
                next_page,
            ],
        )
        action.input(
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
            form_input.input(
                fn=_invalidate_preparation,
                outputs=[
                    confirmation,
                    submit_action,
                    prepared_command,
                ],
            )
        review_action.click(
            fn=lambda package_id, *values: _prepare_action(
                application,
                analyst_identity,
                package_id,
                *values,
            ),
            inputs=[
                review_package_id,
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
            fn=lambda package_id, state: _submit_action(
                application,
                analyst_identity,
                package_id,
                state,
            ),
            inputs=[review_package_id, prepared_command],
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


def _load_case_outputs(
    application: DisputeCopilotApplication,
    identity: TrustedAnalystIdentity,
    case_id: str,
):
    view = application.get_case_view(case_id)
    if view.status is not ExecutionStatus.COMPLETED:
        return _empty_view_outputs(view)
    assert view.package is not None
    package = view.package
    decision_controller = _controller_factory(
        application,
        identity,
        package.review_package_id,
    )
    decision = decision_controller.existing_decision()
    rules = decision_controller.decision_rules()
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
    gap_choices = [
        (f"{gap.requirement_id}: {gap.display_label}", gap.gap_id)
        for gap in package.evidence_gaps
    ]
    evidence_choices, selections = _evidence_choices(package)
    locked = decision is not None
    result_label = (
        "### PERSISTED RESULT\n\n"
        "_Originally produced by a live execution; reopening this view "
        "does not call the model._"
        if view.execution_mode is ExecutionMode.LIVE
        else "### PERSISTED/CACHED RESULT\n\n"
        "_Loaded from SQLite without a model call._"
    )
    return (
        result_label + "\n\n" + _case_summary_markdown(package, view.case),
        _recommendation_markdown(package),
        _aggregation_markdown(package),
        "### Deterministic rationale\n"
        + "\n".join(f"- {item}" for item in package.rationale.sentences),
        _requirements_markdown(view),
        _gaps_markdown(view),
        _facts_markdown(view),
        _lineage_markdown(view),
        str(package.review_package_id),
        gr.Dropdown(
            choices=evidence_choices,
            value=None,
            interactive=bool(evidence_choices),
        ),
        selections,
        None,
        "Select a source or evidence gap to inspect it.",
        None,
        gr.Button(interactive=False),
        gr.Button(interactive=False),
        gr.Radio(value=None, interactive=not locked),
        gr.Dropdown(
            choices=override_outcomes,
            value=None,
            visible=False,
            interactive=not locked,
        ),
        gr.Dropdown(
            choices=reason_choices,
            value=None,
            visible=False,
            interactive=not locked,
        ),
        gr.CheckboxGroup(
            choices=gap_choices,
            value=[],
            visible=False,
            interactive=not locked,
        ),
        gr.Textbox(value="", interactive=not locked),
        gr.Button(interactive=not locked),
        gr.Button(interactive=not locked),
        (
            _decision_markdown(decision)
            if decision is not None
            else "Select an action, then review it before submission."
        ),
        gr.Button(interactive=False),
        None,
    )


def _empty_view_outputs(view: AnalystCaseView):
    status = view.status.value
    case = view.case
    summary = "\n".join(
        (
            "## Case",
            f"- **Case ID:** `{case.case_id}`",
            f"- **Merchant:** {case.transaction.merchant_name}",
            f"- **Scheme / reason:** {case.scheme} / {case.reason_code}",
            f"- **Execution status:** `{status}`",
            "",
            view.status_message,
        )
    )
    return (
        summary,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        None,
        gr.Dropdown(choices=[], value=None, interactive=False),
        {},
        None,
        "No completed result is available for this case.",
        None,
        gr.Button(interactive=False),
        gr.Button(interactive=False),
        gr.Radio(value=None, interactive=False),
        gr.Dropdown(
            choices=[],
            value=None,
            visible=False,
            interactive=False,
        ),
        gr.Dropdown(
            value=None,
            visible=False,
            interactive=False,
        ),
        gr.CheckboxGroup(
            choices=[],
            value=[],
            visible=False,
            interactive=False,
        ),
        gr.Textbox(value="", interactive=False),
        gr.Button(interactive=False),
        gr.Button(interactive=False),
        "A completed case is required before analyst action.",
        gr.Button(interactive=False),
        None,
    )


def _run_selected(application, case_id: str, refresh_token: float):
    token = int(refresh_token or 0)
    for event in application.run_case_progress(case_id):
        token += 1
        yield (
            _queue_markdown(application.list_cases()),
            _event_markdown(event),
            token,
        )


def _run_all(application, refresh_token: float):
    token = int(refresh_token or 0)
    for event in application.run_all_cases():
        token += 1
        yield (
            _queue_markdown(application.list_cases()),
            _event_markdown(event),
            token,
        )


def _queue_markdown(queue) -> str:
    lines = [
        "### Case execution queue",
        "| Case | Scheme / code | Evidence | Status |",
        "|---|---|---:|---|",
    ]
    for item in queue:
        lines.append(
            f"| `{item.case_id}` | {item.card_scheme} / "
            f"{item.reason_code} | {item.declared_evidence_count} | "
            f"`{item.status.value}` |"
        )
    return "\n".join(lines)


def _event_markdown(event) -> str:
    message = (
        f"### Execution update\n\n`{event.case_id}` → "
        f"`{event.status.value}`"
    )
    if event.error_message:
        message += f"\n\n{event.error_message}"
    return message


def _requirements_markdown(view: AnalystCaseView) -> str:
    assert view.package is not None
    lines = [
        "### Policy requirements",
        "| Requirement | Grade | Basis |",
        "|---|---|---|",
    ]
    for item in view.package.requirement_checklist:
        lines.append(
            f"| `{item.requirement_id}` {item.requirement_text} | "
            f"`{item.status.value}` | "
            f"{', '.join(item.evaluation_basis_codes)} |"
        )
    return "\n".join(lines)


def _gaps_markdown(view: AnalystCaseView) -> str:
    assert view.package is not None
    lines = ["### Evidence gaps"]
    if not view.package.evidence_gaps:
        lines.append("No unresolved evidence gaps.")
    else:
        lines.extend(
            f"- `{gap.requirement_id}` — {gap.display_label} "
            f"(`{gap.resolution_status}`)"
            for gap in view.package.evidence_gaps
        )
    insufficient = tuple(
        (
            requirement.requirement_id,
            reference.fact_name,
        )
        for requirement in view.package.requirement_checklist
        if requirement.status.value != "SATISFIED"
        for reference in requirement.supporting_fact_refs
    )
    lines.extend(("", "### Submitted evidence that is not sufficient"))
    if insufficient:
        lines.extend(
            f"- `{fact_name}` is relevant to `{requirement_id}`, but the "
            "requirement is not established or is not satisfied."
            for requirement_id, fact_name in insufficient
        )
    else:
        lines.append("No such submitted evidence is recorded.")
    if any(
        "LIMITED_VIEW_CANNOT_ESTABLISH_MATERIAL_ITEM_CONDITION"
        in requirement.evaluation_basis_codes
        for requirement in view.package.requirement_checklist
    ):
        lines.extend(
            (
                "",
                "### Deliberately not inferred",
                "- A limited delivery image is not treated as proof that "
                "there is no hidden damage, wobble, structural defect, or "
                "other non-visible condition.",
            )
        )
    lines.extend(("", "### Suggested next action"))
    action = view.suggested_action
    if action is None:
        lines.append(
            "No persisted suggested action is available for this package."
        )
    else:
        lines.append(f"**`{action.action.value}`**")
        lines.extend(f"- {item}" for item in action.requested_items)
        lines.append(
            "_This suggestion is separate from, and does not alter, the "
            "advisory recommendation._"
        )
    return "\n".join(lines)


def _facts_markdown(view: AnalystCaseView) -> str:
    assert view.resolution is not None
    lines = ["### Extracted and resolved facts"]
    source_by_fact = {}
    assert view.package is not None
    for source in view.package.supporting_sources:
        source_by_fact.setdefault(source.fact_name, []).append(source)
    for fact in view.resolution.facts:
        lines.append(f"- **`{fact.fact_name}`:** `{fact.status}`")
        for value in fact.resolved_values:
            lines.append(f"  - Value: `{value.value}`")
        for source in source_by_fact.get(fact.fact_name, ()):
            location = (
                f"{source.source_document}, page {source.page_number}"
                if source.source_document
                else "immutable case metadata"
            )
            lines.append(
                f"  - Citation: {location} "
                f"(`{source.support_type.value}`)"
            )
    return "\n".join(lines)


def _lineage_markdown(view: AnalystCaseView) -> str:
    lineage = view.technical_lineage
    assert lineage is not None
    return "\n".join(
        (
            f"- **Run:** `{lineage.run_id}`",
            f"- **Selection:** `{lineage.selection_id}`",
            f"- **Resolution:** `{lineage.resolution_id}`",
            f"- **Evaluation:** `{lineage.evaluation_id}`",
            f"- **Recommendation:** `{lineage.recommendation_id}`",
            f"- **Review package:** `{lineage.review_package_id}`",
            f"- **Policy:** `{lineage.policy_id}`",
            f"- **Rulebook:** `{lineage.rulebook_version}`",
            f"- **Provider / model:** `{lineage.provider}` / "
            f"`{lineage.model}`",
            f"- **Prompt / schema:** `{lineage.prompt_version}` / "
            f"`{lineage.schema_version}`",
            f"- **Parameters:** `{lineage.parameters_json}`",
            f"- **Latency:** {lineage.request_latency}",
            f"- **Tokens:** {lineage.token_usage}",
            f"- **Estimated cost:** {lineage.estimated_cost}",
        )
    )


def _evidence_choices(package):
    choices = []
    selections = {}
    for source in package.supporting_sources:
        if source.source_document is None:
            continue
        key = f"source:{source.source_ref_id}"
        choices.append(
            (
                f"{source.fact_name} — {source.source_document}, "
                f"page {source.page_number}",
                key,
            )
        )
        selections[key] = {
            "kind": "source",
            "item_id": source.source_ref_id,
            "requirement_id": None,
            "page_number": source.page_number,
        }
    for gap in package.evidence_gaps:
        key = f"gap:{gap.gap_id}"
        choices.append(
            (
                f"Gap: {gap.requirement_id} — {gap.display_label}",
                key,
            )
        )
        selections[key] = {
            "kind": "gap",
            "item_id": gap.gap_id,
            "requirement_id": gap.requirement_id,
            "page_number": None,
        }
    return choices, selections


def _inspect_evidence(
    application,
    identity,
    package_id,
    selection_key,
    selections,
):
    if not package_id or not selection_key:
        return (
            None,
            "Select a source or evidence gap.",
            None,
            gr.Button(interactive=False),
            gr.Button(interactive=False),
        )
    try:
        selection = DisplaySelection(**selections[selection_key])
        controller = _controller_factory(
            application,
            identity,
            UUID(package_id),
        )
        return _panel_outputs(controller.inspect(selection))
    except (HumanReviewError, KeyError, TypeError, ValueError) as error:
        return (
            None,
            f"### Evidence unavailable\n\n{error}",
            None,
            gr.Button(interactive=False),
            gr.Button(interactive=False),
        )


def _change_page(
    application,
    identity,
    package_id,
    state,
    delta,
):
    if not package_id or not state:
        return (
            None,
            "Select a source before changing pages.",
            None,
            gr.Button(interactive=False),
            gr.Button(interactive=False),
        )
    try:
        controller = _controller_factory(
            application,
            identity,
            UUID(package_id),
        )
        return _panel_outputs(
            controller.change_page(DisplaySelection(**state), delta)
        )
    except (HumanReviewError, TypeError, ValueError) as error:
        return (
            None,
            f"### Page unavailable\n\n{error}",
            state,
            gr.Button(interactive=False),
            gr.Button(interactive=False),
        )


def _prepare_action(
    application,
    identity,
    package_id,
    action_label,
    selected_outcome,
    override_reason,
    analyst_comment,
    selected_gaps,
):
    if not package_id:
        return (
            "A completed case is required.",
            gr.Button(interactive=False),
            None,
        )
    action = ACTION_LABELS.get(action_label)
    if action == "DEFER_OR_CLOSE":
        return _close_callback()[:3]
    try:
        controller = _controller_factory(
            application,
            identity,
            UUID(package_id),
        )
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
    return (
        _preview_markdown(preview),
        gr.Button(interactive=True),
        command.model_dump(mode="json"),
    )


def _submit_action(
    application,
    identity,
    package_id,
    state,
):
    if not package_id or state is None:
        return _submit_error_outputs(
            "Review the action before final submission."
        )
    try:
        command = _DECISION_ADAPTER.validate_json(json.dumps(state))
        controller = _controller_factory(
            application,
            identity,
            UUID(package_id),
        )
        decision = controller.submit(command)
    except (HumanReviewError, ValidationError, ValueError) as error:
        return _submit_error_outputs(str(error))
    locked = gr.Button(interactive=False)
    return (
        _decision_markdown(decision),
        locked,
        None,
        gr.Radio(value=None, interactive=False),
        locked,
        locked,
        gr.Dropdown(interactive=False),
        gr.Dropdown(interactive=False),
        gr.CheckboxGroup(interactive=False),
        gr.Textbox(interactive=False),
    )


def _controller_factory(
    application: DisputeCopilotApplication,
    identity: TrustedAnalystIdentity,
    package_id: UUID | None,
) -> HumanReviewController:
    # A placeholder ID is used only to load global decision rules while
    # building the interface; no package lookup occurs in that path.
    return HumanReviewController(
        review_package_id=package_id or UUID(int=0),
        analyst_identity=identity,
        database_path=application.database_path,
        project_root=application.project_root,
    )
