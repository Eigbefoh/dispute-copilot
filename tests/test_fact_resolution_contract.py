"""Tests for the immutable fact-resolution admission boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from dispute_copilot.domain.case_run import CaseRun
from dispute_copilot.domain.policy_selection import PolicySelection
from dispute_copilot.extraction.bundle_loader import load_evidence_bundle
from dispute_copilot.extraction.document_converter import DocumentConverter
from dispute_copilot.extraction.fact_resolution_models import (
    CaseMetadataProvenance,
    EvidenceTextProvenance,
    FactResolutionResult,
    ResolvedFact,
    ResolvedValue,
    VisualObservationProvenance,
)
from dispute_copilot.extraction.fact_resolution_validation import (
    FactCompletenessError,
    FactProvenanceValidationError,
    FactRoutingValidationError,
    FactValueValidationError,
    ResolutionContextError,
    create_fact_resolution_result,
)
from dispute_copilot.extraction.metadata_resolver import (
    get_metadata_binding,
)
from dispute_copilot.extraction.models import (
    EvidenceBundle,
    EvidenceDocument,
    ExtractionMethod,
)
from dispute_copilot.orchestration.service import create_case_run
from dispute_copilot.policy.fact_models import FactCatalogue
from dispute_copilot.policy.import_rulebook import import_rulebook
from dispute_copilot.policy.selection_service import select_policy_for_run
from dispute_copilot.repositories.sqlite import SQLiteCaseRunRepository

PROJECT_ROOT = Path(__file__).parents[1]
CASES_PATH = PROJECT_ROOT / "data" / "cases.json"
EVIDENCE_DIRECTORY = PROJECT_ROOT / "data" / "evidence"
RULEBOOK_DIRECTORY = (
    PROJECT_ROOT / "data" / "rulebooks" / "take-home-v2"
)
DELIVERY_PHOTO = "CB-2025-0006_delivery_photo.png"


class FixtureVisionProvider:
    provider_version = "fixture-vision/contract-1"

    def describe_image(self, *, file_path: Path) -> str:
        return (
            "The image shows a sage-coloured armchair from the front. "
            "No serial number is visible."
        )


@dataclass(frozen=True)
class ContractContext:
    selection: PolicySelection
    run: CaseRun
    catalogue: FactCatalogue
    bundle: EvidenceBundle
    documents: tuple[EvidenceDocument, ...]


@pytest.fixture
def context(tmp_path: Path) -> ContractContext:
    database_path = tmp_path / "test.db"
    import_rulebook(RULEBOOK_DIRECTORY, database_path)
    run = create_case_run(
        "CB-2025-0006",
        CASES_PATH,
        EVIDENCE_DIRECTORY,
    )
    SQLiteCaseRunRepository(database_path).save(run)
    selection = select_policy_for_run(
        run.run_id,
        "take-home-v2",
        database_path,
    )
    bundle = load_evidence_bundle(
        selection_id=selection.selection_id,
        database_path=database_path,
    )
    converter = DocumentConverter(
        vision_provider=FixtureVisionProvider()
    )
    documents = tuple(
        converter.convert(evidence_file)
        for evidence_file in bundle.evidence_files
    )
    catalogue = FactCatalogue.model_validate_json(
        (
            RULEBOOK_DIRECTORY / "facts" / "catalogue.json"
        ).read_text()
    )
    return ContractContext(
        selection=selection,
        run=run,
        catalogue=catalogue,
        bundle=bundle,
        documents=documents,
    )


def selection_for(
    context: ContractContext,
    *fact_names: str,
) -> PolicySelection:
    requirement = (
        context.selection.policy_snapshot.requirements[0].model_copy(
            update={"required_facts": tuple(fact_names)}
        )
    )
    policy = context.selection.policy_snapshot.model_copy(
        update={"requirements": (requirement,)}
    )
    return context.selection.model_copy(
        update={"policy_snapshot": policy}
    )


def text_provenance(
    document: EvidenceDocument,
    supporting_text: str,
    *,
    method: str = "NATIVE_TEXT",
    page_number: int = 1,
    document_id: UUID | None = None,
    file_name: str | None = None,
) -> EvidenceTextProvenance:
    return EvidenceTextProvenance(
        source_type="EVIDENCE_TEXT_ASSERTION",
        document_id=document_id or document.document_id,
        file_name=file_name or document.file_name,
        page_number=page_number,
        extraction_method=method,
        supporting_text=supporting_text,
    )


def visual_provenance(
    document: EvidenceDocument,
    *,
    page_number: int = 1,
) -> VisualObservationProvenance:
    return VisualObservationProvenance(
        source_type="EVIDENCE_VISUAL_OBSERVATION",
        document_id=document.document_id,
        file_name=document.file_name,
        page_number=page_number,
        observation=(
            "The image shows a sage-coloured armchair from the front."
        ),
    )


def admit(
    context: ContractContext,
    *,
    selection: PolicySelection,
    facts: tuple[ResolvedFact, ...],
    documents: tuple[EvidenceDocument, ...] | None = None,
    catalogue: FactCatalogue | None = None,
) -> FactResolutionResult:
    return create_fact_resolution_result(
        selection=selection,
        case_run=context.run,
        catalogue=catalogue or context.catalogue,
        evidence_bundle=context.bundle,
        evidence_documents=documents or context.documents,
        facts=facts,
        resolver_version="fact-contract/0.1.0",
    )


def test_valid_deterministic_metadata_fact(
    context: ContractContext,
) -> None:
    selection = selection_for(context, "avs_result")
    binding = get_metadata_binding("avs_result")
    assert binding is not None
    fact = ResolvedFact(
        fact_name="avs_result",
        status="FOUND",
        resolved_values=(
            ResolvedValue(
                value=context.run.case_snapshot.transaction.avs_result,
                provenance=(
                    CaseMetadataProvenance(
                        source_type="CASE_METADATA",
                        case_run_id=context.run.run_id,
                        field_path=binding.field_path,
                    ),
                ),
            ),
        ),
        resolver_type="CASE_METADATA",
    )

    result = admit(context, selection=selection, facts=(fact,))

    assert result.status == "READY_FOR_EVALUATION"
    assert result.requested_facts == ("avs_result",)
    assert result.facts == (fact,)
    assert result.selection_id == selection.selection_id
    assert FactResolutionResult.model_validate_json(
        result.model_dump_json()
    ) == result
    tampered = json.loads(result.model_dump_json())
    tampered["facts"][0]["resolved_values"][0]["value"] = "tampered"
    with pytest.raises(ValidationError, match="content_hash"):
        FactResolutionResult.model_validate_json(json.dumps(tampered))


def test_valid_native_text_evidence_fact(
    context: ContractContext,
) -> None:
    selection = selection_for(
        context,
        "merchant_stated_product_description",
    )
    product_listing = context.documents[0]
    fact = ResolvedFact(
        fact_name="merchant_stated_product_description",
        status="FOUND",
        resolved_values=(
            ResolvedValue(
                value="Carlton Mid-Century Armchair",
                provenance=(
                    text_provenance(
                        product_listing,
                        "Carlton Mid-Century Armchair",
                    ),
                ),
            ),
        ),
        resolver_type="EVIDENCE_EXTRACTION",
        confidence=0.98,
    )

    result = admit(context, selection=selection, facts=(fact,))

    assert result.facts[0].resolved_values[0].value == (
        "Carlton Mid-Century Armchair"
    )


def test_final_admission_uses_shared_citation_normalization(
    context: ContractContext,
) -> None:
    selection = selection_for(
        context,
        "merchant_stated_product_description",
    )
    product_listing = context.documents[0]
    fact = ResolvedFact(
        fact_name="merchant_stated_product_description",
        status="FOUND",
        resolved_values=(
            ResolvedValue(
                value="Carlton Mid-Century Armchair",
                provenance=(
                    text_provenance(
                        product_listing,
                        "Ｃarlton   Mid-Century\nArmchair",
                    ),
                ),
            ),
        ),
        resolver_type="EVIDENCE_EXTRACTION",
        confidence=0.98,
    )

    result = admit(context, selection=selection, facts=(fact,))

    assert result.status == "READY_FOR_EVALUATION"


def test_valid_ocr_derived_fact(context: ContractContext) -> None:
    selection = selection_for(
        context,
        "merchant_stated_product_description",
    )
    original = context.documents[0]
    ocr_page = original.pages[0].model_copy(
        update={"extraction_method": ExtractionMethod.OCR}
    )
    ocr_document = original.model_copy(update={"pages": (ocr_page,)})
    documents = (ocr_document, *context.documents[1:])
    fact = ResolvedFact(
        fact_name="merchant_stated_product_description",
        status="FOUND",
        resolved_values=(
            ResolvedValue(
                value="Carlton Mid-Century Armchair",
                provenance=(
                    text_provenance(
                        ocr_document,
                        "Carlton Mid-Century Armchair",
                        method="OCR",
                    ),
                ),
            ),
        ),
        resolver_type="EVIDENCE_EXTRACTION",
        confidence=0.91,
    )

    result = admit(
        context,
        selection=selection,
        facts=(fact,),
        documents=documents,
    )

    provenance = result.facts[0].resolved_values[0].provenance[0]
    assert provenance.extraction_method == "OCR"


def test_valid_delivery_photo_visual_observation(
    context: ContractContext,
) -> None:
    selection = selection_for(
        context,
        "image_observed_item_colour",
    )
    photo = context.documents[1]
    assert photo.file_name == DELIVERY_PHOTO
    fact = ResolvedFact(
        fact_name="image_observed_item_colour",
        status="FOUND",
        resolved_values=(
            ResolvedValue(
                value="sage",
                provenance=(visual_provenance(photo),),
            ),
        ),
        resolver_type="EVIDENCE_EXTRACTION",
        confidence=0.97,
    )

    result = admit(context, selection=selection, facts=(fact,))

    assert result.facts[0].fact_name == "image_observed_item_colour"
    serialized = result.model_dump_json().lower()
    for forbidden in (
        "product_matches_listing",
        "item_is_not_defective",
        "delivery_is_proven",
        "visa 13.3 is satisfied",
        "merchant_should_represent",
    ):
        assert forbidden not in serialized


def test_valid_multiple_fact_uses_separate_resolved_values(
    context: ContractContext,
) -> None:
    selection = selection_for(
        context,
        "merchant_stated_return_method",
    )
    returns_policy = context.documents[2]
    fact = ResolvedFact(
        fact_name="merchant_stated_return_method",
        status="FOUND",
        resolved_values=(
            ResolvedValue(
                value="contact returns by email",
                provenance=(
                    text_provenance(
                        returns_policy,
                        "contact\nreturns@havenhome.example",
                    ),
                ),
            ),
            ResolvedValue(
                value="free collection",
                provenance=(
                    text_provenance(
                        returns_policy,
                        "free collection",
                    ),
                ),
            ),
        ),
        resolver_type="EVIDENCE_EXTRACTION",
        confidence=0.95,
    )

    result = admit(context, selection=selection, facts=(fact,))

    assert len(result.facts[0].resolved_values) == 2


def test_explicit_not_found_is_valid(context: ContractContext) -> None:
    selection = selection_for(
        context,
        "image_observed_serial_number",
    )
    fact = ResolvedFact(
        fact_name="image_observed_serial_number",
        status="NOT_FOUND",
        resolved_values=(),
        resolver_type="EVIDENCE_EXTRACTION",
    )

    result = admit(context, selection=selection, facts=(fact,))

    assert result.facts[0].status == "NOT_FOUND"


def test_ambiguous_requires_and_accepts_candidate_value(
    context: ContractContext,
) -> None:
    selection = selection_for(
        context,
        "image_observed_item_colour",
    )
    photo = context.documents[1]
    fact = ResolvedFact(
        fact_name="image_observed_item_colour",
        status="AMBIGUOUS",
        resolved_values=(
            ResolvedValue(
                value="sage or grey",
                provenance=(visual_provenance(photo),),
            ),
        ),
        resolver_type="EVIDENCE_EXTRACTION",
        confidence=0.45,
    )

    assert admit(
        context,
        selection=selection,
        facts=(fact,),
    ).facts[0].status == "AMBIGUOUS"

    with pytest.raises(
        ValidationError,
        match="AMBIGUOUS requires at least one",
    ):
        ResolvedFact(
            fact_name="image_observed_item_colour",
            status="AMBIGUOUS",
            resolved_values=(),
            resolver_type="EVIDENCE_EXTRACTION",
        )


def test_conflicting_allows_distinct_candidates_for_single_fact(
    context: ContractContext,
) -> None:
    selection = selection_for(
        context,
        "merchant_stated_product_description",
    )
    product_listing = context.documents[0]
    page = product_listing.pages[0].model_copy(
        update={
            "text": (
                product_listing.pages[0].text
                + "\nAlternate record: Blue sofa"
            )
        }
    )
    changed_document = product_listing.model_copy(
        update={"pages": (page,)}
    )
    documents = (changed_document, *context.documents[1:])
    fact = ResolvedFact(
        fact_name="merchant_stated_product_description",
        status="CONFLICTING",
        resolved_values=(
            ResolvedValue(
                value="Carlton Mid-Century Armchair",
                provenance=(
                    text_provenance(
                        changed_document,
                        "Carlton Mid-Century Armchair",
                    ),
                ),
            ),
            ResolvedValue(
                value="Blue sofa",
                provenance=(
                    text_provenance(
                        changed_document,
                        "Blue sofa",
                    ),
                ),
            ),
        ),
        resolver_type="EVIDENCE_EXTRACTION",
        confidence=0.5,
    )

    result = admit(
        context,
        selection=selection,
        facts=(fact,),
        documents=documents,
    )

    assert result.facts[0].status == "CONFLICTING"
    assert len(result.facts[0].resolved_values) == 2


def test_duplicate_candidate_values_are_rejected_and_sources_merge(
    context: ContractContext,
) -> None:
    document = context.documents[0]
    first = text_provenance(
        document,
        "Carlton Mid-Century Armchair",
    )
    second = text_provenance(document, "Sage Velvet")

    with pytest.raises(
        ValidationError,
        match="candidate values must be distinct",
    ):
        ResolvedFact(
            fact_name="merchant_stated_product_description",
            status="CONFLICTING",
            resolved_values=(
                ResolvedValue(
                    value="armchair",
                    provenance=(first,),
                ),
                ResolvedValue(
                    value="armchair",
                    provenance=(second,),
                ),
            ),
            resolver_type="EVIDENCE_EXTRACTION",
        )

    merged = ResolvedValue(
        value="armchair",
        provenance=(first, second),
    )
    assert len(merged.provenance) == 2


def test_missing_unrequested_and_duplicate_facts_are_rejected(
    context: ContractContext,
) -> None:
    selection = selection_for(
        context,
        "image_observed_item_colour",
    )
    requested = ResolvedFact(
        fact_name="image_observed_item_colour",
        status="NOT_FOUND",
        resolved_values=(),
        resolver_type="EVIDENCE_EXTRACTION",
    )
    unrequested = ResolvedFact(
        fact_name="image_observed_item_type",
        status="NOT_FOUND",
        resolved_values=(),
        resolver_type="EVIDENCE_EXTRACTION",
    )

    with pytest.raises(FactCompletenessError, match="missing"):
        admit(context, selection=selection, facts=())
    with pytest.raises(FactCompletenessError, match="unrequested"):
        admit(context, selection=selection, facts=(unrequested,))
    with pytest.raises(FactCompletenessError, match="unique"):
        admit(
            context,
            selection=selection,
            facts=(requested, requested),
        )


@pytest.mark.parametrize("wrong_value", ["14", True, Decimal("14")])
def test_wrong_exact_value_type_is_rejected(
    context: ContractContext,
    wrong_value,
) -> None:
    selection = selection_for(
        context,
        "merchant_stated_return_window_days",
    )
    returns_policy = context.documents[2]
    fact = ResolvedFact(
        fact_name="merchant_stated_return_window_days",
        status="FOUND",
        resolved_values=(
            ResolvedValue(
                value=wrong_value,
                provenance=(
                    text_provenance(returns_policy, "14 days"),
                ),
            ),
        ),
        resolver_type="EVIDENCE_EXTRACTION",
    )

    with pytest.raises(
        FactValueValidationError,
        match="requires INTEGER",
    ):
        admit(context, selection=selection, facts=(fact,))


def test_pydantic_boundary_rejects_unsupported_float_value(
    context: ContractContext,
) -> None:
    with pytest.raises(ValidationError):
        ResolvedValue(
            value=14.0,
            provenance=(
                visual_provenance(context.documents[1]),
            ),
        )


def test_single_found_fact_rejects_multiple_values(
    context: ContractContext,
) -> None:
    selection = selection_for(
        context,
        "merchant_stated_product_description",
    )
    document = context.documents[0]
    fact = ResolvedFact(
        fact_name="merchant_stated_product_description",
        status="FOUND",
        resolved_values=(
            ResolvedValue(
                value="armchair",
                provenance=(
                    text_provenance(document, "armchair"),
                ),
            ),
            ResolvedValue(
                value="sage velvet",
                provenance=(
                    text_provenance(document, "sage velvet"),
                ),
            ),
        ),
        resolver_type="EVIDENCE_EXTRACTION",
    )

    with pytest.raises(
        FactValueValidationError,
        match="SINGLE",
    ):
        admit(context, selection=selection, facts=(fact,))


def test_invalid_status_value_combinations_and_confidence_fail() -> None:
    with pytest.raises(ValidationError, match="FOUND requires"):
        ResolvedFact(
            fact_name="example_fact",
            status="FOUND",
            resolved_values=(),
            resolver_type="EVIDENCE_EXTRACTION",
        )
    with pytest.raises(ValidationError, match="NOT_FOUND requires"):
        ResolvedFact(
            fact_name="example_fact",
            status="NOT_FOUND",
            resolved_values=(
                ResolvedValue(
                    value="value",
                    provenance=(
                        VisualObservationProvenance(
                            source_type=(
                                "EVIDENCE_VISUAL_OBSERVATION"
                            ),
                            document_id=uuid4(),
                            file_name="image.png",
                            page_number=1,
                            observation="Visible value.",
                        ),
                    ),
                ),
            ),
            resolver_type="EVIDENCE_EXTRACTION",
        )
    with pytest.raises(ValidationError):
        ResolvedFact(
            fact_name="example_fact",
            status="NOT_FOUND",
            resolved_values=(),
            resolver_type="EVIDENCE_EXTRACTION",
            confidence=1.1,
        )
    with pytest.raises(ValidationError):
        ResolvedFact(
            fact_name="example_fact",
            status="NOT_FOUND",
            resolved_values=(),
            resolver_type="EVIDENCE_EXTRACTION",
            confidence=float("nan"),
        )


def test_metadata_and_evidence_routes_cannot_cross(
    context: ContractContext,
) -> None:
    product_listing = context.documents[0]
    metadata_selection = selection_for(context, "avs_result")
    metadata_as_evidence = ResolvedFact(
        fact_name="avs_result",
        status="FOUND",
        resolved_values=(
            ResolvedValue(
                value=context.run.case_snapshot.transaction.avs_result,
                provenance=(
                    text_provenance(product_listing, "Product"),
                ),
            ),
        ),
        resolver_type="EVIDENCE_EXTRACTION",
    )
    with pytest.raises(
        FactRoutingValidationError,
        match="routed through evidence",
    ):
        admit(
            context,
            selection=metadata_selection,
            facts=(metadata_as_evidence,),
        )

    binding = get_metadata_binding("avs_result")
    assert binding is not None
    evidence_selection = selection_for(
        context,
        "merchant_stated_product_description",
    )
    evidence_as_metadata = ResolvedFact(
        fact_name="merchant_stated_product_description",
        status="FOUND",
        resolved_values=(
            ResolvedValue(
                value="Carlton Mid-Century Armchair",
                provenance=(
                    CaseMetadataProvenance(
                        source_type="CASE_METADATA",
                        case_run_id=context.run.run_id,
                        field_path=binding.field_path,
                    ),
                ),
            ),
        ),
        resolver_type="CASE_METADATA",
    )
    with pytest.raises(
        FactRoutingValidationError,
        match="routed through metadata",
    ):
        admit(
            context,
            selection=evidence_selection,
            facts=(evidence_as_metadata,),
        )


def test_wrong_metadata_run_field_or_value_is_rejected(
    context: ContractContext,
) -> None:
    selection = selection_for(context, "avs_result")
    binding = get_metadata_binding("avs_result")
    assert binding is not None

    for provenance, value, expected_error in (
        (
            CaseMetadataProvenance(
                source_type="CASE_METADATA",
                case_run_id=uuid4(),
                field_path=binding.field_path,
            ),
            context.run.case_snapshot.transaction.avs_result,
            "wrong case run",
        ),
        (
            CaseMetadataProvenance(
                source_type="CASE_METADATA",
                case_run_id=context.run.run_id,
                field_path="case_snapshot.transaction.cvv_result",
            ),
            context.run.case_snapshot.transaction.avs_result,
            "unapproved field path",
        ),
        (
            CaseMetadataProvenance(
                source_type="CASE_METADATA",
                case_run_id=context.run.run_id,
                field_path=binding.field_path,
            ),
            "not-the-frozen-value",
            "does not match immutable field",
        ),
    ):
        fact = ResolvedFact(
            fact_name="avs_result",
            status="FOUND",
            resolved_values=(
                ResolvedValue(
                    value=value,
                    provenance=(provenance,),
                ),
            ),
            resolver_type="CASE_METADATA",
        )
        with pytest.raises(
            (
                FactProvenanceValidationError,
                FactValueValidationError,
            ),
            match=expected_error,
        ):
            admit(context, selection=selection, facts=(fact,))


def test_visual_fact_cannot_use_textual_quotation(
    context: ContractContext,
) -> None:
    selection = selection_for(
        context,
        "image_observed_item_colour",
    )
    fact = ResolvedFact(
        fact_name="image_observed_item_colour",
        status="FOUND",
        resolved_values=(
            ResolvedValue(
                value="sage",
                provenance=(
                    text_provenance(
                        context.documents[0],
                        "Sage Velvet",
                    ),
                ),
            ),
        ),
        resolver_type="EVIDENCE_EXTRACTION",
    )

    with pytest.raises(
        FactRoutingValidationError,
        match="visual provenance",
    ):
        admit(context, selection=selection, facts=(fact,))


@pytest.mark.parametrize(
    ("document_id", "file_name", "page_number", "error"),
    [
        (uuid4(), None, 1, "unknown evidence document"),
        (None, "wrong.pdf", 1, "file_name"),
        (None, None, 2, "does not exist"),
    ],
)
def test_invalid_document_filename_or_page_is_rejected(
    context: ContractContext,
    document_id,
    file_name,
    page_number,
    error,
) -> None:
    selection = selection_for(
        context,
        "merchant_stated_product_description",
    )
    document = context.documents[0]
    fact = ResolvedFact(
        fact_name="merchant_stated_product_description",
        status="FOUND",
        resolved_values=(
            ResolvedValue(
                value="Carlton Mid-Century Armchair",
                provenance=(
                    text_provenance(
                        document,
                        "Carlton Mid-Century Armchair",
                        document_id=document_id,
                        file_name=file_name,
                        page_number=page_number,
                    ),
                ),
            ),
        ),
        resolver_type="EVIDENCE_EXTRACTION",
    )

    with pytest.raises(
        FactProvenanceValidationError,
        match=error,
    ):
        admit(context, selection=selection, facts=(fact,))


def test_supporting_text_and_extraction_method_are_verified(
    context: ContractContext,
) -> None:
    selection = selection_for(
        context,
        "merchant_stated_product_description",
    )
    document = context.documents[0]

    for provenance, error in (
        (
            text_provenance(document, "text not on the page"),
            "does not occur",
        ),
        (
            text_provenance(
                document,
                "Carlton Mid-Century Armchair",
                method="OCR",
            ),
            "extraction method",
        ),
    ):
        fact = ResolvedFact(
            fact_name="merchant_stated_product_description",
            status="FOUND",
            resolved_values=(
                ResolvedValue(
                    value="Carlton Mid-Century Armchair",
                    provenance=(provenance,),
                ),
            ),
            resolver_type="EVIDENCE_EXTRACTION",
        )
        with pytest.raises(
            FactProvenanceValidationError,
            match=error,
        ):
            admit(context, selection=selection, facts=(fact,))


def test_rulebook_and_catalogue_identity_are_exact(
    context: ContractContext,
) -> None:
    fact = ResolvedFact(
        fact_name="image_observed_serial_number",
        status="NOT_FOUND",
        resolved_values=(),
        resolver_type="EVIDENCE_EXTRACTION",
    )
    v1_selection = context.selection.model_copy(
        update={"rulebook_version": "take-home-v1"}
    )
    with pytest.raises(
        ResolutionContextError,
        match="take-home-v2",
    ):
        admit(
            context,
            selection=v1_selection,
            facts=(fact,),
        )

    selection = selection_for(
        context,
        "image_observed_serial_number",
    )
    wrong_catalogue = context.catalogue.model_copy(
        update={"rulebook_version": "different-version"}
    )
    with pytest.raises(
        ResolutionContextError,
        match="catalogue version",
    ):
        admit(
            context,
            selection=selection,
            facts=(fact,),
            catalogue=wrong_catalogue,
        )


def test_requested_facts_come_only_from_frozen_selection_snapshot(
    context: ContractContext,
) -> None:
    selection = selection_for(
        context,
        "image_observed_serial_number",
    )
    fact = ResolvedFact(
        fact_name="image_observed_serial_number",
        status="NOT_FOUND",
        resolved_values=(),
        resolver_type="EVIDENCE_EXTRACTION",
    )

    result = admit(context, selection=selection, facts=(fact,))

    assert result.requested_facts == (
        "image_observed_serial_number",
    )
    assert len(
        context.selection.policy_snapshot.requirements
    ) == 3


def test_all_67_catalogue_definitions_can_cross_the_contract(
    context: ContractContext,
) -> None:
    fact_names = tuple(
        definition.fact_name
        for definition in context.catalogue.facts
    )
    selection = selection_for(context, *fact_names)
    facts = []
    for definition in context.catalogue.facts:
        if definition.source_type != "CASE_METADATA":
            facts.append(
                ResolvedFact(
                    fact_name=definition.fact_name,
                    status="NOT_FOUND",
                    resolved_values=(),
                    resolver_type="EVIDENCE_EXTRACTION",
                )
            )
            continue

        binding = get_metadata_binding(definition.fact_name)
        assert binding is not None
        value = binding.resolver(context.run)
        if value is None:
            facts.append(
                ResolvedFact(
                    fact_name=definition.fact_name,
                    status="NOT_FOUND",
                    resolved_values=(),
                    resolver_type="CASE_METADATA",
                )
            )
        else:
            facts.append(
                ResolvedFact(
                    fact_name=definition.fact_name,
                    status="FOUND",
                    resolved_values=(
                        ResolvedValue(
                            value=value,
                            provenance=(
                                CaseMetadataProvenance(
                                    source_type="CASE_METADATA",
                                    case_run_id=context.run.run_id,
                                    field_path=binding.field_path,
                                ),
                            ),
                        ),
                    ),
                    resolver_type="CASE_METADATA",
                )
            )

    result = admit(
        context,
        selection=selection,
        facts=tuple(facts),
    )

    assert len(result.facts) == len(result.requested_facts) == 67
    assert result.status == "READY_FOR_EVALUATION"


def test_invalid_context_raises_and_produces_no_ready_result(
    context: ContractContext,
) -> None:
    selection = selection_for(
        context,
        "image_observed_serial_number",
    )
    fact = ResolvedFact(
        fact_name="image_observed_serial_number",
        status="NOT_FOUND",
        resolved_values=(),
        resolver_type="EVIDENCE_EXTRACTION",
    )
    wrong_bundle = context.bundle.model_copy(
        update={"selection_id": uuid4()}
    )
    result = None

    with pytest.raises(ResolutionContextError):
        result = create_fact_resolution_result(
            selection=selection,
            case_run=context.run,
            catalogue=context.catalogue,
            evidence_bundle=wrong_bundle,
            evidence_documents=context.documents,
            facts=(fact,),
            resolver_version="fact-contract/0.1.0",
        )

    assert result is None


def test_models_are_immutable_and_contain_no_evaluation_fields(
    context: ContractContext,
) -> None:
    provenance = visual_provenance(context.documents[1])
    value = ResolvedValue(value="sage", provenance=(provenance,))
    fact = ResolvedFact(
        fact_name="image_observed_item_colour",
        status="FOUND",
        resolved_values=(value,),
        resolver_type="EVIDENCE_EXTRACTION",
    )

    with pytest.raises(ValidationError, match="Instance is frozen"):
        value.value = "blue"
    with pytest.raises(ValidationError, match="Instance is frozen"):
        fact.status = "NOT_FOUND"

    fields = set(FactResolutionResult.model_fields)
    assert fields.isdisjoint(
        {
            "requirement_status",
            "policy_satisfied",
            "evidence_sufficient",
            "recommendation",
            "product_matches_listing",
            "fraud_conclusion",
            "liability_conclusion",
        }
    )
