"""Deterministically adapt untrusted extraction candidates."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TypeAlias
from uuid import UUID

from dispute_copilot.extraction.citation_text import (
    normalize_citation_text,
    supporting_text_occurs_on_page,
)
from dispute_copilot.extraction.errors import (
    EvidenceCandidateProvenanceError,
    EvidenceCandidateValidationError,
    EvidenceCandidateValueError,
)
from dispute_copilot.extraction.evidence_extraction_models import (
    CandidateRawValue,
    ExtractionCandidate,
    ExtractionEvidenceReference,
    TextExtractionEvidenceReference,
    VisualExtractionEvidenceReference,
)
from dispute_copilot.extraction.fact_resolution_models import (
    EvidenceTextProvenance,
    FactProvenance,
    FactValue,
    ResolvedFact,
    ResolvedValue,
    VisualObservationProvenance,
)
from dispute_copilot.extraction.models import (
    EvidenceDocument,
    EvidencePage,
    ExtractionMethod,
)
from dispute_copilot.policy.fact_models import FactDefinition

ParsedValueKey: TypeAlias = tuple[type, FactValue]
ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def to_resolved_facts(
    *,
    provider_candidates: tuple[ExtractionCandidate, ...],
    requested_facts: tuple[FactDefinition, ...],
    evidence_documents: tuple[EvidenceDocument, ...],
) -> tuple[ResolvedFact, ...]:
    """Validate and adapt candidates in requested-policy order."""
    requested_names = tuple(
        definition.fact_name for definition in requested_facts
    )
    if len(requested_names) != len(set(requested_names)):
        raise EvidenceCandidateValidationError(
            "requested evidence facts must be unique"
        )

    candidate_names = tuple(
        candidate.fact_name for candidate in provider_candidates
    )
    if len(candidate_names) != len(set(candidate_names)):
        raise EvidenceCandidateValidationError(
            "provider returned duplicate fact candidates"
        )

    missing = set(requested_names) - set(candidate_names)
    unrequested = set(candidate_names) - set(requested_names)
    if missing or unrequested:
        raise EvidenceCandidateValidationError(
            "provider candidates must exactly cover requested evidence "
            f"facts; missing={sorted(missing)}, "
            f"unrequested={sorted(unrequested)}"
        )

    candidates_by_name = {
        candidate.fact_name: candidate
        for candidate in provider_candidates
    }
    candidate_positions = {
        candidate.fact_name: index
        for index, candidate in enumerate(provider_candidates)
    }
    documents_by_id = _index_documents(evidence_documents)

    resolved: list[ResolvedFact] = []
    for definition in requested_facts:
        if definition.source_type == "CASE_METADATA":
            raise EvidenceCandidateValidationError(
                "metadata fact cannot enter evidence candidate adapter: "
                f"{definition.fact_name}"
            )
        candidate = candidates_by_name[definition.fact_name]
        resolved_values = _adapt_values(
            candidate=candidate,
            candidate_index=candidate_positions[candidate.fact_name],
            definition=definition,
            documents_by_id=documents_by_id,
        )
        if (
            candidate.status == "FOUND"
            and definition.cardinality == "SINGLE"
            and len(resolved_values) != 1
        ):
            raise EvidenceCandidateValueError(
                f"SINGLE fact {definition.fact_name} requires exactly "
                "one established value"
            )
        resolved.append(
            ResolvedFact(
                fact_name=definition.fact_name,
                status=candidate.status,
                resolved_values=resolved_values,
                resolver_type="EVIDENCE_EXTRACTION",
                confidence=candidate.confidence,
            )
        )
    return tuple(resolved)


def _adapt_values(
    *,
    candidate: ExtractionCandidate,
    candidate_index: int,
    definition: FactDefinition,
    documents_by_id: dict[UUID, EvidenceDocument],
) -> tuple[ResolvedValue, ...]:
    resolved: list[ResolvedValue] = []
    seen_values: set[ParsedValueKey] = set()
    for value_index, candidate_value in enumerate(candidate.values):
        value = _parse_value(
            fact_name=definition.fact_name,
            raw_value=candidate_value.raw_value,
            value_type=definition.value_type,
        )
        key = (type(value), value)
        if key in seen_values:
            raise EvidenceCandidateValueError(
                "provider returned duplicate value entries for "
                f"{definition.fact_name}; attach multiple references to "
                "one value instead"
            )
        seen_values.add(key)

        provenance_items: list[FactProvenance] = []
        for reference_index, reference in enumerate(
            candidate_value.references
        ):
            provenance_items.append(
                _adapt_reference(
                    fact_name=definition.fact_name,
                    expected_source_type=definition.source_type,
                    reference=reference,
                    documents_by_id=documents_by_id,
                    candidate_path=(
                        f"candidates.{candidate_index}.values."
                        f"{value_index}.references.{reference_index}"
                    ),
                )
            )
        provenance = tuple(provenance_items)
        resolved.append(
            ResolvedValue(
                value=value,
                provenance=provenance,
            )
        )
    return tuple(resolved)


def _parse_value(
    *,
    fact_name: str,
    raw_value: CandidateRawValue,
    value_type: str,
) -> FactValue:
    if value_type in {"STRING", "ADDRESS"}:
        if type(raw_value) is not str or not raw_value.strip():
            raise EvidenceCandidateValueError(
                f"{fact_name} requires a non-blank {value_type}"
            )
        return raw_value

    if value_type == "INTEGER":
        if type(raw_value) is not int:
            raise EvidenceCandidateValueError(
                f"{fact_name} requires an exact INTEGER"
            )
        return raw_value

    if value_type == "BOOLEAN":
        if type(raw_value) is not bool:
            raise EvidenceCandidateValueError(
                f"{fact_name} requires an exact BOOLEAN"
            )
        return raw_value

    if type(raw_value) is not str or raw_value != raw_value.strip():
        raise EvidenceCandidateValueError(
            f"{fact_name} requires a canonical {value_type} string"
        )

    if value_type == "DATE":
        if not ISO_DATE_PATTERN.fullmatch(raw_value):
            raise EvidenceCandidateValueError(
                f"{fact_name} requires an ISO DATE"
            )
        try:
            return date.fromisoformat(raw_value)
        except ValueError as error:
            raise EvidenceCandidateValueError(
                f"{fact_name} contains an invalid DATE"
            ) from error

    if value_type == "DATETIME":
        try:
            parsed_datetime = datetime.fromisoformat(raw_value)
        except ValueError as error:
            raise EvidenceCandidateValueError(
                f"{fact_name} contains an invalid DATETIME"
            ) from error
        if (
            parsed_datetime.tzinfo is None
            or parsed_datetime.utcoffset() is None
        ):
            raise EvidenceCandidateValueError(
                f"{fact_name} requires a timezone-aware DATETIME"
            )
        return parsed_datetime

    if value_type == "DECIMAL":
        try:
            parsed_decimal = Decimal(raw_value)
        except InvalidOperation as error:
            raise EvidenceCandidateValueError(
                f"{fact_name} contains an invalid DECIMAL"
            ) from error
        if not parsed_decimal.is_finite():
            raise EvidenceCandidateValueError(
                f"{fact_name} requires a finite DECIMAL"
            )
        return parsed_decimal

    raise EvidenceCandidateValueError(
        f"{fact_name} has unsupported value type {value_type}"
    )


def _adapt_reference(
    *,
    fact_name: str,
    expected_source_type: str,
    reference: ExtractionEvidenceReference,
    documents_by_id: dict[UUID, EvidenceDocument],
    candidate_path: str,
) -> FactProvenance:
    actual_source_type = (
        "EVIDENCE_TEXT_ASSERTION"
        if isinstance(reference, TextExtractionEvidenceReference)
        else "EVIDENCE_VISUAL_OBSERVATION"
    )
    if actual_source_type != expected_source_type:
        raise EvidenceCandidateProvenanceError(
            f"{fact_name} requires {expected_source_type} provenance"
        )
    document = documents_by_id.get(reference.document_id)
    if document is None:
        raise EvidenceCandidateProvenanceError(
            f"unknown evidence document: {reference.document_id}"
        )
    if reference.file_name != document.file_name:
        raise EvidenceCandidateProvenanceError(
            "candidate file_name does not match its document"
        )
    page = _get_page(
        document=document,
        page_number=reference.page_number,
    )
    if reference.extraction_method != page.extraction_method.value:
        raise EvidenceCandidateProvenanceError(
            "candidate extraction method does not match its page"
        )

    if expected_source_type == "EVIDENCE_TEXT_ASSERTION":
        if not isinstance(
            reference,
            TextExtractionEvidenceReference,
        ):
            raise EvidenceCandidateProvenanceError(
                f"{fact_name} requires TEXT evidence"
            )
        supporting_text = reference.supporting_text
        if not supporting_text_occurs_on_page(
            supporting_text=supporting_text,
            canonical_page_text=page.text,
        ):
            raise EvidenceCandidateProvenanceError(
                f"{candidate_path}: supporting_text does not occur in "
                "canonical page text; "
                f"document={document.file_name!r}; "
                f"document_id={str(document.document_id)!r}; "
                f"page={page.page_number}; "
                "rejected_supporting_text="
                f"{_bounded_repr(supporting_text)}; "
                "canonical_page_text_empty="
                f"{not bool(normalize_citation_text(page.text))}"
            )
        return EvidenceTextProvenance(
            source_type="EVIDENCE_TEXT_ASSERTION",
            document_id=document.document_id,
            file_name=document.file_name,
            page_number=page.page_number,
            extraction_method=reference.extraction_method,
            supporting_text=supporting_text,
        )

    if not isinstance(
        reference,
        VisualExtractionEvidenceReference,
    ):
        raise EvidenceCandidateProvenanceError(
            f"{fact_name} requires VISUAL evidence"
        )
    if page.extraction_method is not ExtractionMethod.VISION:
        raise EvidenceCandidateProvenanceError(
            "visual provenance must reference a VISION page"
        )
    return VisualObservationProvenance(
        source_type="EVIDENCE_VISUAL_OBSERVATION",
        document_id=document.document_id,
        file_name=document.file_name,
        page_number=page.page_number,
        observation=reference.visual_observation,
    )


def _bounded_repr(value: str, limit: int = 500) -> str:
    """Render rejected evidence text without unbounded log output."""
    rendered = repr(value)
    if len(rendered) <= limit:
        return rendered
    return rendered[: limit - 3] + "..."


def _index_documents(
    evidence_documents: tuple[EvidenceDocument, ...],
) -> dict[UUID, EvidenceDocument]:
    documents_by_id: dict[UUID, EvidenceDocument] = {}
    for document in evidence_documents:
        if document.document_id in documents_by_id:
            raise EvidenceCandidateProvenanceError(
                f"duplicate document ID: {document.document_id}"
            )
        documents_by_id[document.document_id] = document
    return documents_by_id


def _get_page(
    *,
    document: EvidenceDocument,
    page_number: int,
) -> EvidencePage:
    if page_number > len(document.pages):
        raise EvidenceCandidateProvenanceError(
            f"page {page_number} does not exist in {document.file_name}"
        )
    return document.pages[page_number - 1]
