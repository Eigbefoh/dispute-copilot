"""Pure deterministic analyst-review package assembly."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from uuid import UUID, uuid4

from dispute_copilot.analyst_review.errors import (
    AnalystReviewInputError,
    PresentationRulesError,
)
from dispute_copilot.analyst_review.guards import AnalystReviewInputs
from dispute_copilot.analyst_review.models import (
    AnalystReviewPackage,
    CaseSummary,
    DeterministicRationale,
    DisplayFactValue,
    EvidenceGapItem,
    FactDisplayReference,
    RecommendationDisplay,
    RequirementChecklistItem,
    ReviewLineage,
    SupportingSourceItem,
    SupportType,
    calculate_review_package_content_hash,
)
from dispute_copilot.analyst_review.presentation_models import (
    PLACEHOLDER_PATTERN,
    PresentationRulesManifest,
)
from dispute_copilot.evaluation.models import RequirementStatus
from dispute_copilot.extraction.fact_resolution_models import (
    CaseMetadataProvenance,
    EvidenceTextProvenance,
    FactProvenance,
    ResolvedFact,
    VisualObservationProvenance,
)

REVIEW_PACKAGE_SCHEMA_VERSION = "1.0.0"


def assemble_analyst_review_package(
    *,
    inputs: AnalystReviewInputs,
    presentation_rules: PresentationRulesManifest,
    review_package_id: UUID | None = None,
    created_at: datetime | None = None,
) -> AnalystReviewPackage:
    """Assemble already-authoritative results without making a decision."""
    recommendation = inputs.recommendation
    evaluation = inputs.evaluation
    resolution = inputs.resolution
    selection = inputs.selection
    case_run = inputs.case_run

    facts_by_name = {
        fact.fact_name: (index, fact)
        for index, fact in enumerate(resolution.facts)
    }
    supporting_sources: list[SupportingSourceItem] = []
    seen_source_ids: set[str] = set()
    checklist: list[RequirementChecklistItem] = []
    evidence_gaps: list[EvidenceGapItem] = []

    policy_requirements = selection.policy_snapshot.requirements
    for policy_requirement, evaluated in zip(
        policy_requirements,
        evaluation.ordered_requirement_results,
        strict=True,
    ):
        supporting_fact_refs: list[FactDisplayReference] = []
        unresolved_fact_refs: list[FactDisplayReference] = []
        requirement_source_refs: list[str] = []
        for binding in evaluated.fact_bindings:
            fact_index, fact = facts_by_name[binding.fact_name]
            display_reference = _fact_display_reference(
                fact=fact,
                fact_index=fact_index,
                value_indexes=binding.value_indexes,
            )
            if fact.status == "FOUND":
                supporting_fact_refs.append(display_reference)
                for value_index in binding.value_indexes:
                    value = fact.resolved_values[value_index]
                    for provenance_index, provenance in enumerate(
                        value.provenance
                    ):
                        source = _supporting_source(
                            inputs=inputs,
                            fact=fact,
                            fact_value_index=value_index,
                            provenance_index=provenance_index,
                            provenance=provenance,
                        )
                        requirement_source_refs.append(source.source_ref_id)
                        if source.source_ref_id not in seen_source_ids:
                            seen_source_ids.add(source.source_ref_id)
                            supporting_sources.append(source)
            else:
                unresolved_fact_refs.append(display_reference)

        if evaluated.status is RequirementStatus.NOT_ESTABLISHED:
            for fact_name in evaluated.missing_or_unresolved_facts:
                _, fact = facts_by_name[fact_name]
                if fact.status == "FOUND":
                    raise AnalystReviewInputError(
                        "A NOT_ESTABLISHED evidence gap cannot reference "
                        f"FOUND fact '{fact_name}'"
                    )
                label = presentation_rules.evidence_gap_display_labels.get(
                    fact_name
                )
                if label is None:
                    raise PresentationRulesError(
                        "No evidence-gap display label is configured for "
                        f"'{fact_name}'"
                    )
                evidence_gaps.append(
                    EvidenceGapItem(
                        gap_id=_stable_id(
                            "gap",
                            str(evaluation.evaluation_id),
                            evaluated.requirement_id,
                            fact_name,
                        ),
                        requirement_id=evaluated.requirement_id,
                        fact_name=fact_name,
                        display_label=label,
                        resolution_status=fact.status,
                        blocking_effect=(
                            f"{evaluated.requirement_id} remains "
                            "NOT_ESTABLISHED"
                        ),
                    )
                )

        checklist.append(
            RequirementChecklistItem(
                requirement_id=evaluated.requirement_id,
                requirement_order=evaluated.requirement_order,
                requirement_text=policy_requirement.description,
                status=evaluated.status,
                evaluation_basis_codes=evaluated.basis_codes,
                supporting_fact_refs=tuple(supporting_fact_refs),
                unresolved_fact_refs=tuple(unresolved_fact_refs),
                source_refs=tuple(dict.fromkeys(requirement_source_refs)),
            )
        )

    rationale = _render_rationale(
        inputs=inputs,
        presentation_rules=presentation_rules,
    )
    fields = {
        "review_package_id": review_package_id or uuid4(),
        "schema_version": REVIEW_PACKAGE_SCHEMA_VERSION,
        "presentation_rules_key": (
            presentation_rules.presentation_rules_key
        ),
        "presentation_rules_version": (
            presentation_rules.presentation_rules_version
        ),
        "case_summary": CaseSummary(
            case_id=case_run.case_snapshot.case_id,
            card_scheme=selection.scheme,
            reason_code=selection.reason_code,
            policy_key=selection.policy_id,
            policy_version=selection.rulebook_version,
            current_workflow_status="READY_FOR_ANALYST_REVIEW",
        ),
        "recommendation": RecommendationDisplay(
            recommendation_id=recommendation.recommendation_id,
            decision=recommendation.recommendation,
            display_label=presentation_rules.recommendation_display_labels[
                recommendation.recommendation
            ],
            basis_codes=recommendation.basis_codes,
            causal_requirement_ids=tuple(
                item.requirement_id
                for item in recommendation.causal_requirement_results
            ),
            advisory_only=True,
        ),
        "rationale": rationale,
        "requirement_checklist": tuple(checklist),
        "evidence_gaps": tuple(evidence_gaps),
        "supporting_sources": tuple(supporting_sources),
        "limitations": presentation_rules.limitations,
        "lineage": ReviewLineage(
            run_id=case_run.run_id,
            selection_id=selection.selection_id,
            fact_resolution_id=resolution.resolution_id,
            evaluation_id=evaluation.evaluation_id,
            recommendation_id=recommendation.recommendation_id,
            case_run_content_hash=inputs.case_run_content_hash,
            policy_selection_content_hash=(
                inputs.policy_selection_content_hash
            ),
            policy_integrity_hash=selection.policy_sha256,
            fact_resolution_content_hash=resolution.content_hash,
            evaluation_content_hash=evaluation.content_hash,
            recommendation_content_hash=recommendation.content_hash,
            presentation_rules_integrity_hash=(
                presentation_rules.integrity_hash
            ),
        ),
        "created_at": created_at or datetime.now(timezone.utc),
    }
    draft = AnalystReviewPackage.model_construct(
        **fields,
        content_hash="0" * 64,
    )
    return AnalystReviewPackage(
        **fields,
        content_hash=calculate_review_package_content_hash(draft),
    )


def _fact_display_reference(
    *,
    fact: ResolvedFact,
    fact_index: int,
    value_indexes: tuple[int, ...],
) -> FactDisplayReference:
    return FactDisplayReference(
        fact_name=fact.fact_name,
        resolution_fact_index=fact_index,
        resolution_status=fact.status,
        value_indexes=value_indexes,
        values=tuple(
            _display_value(fact.resolved_values[index].value)
            for index in value_indexes
        ),
    )


def _display_value(value: object) -> DisplayFactValue:
    if isinstance(value, datetime):
        return DisplayFactValue(
            value_type="DATETIME",
            value=value.isoformat(),
        )
    if isinstance(value, date):
        return DisplayFactValue(
            value_type="DATE",
            value=value.isoformat(),
        )
    if isinstance(value, bool):
        return DisplayFactValue(
            value_type="BOOLEAN",
            value="true" if value else "false",
        )
    if isinstance(value, int):
        return DisplayFactValue(
            value_type="INTEGER",
            value=str(value),
        )
    if isinstance(value, Decimal):
        return DisplayFactValue(
            value_type="DECIMAL",
            value=format(value, "f"),
        )
    if isinstance(value, str):
        return DisplayFactValue(value_type="STRING", value=value)
    raise AnalystReviewInputError(
        f"Unsupported admitted fact value type: {type(value).__name__}"
    )


def _supporting_source(
    *,
    inputs: AnalystReviewInputs,
    fact: ResolvedFact,
    fact_value_index: int,
    provenance_index: int,
    provenance: FactProvenance,
) -> SupportingSourceItem:
    source_ref_id = _stable_id(
        "source",
        str(inputs.resolution.resolution_id),
        fact.fact_name,
        str(fact_value_index),
        str(provenance_index),
        provenance.model_dump_json(),
    )
    common = {
        "source_ref_id": source_ref_id,
        "fact_name": fact.fact_name,
        "fact_value_index": fact_value_index,
        "extraction_route": fact.resolver_type,
    }
    if isinstance(provenance, CaseMetadataProvenance):
        return SupportingSourceItem(
            **common,
            source_document=None,
            page_number=None,
            extraction_method="DETERMINISTIC_METADATA",
            support_type=SupportType.STRUCTURED_CASE_DATA,
            supporting_content=provenance.field_path,
        )
    if isinstance(provenance, EvidenceTextProvenance):
        return SupportingSourceItem(
            **common,
            source_document=provenance.file_name,
            page_number=provenance.page_number,
            extraction_method=provenance.extraction_method,
            support_type=SupportType.SOURCE_TEXT_ASSERTION,
            supporting_content=provenance.supporting_text,
        )
    if isinstance(provenance, VisualObservationProvenance):
        return SupportingSourceItem(
            **common,
            source_document=provenance.file_name,
            page_number=provenance.page_number,
            extraction_method="VISION",
            support_type=SupportType.VISUAL_MODEL_OBSERVATION,
            supporting_content=provenance.observation,
        )
    raise AnalystReviewInputError("Unsupported admitted provenance type")


def _render_rationale(
    *,
    inputs: AnalystReviewInputs,
    presentation_rules: PresentationRulesManifest,
) -> DeterministicRationale:
    recommendation = inputs.recommendation.recommendation
    matches = tuple(
        template
        for template in presentation_rules.rationale_templates
        if template.recommendation is recommendation
    )
    if len(matches) != 1:
        raise PresentationRulesError(
            "Expected exactly one rationale template for "
            f"{recommendation.value}"
        )
    template = matches[0]
    evaluation = inputs.evaluation
    case_snapshot = inputs.case_run.case_snapshot
    values = {
        "policy_display_name": (
            f"{inputs.selection.scheme.title()} reason code "
            f"{inputs.selection.reason_code} "
            f"({case_snapshot.reason_code_label})"
        ),
        "satisfied_requirement_summary": _format_requirement_list(
            tuple(
                item.requirement_id
                for item in evaluation.ordered_requirement_results
                if item.status is RequirementStatus.SATISFIED
            ),
            empty="no policy requirements",
        ),
        "causal_requirement_list": _format_requirement_list(
            tuple(
                item.requirement_id
                for item in inputs.recommendation.causal_requirement_results
            ),
            empty=None,
        ),
    }
    rendered: list[str] = []
    for sentence in template.sentences:
        placeholders = PLACEHOLDER_PATTERN.findall(sentence)
        for placeholder in placeholders:
            value = values.get(placeholder)
            if value is None or not value.strip():
                raise PresentationRulesError(
                    "Rationale placeholder could not be resolved: "
                    f"{placeholder}"
                )
        rendered_sentence = sentence.format_map(values)
        if PLACEHOLDER_PATTERN.search(rendered_sentence):
            raise PresentationRulesError(
                "Rationale contains an unresolved placeholder"
            )
        rendered.append(rendered_sentence)
    return DeterministicRationale(
        template_key=template.template_key,
        template_version=template.template_version,
        sentences=tuple(rendered),
    )


def _format_requirement_list(
    requirement_ids: tuple[str, ...],
    *,
    empty: str | None,
) -> str:
    if not requirement_ids:
        if empty is None:
            raise PresentationRulesError(
                "Required rationale requirement list is empty"
            )
        return empty
    prefix = "requirement" if len(requirement_ids) == 1 else "requirements"
    if len(requirement_ids) == 1:
        joined = requirement_ids[0]
    elif len(requirement_ids) == 2:
        joined = f"{requirement_ids[0]} and {requirement_ids[1]}"
    else:
        joined = (
            ", ".join(requirement_ids[:-1])
            + f", and {requirement_ids[-1]}"
        )
    return f"{prefix} {joined}"


def _stable_id(prefix: str, *parts: str) -> str:
    canonical = json.dumps(
        parts,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return f"{prefix}-{sha256(canonical.encode('utf-8')).hexdigest()}"
