"""Deterministically admit candidate facts to future policy evaluation."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from dispute_copilot.domain.case_run import CaseRun
from dispute_copilot.domain.policy_selection import (
    PolicySelection,
    PolicySelectionStatus,
)
from dispute_copilot.extraction.citation_text import (
    supporting_text_occurs_on_page,
)
from dispute_copilot.extraction.fact_resolution_models import (
    CaseMetadataProvenance,
    EvidenceTextProvenance,
    FactResolutionResult,
    ResolvedFact,
    VisualObservationProvenance,
    calculate_fact_resolution_content_hash,
)
from dispute_copilot.extraction.metadata_resolver import (
    get_metadata_binding,
)
from dispute_copilot.extraction.models import (
    EvidenceBundle,
    EvidenceDocument,
    EvidencePage,
    ExtractionMethod,
)
from dispute_copilot.policy.fact_models import (
    FactCatalogue,
    FactDefinition,
)

SUPPORTED_RULEBOOK_VERSION = "take-home-v2"


class FactResolutionValidationError(ValueError):
    """Base error for rejected candidate resolution data."""


class ResolutionContextError(FactResolutionValidationError):
    """Raised when trusted snapshots do not describe the same workflow."""


class FactCompletenessError(FactResolutionValidationError):
    """Raised when candidates do not exactly cover selected-policy facts."""


class FactValueValidationError(FactResolutionValidationError):
    """Raised when a candidate value violates catalogue semantics."""


class FactRoutingValidationError(FactResolutionValidationError):
    """Raised when a fact crosses its registered resolution boundary."""


class FactProvenanceValidationError(FactResolutionValidationError):
    """Raised when provenance does not resolve to an admitted source."""


def selected_policy_fact_names(
    selection: PolicySelection,
) -> tuple[str, ...]:
    """Derive stable unique fact order only from the frozen snapshot."""
    ordered: list[str] = []
    seen: set[str] = set()
    for requirement in selection.policy_snapshot.requirements:
        for fact_name in requirement.required_facts:
            if fact_name not in seen:
                seen.add(fact_name)
                ordered.append(fact_name)
    return tuple(ordered)


def create_fact_resolution_result(
    *,
    selection: PolicySelection,
    case_run: CaseRun,
    catalogue: FactCatalogue,
    evidence_bundle: EvidenceBundle,
    evidence_documents: tuple[EvidenceDocument, ...],
    facts: tuple[ResolvedFact, ...],
    resolver_version: str,
    resolution_id: UUID | None = None,
    created_at: datetime | None = None,
) -> FactResolutionResult:
    """Validate all trusted context before constructing readiness."""
    _validate_context(
        selection=selection,
        case_run=case_run,
        catalogue=catalogue,
        evidence_bundle=evidence_bundle,
    )
    documents_by_id = _validate_document_membership(
        evidence_bundle=evidence_bundle,
        evidence_documents=evidence_documents,
    )

    requested_facts = selected_policy_fact_names(selection)
    candidate_names = tuple(fact.fact_name for fact in facts)
    if len(candidate_names) != len(set(candidate_names)):
        raise FactCompletenessError(
            "candidate fact names must be unique"
        )
    if candidate_names != requested_facts:
        missing = set(requested_facts) - set(candidate_names)
        unrequested = set(candidate_names) - set(requested_facts)
        raise FactCompletenessError(
            "candidate facts must exactly match the frozen policy "
            f"snapshot in order; missing={sorted(missing)}, "
            f"unrequested={sorted(unrequested)}"
        )

    definitions = {
        definition.fact_name: definition
        for definition in catalogue.facts
    }
    for fact in facts:
        definition = definitions.get(fact.fact_name)
        if definition is None:
            raise FactCompletenessError(
                f"fact is not registered in catalogue: {fact.fact_name}"
            )
        _validate_resolved_fact(
            fact=fact,
            definition=definition,
            case_run=case_run,
            documents_by_id=documents_by_id,
        )

    if not resolver_version.strip():
        raise ResolutionContextError(
            "resolver_version cannot be blank"
        )
    result_fields = {
        "resolution_id": resolution_id or uuid4(),
        "run_id": case_run.run_id,
        "selection_id": selection.selection_id,
        "policy_id": selection.policy_id,
        "rulebook_version": selection.rulebook_version,
        "policy_sha256": selection.policy_sha256,
        "requested_facts": requested_facts,
        "facts": facts,
        "resolver_version": resolver_version,
        "created_at": created_at or datetime.now(timezone.utc),
        "status": "READY_FOR_EVALUATION",
    }
    draft = FactResolutionResult.model_construct(
        **result_fields,
        content_hash="0" * 64,
    )
    return FactResolutionResult(
        **result_fields,
        content_hash=calculate_fact_resolution_content_hash(draft),
    )


def _validate_context(
    *,
    selection: PolicySelection,
    case_run: CaseRun,
    catalogue: FactCatalogue,
    evidence_bundle: EvidenceBundle,
) -> None:
    if selection.status is not PolicySelectionStatus.READY_FOR_EXTRACTION:
        raise ResolutionContextError(
            "policy selection is not ready for extraction"
        )
    if selection.rulebook_version != SUPPORTED_RULEBOOK_VERSION:
        raise ResolutionContextError(
            "fact resolution requires take-home-v2 selection"
        )
    if catalogue.rulebook_version != selection.rulebook_version:
        raise ResolutionContextError(
            "catalogue version does not match policy selection"
        )

    policy = selection.policy_snapshot
    selection_identity = (
        selection.policy_id,
        selection.rulebook_version,
        selection.scheme,
        selection.reason_code,
    )
    snapshot_identity = (
        policy.policy_id,
        policy.rulebook_version,
        policy.scheme,
        policy.reason_code,
    )
    if selection_identity != snapshot_identity:
        raise ResolutionContextError(
            "policy selection identity does not match its frozen snapshot"
        )
    if selection.run_id != case_run.run_id:
        raise ResolutionContextError(
            "policy selection does not belong to the case run"
        )
    if (
        selection.scheme != case_run.case_snapshot.scheme
        or selection.reason_code
        != case_run.case_snapshot.reason_code
    ):
        raise ResolutionContextError(
            "selected policy does not match immutable case metadata"
        )
    if (
        evidence_bundle.selection_id != selection.selection_id
        or evidence_bundle.run_id != case_run.run_id
        or evidence_bundle.policy_id != selection.policy_id
    ):
        raise ResolutionContextError(
            "evidence bundle does not belong to the selection and run"
        )


def _validate_document_membership(
    *,
    evidence_bundle: EvidenceBundle,
    evidence_documents: tuple[EvidenceDocument, ...],
) -> dict[UUID, EvidenceDocument]:
    if len(evidence_documents) != len(evidence_bundle.evidence_files):
        raise ResolutionContextError(
            "converted documents do not cover the complete evidence bundle"
        )

    documents_by_id: dict[UUID, EvidenceDocument] = {}
    for evidence_file, document in zip(
        evidence_bundle.evidence_files,
        evidence_documents,
        strict=True,
    ):
        if document.document_id in documents_by_id:
            raise ResolutionContextError(
                f"duplicate evidence document ID: {document.document_id}"
            )
        if (
            document.file_name != evidence_file.file_name
            or document.source_sha256 != evidence_file.sha256
            or document.media_type != evidence_file.media_type
        ):
            raise ResolutionContextError(
                "evidence document does not match its admitted bundle file: "
                f"{document.file_name}"
            )
        documents_by_id[document.document_id] = document
    return documents_by_id


def _validate_resolved_fact(
    *,
    fact: ResolvedFact,
    definition: FactDefinition,
    case_run: CaseRun,
    documents_by_id: dict[UUID, EvidenceDocument],
) -> None:
    for resolved_value in fact.resolved_values:
        _validate_exact_value_type(
            fact_name=fact.fact_name,
            value=resolved_value.value,
            definition=definition,
        )

    if (
        fact.status == "FOUND"
        and definition.cardinality == "SINGLE"
        and len(fact.resolved_values) != 1
    ):
        raise FactValueValidationError(
            f"SINGLE fact {fact.fact_name} requires exactly one "
            "established value"
        )

    if definition.source_type == "CASE_METADATA":
        _validate_metadata_fact(
            fact=fact,
            case_run=case_run,
        )
    elif definition.source_type == "EVIDENCE_TEXT_ASSERTION":
        _validate_text_fact(
            fact=fact,
            documents_by_id=documents_by_id,
        )
    else:
        _validate_visual_fact(
            fact=fact,
            documents_by_id=documents_by_id,
        )


def _validate_exact_value_type(
    *,
    fact_name: str,
    value: object,
    definition: FactDefinition,
) -> None:
    expected_types: dict[str, type] = {
        "STRING": str,
        "ADDRESS": str,
        "INTEGER": int,
        "DECIMAL": Decimal,
        "BOOLEAN": bool,
        "DATE": date,
        "DATETIME": datetime,
    }
    expected_type = expected_types[definition.value_type]
    if type(value) is not expected_type:
        raise FactValueValidationError(
            f"{fact_name} requires {definition.value_type}; "
            f"received {type(value).__name__}"
        )


def _validate_metadata_fact(
    *,
    fact: ResolvedFact,
    case_run: CaseRun,
) -> None:
    if fact.resolver_type != "CASE_METADATA":
        raise FactRoutingValidationError(
            f"metadata fact routed through evidence extraction: "
            f"{fact.fact_name}"
        )
    if fact.status not in {"FOUND", "NOT_FOUND"}:
        raise FactRoutingValidationError(
            "deterministic metadata facts cannot be ambiguous or "
            f"conflicting: {fact.fact_name}"
        )

    binding = get_metadata_binding(fact.fact_name)
    if binding is None:
        raise FactRoutingValidationError(
            f"no approved metadata binding for {fact.fact_name}"
        )
    deterministic_value = binding.resolver(case_run)
    if fact.status == "NOT_FOUND":
        if deterministic_value is not None:
            raise FactValueValidationError(
                f"metadata value exists for {fact.fact_name}"
            )
        return

    resolved_value = fact.resolved_values[0]
    if (
        type(resolved_value.value) is not type(deterministic_value)
        or resolved_value.value != deterministic_value
    ):
        raise FactValueValidationError(
            f"metadata value does not match immutable field for "
            f"{fact.fact_name}"
        )
    for provenance in resolved_value.provenance:
        if not isinstance(provenance, CaseMetadataProvenance):
            raise FactRoutingValidationError(
                f"metadata fact has non-metadata provenance: "
                f"{fact.fact_name}"
            )
        if provenance.case_run_id != case_run.run_id:
            raise FactProvenanceValidationError(
                "metadata provenance references the wrong case run"
            )
        if provenance.field_path != binding.field_path:
            raise FactProvenanceValidationError(
                f"unapproved field path for {fact.fact_name}: "
                f"{provenance.field_path}"
            )


def _validate_text_fact(
    *,
    fact: ResolvedFact,
    documents_by_id: dict[UUID, EvidenceDocument],
) -> None:
    if fact.resolver_type != "EVIDENCE_EXTRACTION":
        raise FactRoutingValidationError(
            f"text evidence fact routed through metadata: "
            f"{fact.fact_name}"
        )
    for resolved_value in fact.resolved_values:
        for provenance in resolved_value.provenance:
            if not isinstance(provenance, EvidenceTextProvenance):
                raise FactRoutingValidationError(
                    f"text assertion requires text provenance: "
                    f"{fact.fact_name}"
                )
            page = _resolve_evidence_page(
                provenance=provenance,
                documents_by_id=documents_by_id,
            )
            if page.extraction_method.value != (
                provenance.extraction_method
            ):
                raise FactProvenanceValidationError(
                    "text provenance extraction method does not match "
                    "the converted page"
                )
            if not supporting_text_occurs_on_page(
                supporting_text=provenance.supporting_text,
                canonical_page_text=page.text,
            ):
                raise FactProvenanceValidationError(
                    "supporting_text does not occur on the referenced page"
                )


def _validate_visual_fact(
    *,
    fact: ResolvedFact,
    documents_by_id: dict[UUID, EvidenceDocument],
) -> None:
    if fact.resolver_type != "EVIDENCE_EXTRACTION":
        raise FactRoutingValidationError(
            f"visual fact routed through metadata: {fact.fact_name}"
        )
    for resolved_value in fact.resolved_values:
        for provenance in resolved_value.provenance:
            if not isinstance(provenance, VisualObservationProvenance):
                raise FactRoutingValidationError(
                    f"visual fact requires visual provenance: "
                    f"{fact.fact_name}"
                )
            page = _resolve_evidence_page(
                provenance=provenance,
                documents_by_id=documents_by_id,
            )
            if page.extraction_method is not ExtractionMethod.VISION:
                raise FactProvenanceValidationError(
                    "visual provenance must reference a VISION page"
                )


def _resolve_evidence_page(
    *,
    provenance: EvidenceTextProvenance
    | VisualObservationProvenance,
    documents_by_id: dict[UUID, EvidenceDocument],
) -> EvidencePage:
    document = documents_by_id.get(provenance.document_id)
    if document is None:
        raise FactProvenanceValidationError(
            f"unknown evidence document: {provenance.document_id}"
        )
    if provenance.file_name != document.file_name:
        raise FactProvenanceValidationError(
            "provenance file_name does not match the evidence document"
        )
    if provenance.page_number > len(document.pages):
        raise FactProvenanceValidationError(
            f"page {provenance.page_number} does not exist in "
            f"{document.file_name}"
        )
    return document.pages[provenance.page_number - 1]
