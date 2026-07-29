"""Focused tests for untrusted extraction and deterministic adaptation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from dispute_copilot.config import ModelProviderSettings
from dispute_copilot.extraction.errors import (
    EvidenceCandidateProvenanceError,
    EvidenceCandidateValidationError,
    EvidenceCandidateValueError,
    InvalidProviderResponseError,
    ProviderEndpointError,
    ProviderRejectionError,
    StructuredOutputUnsupportedError,
)
from dispute_copilot.extraction.evidence_candidate_adapter import (
    to_resolved_facts,
)
from dispute_copilot.extraction.evidence_extraction_models import (
    ExtractionCandidate,
    ExtractionCandidateValue,
    ExtractionEvidenceReference,
    ExtractionResponse,
    TextExtractionEvidenceReference,
    VisualExtractionEvidenceReference,
)
from dispute_copilot.extraction.openai_responses_adapter import (
    EXTRACTION_INSTRUCTIONS,
    OpenAIResponsesEvidenceExtractor,
    OpenAIResponsesVisionProvider,
)
from dispute_copilot.extraction.fact_resolution_models import (
    EvidenceTextProvenance,
    VisualObservationProvenance,
)
from dispute_copilot.extraction.models import (
    EvidenceBundle,
    EvidenceDocument,
    EvidenceFile,
    EvidencePage,
    ExtractionMethod,
)
from dispute_copilot.policy.fact_models import FactDefinition


class FakeResponses:
    def __init__(
        self,
        *,
        parsed: ExtractionResponse | None = None,
        output_text: str = "A sage armchair is visible.",
        error: Exception | None = None,
    ) -> None:
        self.parsed = parsed
        self.output_text = output_text
        self.error = error
        self.parse_calls: list[dict[str, object]] = []
        self.create_calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> SimpleNamespace:
        self.parse_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(output_parsed=self.parsed)

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.create_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(output_text=self.output_text)


class FakeClient:
    def __init__(self, responses: FakeResponses) -> None:
        self.responses = responses


def provider_settings(
    *,
    model: str = "user-selected-nebius-model",
) -> ModelProviderSettings:
    return ModelProviderSettings(
        provider="nebius",
        api_key="test-only-nebius-token",
        model=model,
        base_url="https://api.tokenfactory.nebius.com/v1/",
    )


@pytest.fixture
def documents(
    tmp_path: Path,
) -> tuple[EvidenceBundle, tuple[EvidenceDocument, ...]]:
    text_path = tmp_path / "listing.pdf"
    text_path.write_bytes(b"listing")
    image_path = tmp_path / "photo.png"
    image_path.write_bytes(b"image")

    text_file = EvidenceFile(
        file_name=text_path.name,
        file_path=text_path,
        media_type="application/pdf",
        sha256="a" * 64,
        size_bytes=text_path.stat().st_size,
    )
    image_file = EvidenceFile(
        file_name=image_path.name,
        file_path=image_path,
        media_type="image/png",
        sha256="b" * 64,
        size_bytes=image_path.stat().st_size,
    )
    text_document = EvidenceDocument(
        document_id=uuid4(),
        file_name=text_path.name,
        source_sha256=text_file.sha256,
        media_type=text_file.media_type,
        pages=(
            EvidencePage(
                page_number=1,
                text="Product: Carlton Armchair",
                extraction_method=ExtractionMethod.NATIVE_TEXT,
            ),
        ),
        converter_version="test/text",
        created_at=datetime.now(timezone.utc),
    )
    image_document = EvidenceDocument(
        document_id=uuid4(),
        file_name=image_path.name,
        source_sha256=image_file.sha256,
        media_type=image_file.media_type,
        pages=(
            EvidencePage(
                page_number=1,
                text=(
                    "HavenHome — Delivery Photo\n"
                    "Carlton Armchair (Sage) — delivered 18 Apr 2025\n"
                    "Photo taken by driver at delivery (front view only)\n"
                    "A sage armchair is visible."
                ),
                extraction_method=ExtractionMethod.VISION,
                image_width=100,
                image_height=100,
            ),
        ),
        converter_version="test/vision",
        created_at=datetime.now(timezone.utc),
    )
    return (
        EvidenceBundle(
            run_id=uuid4(),
            selection_id=uuid4(),
            policy_id="visa-13.3-test",
            evidence_files=(text_file, image_file),
        ),
        (text_document, image_document),
    )


def definition(
    fact_name: str,
    *,
    value_type: str = "STRING",
    source_type: str = "EVIDENCE_TEXT_ASSERTION",
    cardinality: str = "SINGLE",
) -> FactDefinition:
    return FactDefinition(
        fact_name=fact_name,
        description=f"Definition for {fact_name}.",
        value_type=value_type,
        source_type=source_type,
        cardinality=cardinality,
    )


def reference(
    document: EvidenceDocument,
    *,
    source_type: str = "EVIDENCE_TEXT_ASSERTION",
    page_number: int = 1,
    supporting_text: str | None = "Product: Carlton Armchair",
    observation: str | None = None,
) -> ExtractionEvidenceReference:
    if source_type == "EVIDENCE_VISUAL_OBSERVATION":
        assert observation is not None
        return VisualExtractionEvidenceReference(
            evidence_type="VISUAL",
            document_id=document.document_id,
            file_name=document.file_name,
            page_number=page_number,
            extraction_method="VISION",
            visual_observation=observation,
        )
    assert supporting_text is not None
    return TextExtractionEvidenceReference(
        evidence_type="TEXT",
        document_id=document.document_id,
        file_name=document.file_name,
        page_number=page_number,
        extraction_method="NATIVE_TEXT",
        supporting_text=supporting_text,
    )


def candidate(
    fact_name: str,
    *,
    raw_value: object | None = "Carlton Armchair",
    evidence_reference: ExtractionEvidenceReference | None = None,
    status: str = "FOUND",
) -> ExtractionCandidate:
    values = ()
    if raw_value is not None:
        assert evidence_reference is not None
        values = (
            ExtractionCandidateValue(
                raw_value=raw_value,
                references=(evidence_reference,),
            ),
        )
    return ExtractionCandidate(
        fact_name=fact_name,
        status=status,
        values=values,
        confidence=0.9 if status != "NOT_FOUND" else None,
    )


def test_provider_receives_only_requested_evidence_facts_and_image(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
) -> None:
    bundle, evidence_documents = documents
    requested = (
        definition("merchant_stated_product_description"),
    )
    response = ExtractionResponse(
        candidates=(
            candidate(
                requested[0].fact_name,
                evidence_reference=reference(evidence_documents[0]),
            ),
        )
    )
    responses = FakeResponses(parsed=response)
    settings = provider_settings()
    extractor = OpenAIResponsesEvidenceExtractor(
        client=FakeClient(responses),
        settings=settings,
    )

    result = extractor.extract_evidence_candidates(
        requested_facts=requested,
        evidence_bundle=bundle,
        evidence_documents=evidence_documents,
    )

    assert result == response.candidates
    assert len(responses.parse_calls) == 1
    call = responses.parse_calls[0]
    assert call["text_format"] is ExtractionResponse
    assert call["model"] == settings.model
    content = call["input"][0]["content"]
    assert any(item["type"] == "input_image" for item in content)
    prompt = content[0]["text"]
    assert "merchant_stated_product_description" in prompt
    assert '"canonical_text":' in prompt
    assert '"text":' not in prompt


def test_metadata_fact_is_never_sent_to_model(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
) -> None:
    bundle, evidence_documents = documents
    responses = FakeResponses(parsed=ExtractionResponse(candidates=()))
    extractor = OpenAIResponsesEvidenceExtractor(
        client=FakeClient(responses),
        settings=provider_settings(),
    )

    with pytest.raises(
        EvidenceCandidateValidationError,
        match="metadata facts",
    ):
        extractor.extract_evidence_candidates(
            requested_facts=(
                definition(
                    "avs_result",
                    source_type="CASE_METADATA",
                ),
            ),
            evidence_bundle=bundle,
            evidence_documents=evidence_documents,
        )

    assert responses.parse_calls == []


def test_model_failure_produces_typed_error(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
) -> None:
    bundle, evidence_documents = documents
    extractor = OpenAIResponsesEvidenceExtractor(
        client=FakeClient(
            FakeResponses(error=RuntimeError("provider failed"))
        ),
        settings=provider_settings(),
    )

    with pytest.raises(ProviderRejectionError):
        extractor.extract_evidence_candidates(
            requested_facts=(
                definition("merchant_stated_product_description"),
            ),
            evidence_bundle=bundle,
            evidence_documents=evidence_documents,
        )


class FakeProviderRejection(Exception):
    status_code = 400

    def __init__(self, message: str, error_type: str) -> None:
        self.body = {
            "error": {
                "type": error_type,
                "message": message,
            }
        }
        super().__init__(message)


def test_provider_rejection_preserves_safe_details_without_substitution(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
) -> None:
    bundle, evidence_documents = documents
    settings = provider_settings()
    secret = settings.api_key.get_secret_value()
    responses = FakeResponses(
        error=FakeProviderRejection(
            f"model unavailable; MODEL_API_KEY={secret}",
            "invalid_request_error",
        )
    )
    extractor = OpenAIResponsesEvidenceExtractor(
        client=FakeClient(responses),
        settings=settings,
    )

    with pytest.raises(
        ProviderRejectionError,
    ) as captured:
        extractor.extract_evidence_candidates(
            requested_facts=(
                definition("merchant_stated_product_description"),
            ),
            evidence_bundle=bundle,
            evidence_documents=evidence_documents,
        )

    message = str(captured.value)
    assert secret not in message
    assert "[REDACTED]" in message
    assert settings.model in message
    assert len(responses.parse_calls) == 1
    assert responses.parse_calls[0]["model"] == settings.model


def test_structured_output_rejection_has_no_fallback(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
) -> None:
    bundle, evidence_documents = documents
    settings = provider_settings()
    responses = FakeResponses(
        error=FakeProviderRejection(
            "response_format json_schema is unsupported",
            "unsupported_parameter",
        )
    )
    extractor = OpenAIResponsesEvidenceExtractor(
        client=FakeClient(responses),
        settings=settings,
    )

    with pytest.raises(
        StructuredOutputUnsupportedError,
        match="no provider/model substitution",
    ):
        extractor.extract_evidence_candidates(
            requested_facts=(
                definition("merchant_stated_product_description"),
            ),
            evidence_bundle=bundle,
            evidence_documents=evidence_documents,
        )

    assert len(responses.parse_calls) == 1


def test_unreachable_provider_endpoint_is_secret_safe(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
) -> None:
    bundle, evidence_documents = documents
    settings = provider_settings()

    class APIConnectionError(Exception):
        pass

    responses = FakeResponses(
        error=APIConnectionError("connection failed")
    )
    extractor = OpenAIResponsesEvidenceExtractor(
        client=FakeClient(responses),
        settings=settings,
    )

    with pytest.raises(ProviderEndpointError) as captured:
        extractor.extract_evidence_candidates(
            requested_facts=(
                definition("merchant_stated_product_description"),
            ),
            evidence_bundle=bundle,
            evidence_documents=evidence_documents,
        )

    message = str(captured.value)
    assert str(settings.base_url) in message
    assert settings.api_key.get_secret_value() not in message


def test_malformed_provider_response_fails_closed(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
) -> None:
    bundle, evidence_documents = documents
    responses = FakeResponses()
    responses.parsed = {
        "candidates": [
            {
                "fact_name": "merchant_stated_product_description",
                "status": "FOUND",
                "values": [],
                "confidence": 0.9,
            }
        ]
    }
    extractor = OpenAIResponsesEvidenceExtractor(
        client=FakeClient(responses),
        settings=provider_settings(),
    )

    with pytest.raises(
        InvalidProviderResponseError,
        match="response was rejected",
    ):
        extractor.extract_evidence_candidates(
            requested_facts=(
                definition("merchant_stated_product_description"),
            ),
            evidence_bundle=bundle,
            evidence_documents=evidence_documents,
        )


def test_same_model_client_can_describe_image(tmp_path: Path) -> None:
    image_path = tmp_path / "photo.png"
    image_path.write_bytes(b"image")
    responses = FakeResponses(output_text="A sage armchair is visible.")
    provider = OpenAIResponsesVisionProvider(
        client=FakeClient(responses),
        settings=provider_settings(),
    )

    assert provider.describe_image(file_path=image_path) == (
        "A sage armchair is visible."
    )
    assert len(responses.create_calls) == 1
    identity = json.loads(provider.provider_version)
    assert identity["provider"] == "nebius"
    assert identity["model"] == "user-selected-nebius-model"
    assert identity["adapter"] == "openai_responses"


def test_untrusted_schema_has_no_evaluation_fields() -> None:
    schema_text = str(ExtractionResponse.model_json_schema()).lower()

    assert "requirement_result" not in schema_text
    assert "recommendation" not in schema_text
    assert "ready_for_evaluation" not in schema_text
    assert "policy_satisfaction" not in schema_text
    assert "do not decide" in EXTRACTION_INSTRUCTIONS.lower()
    assert "captions" in EXTRACTION_INSTRUCTIONS.lower()
    assert "visible pixels" in EXTRACTION_INSTRUCTIONS.lower()
    assert "exact ordered product" in EXTRACTION_INSTRUCTIONS.lower()
    assert "defect-free" in EXTRACTION_INSTRUCTIONS.lower()
    assert "evidence_type" in EXTRACTION_INSTRUCTIONS
    assert '"evidence_type": "TEXT"' in EXTRACTION_INSTRUCTIONS
    assert '"evidence_type": "VISUAL"' in EXTRACTION_INSTRUCTIONS
    normalized_instructions = " ".join(
        EXTRACTION_INSTRUCTIONS.split()
    )
    assert "two separate references" in normalized_instructions
    assert "copied exclusively and verbatim" in normalized_instructions
    assert "do not independently transcribe" in (
        normalized_instructions.lower()
    )
    assert "absent from canonical_text" in normalized_instructions


def test_provider_schema_discriminates_text_and_visual_references() -> None:
    schema = ExtractionResponse.model_json_schema()
    reference_schema = (
        schema["$defs"]["ExtractionCandidateValue"]["properties"][
            "references"
        ]["items"]
    )

    assert reference_schema["discriminator"]["propertyName"] == (
        "evidence_type"
    )
    assert set(reference_schema["discriminator"]["mapping"]) == {
        "TEXT",
        "VISUAL",
    }
    assert len(reference_schema["oneOf"]) == 2
    text_schema = schema["$defs"]["TextExtractionEvidenceReference"]
    supporting_text_description = text_schema["properties"][
        "supporting_text"
    ]["description"]
    assert "copied exclusively" in supporting_text_description
    assert "canonical_text" in supporting_text_description
    assert "independently transcribed" in supporting_text_description


def test_text_printed_inside_png_remains_text_provenance(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
) -> None:
    _, evidence_documents = documents
    image_document = evidence_documents[1]
    requested = (
        definition("merchant_stated_product_description"),
    )
    text_from_image = TextExtractionEvidenceReference(
        evidence_type="TEXT",
        document_id=image_document.document_id,
        file_name=image_document.file_name,
        page_number=1,
        extraction_method="VISION",
        supporting_text=(
            "Carlton Armchair (Sage) — delivered 18 Apr 2025"
        ),
    )

    resolved = to_resolved_facts(
        provider_candidates=(
            candidate(
                requested[0].fact_name,
                raw_value="Carlton Armchair (Sage)",
                evidence_reference=text_from_image,
            ),
        ),
        requested_facts=requested,
        evidence_documents=evidence_documents,
    )

    provenance = resolved[0].resolved_values[0].provenance[0]
    assert isinstance(provenance, EvidenceTextProvenance)
    assert provenance.extraction_method == "VISION"
    assert provenance.supporting_text == (
        "Carlton Armchair (Sage) — delivered 18 Apr 2025"
    )


def test_citation_whitespace_and_unicode_normalization_is_shared(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
) -> None:
    _, evidence_documents = documents
    requested = (
        definition("merchant_stated_product_description"),
    )
    normalized_reference = TextExtractionEvidenceReference(
        evidence_type="TEXT",
        document_id=evidence_documents[0].document_id,
        file_name=evidence_documents[0].file_name,
        page_number=1,
        extraction_method="NATIVE_TEXT",
        supporting_text="Product:\nＣarlton\tArmchair",
    )

    resolved = to_resolved_facts(
        provider_candidates=(
            candidate(
                requested[0].fact_name,
                evidence_reference=normalized_reference,
            ),
        ),
        requested_facts=requested,
        evidence_documents=evidence_documents,
    )

    assert resolved[0].resolved_values[0].provenance[
        0
    ].supporting_text == "Product:\nＣarlton\tArmchair"


def test_citation_failure_reports_precise_safe_context(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
) -> None:
    _, evidence_documents = documents
    document = evidence_documents[0]
    requested = (
        definition("merchant_stated_product_description"),
    )
    rejected_text = "A paraphrased product description"
    bad_reference = TextExtractionEvidenceReference(
        evidence_type="TEXT",
        document_id=document.document_id,
        file_name=document.file_name,
        page_number=1,
        extraction_method="NATIVE_TEXT",
        supporting_text=rejected_text,
    )

    with pytest.raises(EvidenceCandidateProvenanceError) as captured:
        to_resolved_facts(
            provider_candidates=(
                candidate(
                    requested[0].fact_name,
                    evidence_reference=bad_reference,
                ),
            ),
            requested_facts=requested,
            evidence_documents=evidence_documents,
        )

    diagnostic = str(captured.value)
    assert "candidates.0.values.0.references.0" in diagnostic
    assert f"document={document.file_name!r}" in diagnostic
    assert f"document_id={str(document.document_id)!r}" in diagnostic
    assert "page=1" in diagnostic
    assert f"rejected_supporting_text={rejected_text!r}" in diagnostic
    assert "canonical_page_text_empty=False" in diagnostic
    assert "Product: Carlton Armchair" not in diagnostic


def test_untrusted_schema_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ExtractionCandidate.model_validate(
            {
                "fact_name": "merchant_stated_product_description",
                "status": "NOT_FOUND",
                "values": [],
                "confidence": None,
                "recommendation": "REPRESENT",
            }
        )


def test_text_candidate_adapts_with_exact_provenance(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
) -> None:
    _, evidence_documents = documents
    requested = (
        definition("merchant_stated_product_description"),
    )

    resolved = to_resolved_facts(
        provider_candidates=(
            candidate(
                requested[0].fact_name,
                evidence_reference=reference(evidence_documents[0]),
            ),
        ),
        requested_facts=requested,
        evidence_documents=evidence_documents,
    )

    provenance = resolved[0].resolved_values[0].provenance[0]
    assert isinstance(provenance, EvidenceTextProvenance)
    assert provenance.supporting_text == "Product: Carlton Armchair"


def test_visual_candidate_adapts_as_observation(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
) -> None:
    _, evidence_documents = documents
    requested = (
        definition(
            "image_observed_item_colour",
            source_type="EVIDENCE_VISUAL_OBSERVATION",
            cardinality="MULTIPLE",
        ),
    )
    visual_reference = reference(
        evidence_documents[1],
        source_type="EVIDENCE_VISUAL_OBSERVATION",
        supporting_text=None,
        observation="A sage armchair is visible.",
    )

    resolved = to_resolved_facts(
        provider_candidates=(
            candidate(
                requested[0].fact_name,
                raw_value="sage",
                evidence_reference=visual_reference,
            ),
        ),
        requested_facts=requested,
        evidence_documents=evidence_documents,
    )

    provenance = resolved[0].resolved_values[0].provenance[0]
    assert isinstance(provenance, VisualObservationProvenance)
    assert provenance.observation == "A sage armchair is visible."


@pytest.mark.parametrize(
    ("raw_value", "value_type"),
    [
        ("14", "INTEGER"),
        (14, "DATE"),
        ("2025-04-18T00:00:00", "DATETIME"),
        ("true", "BOOLEAN"),
    ],
)
def test_incorrect_or_coercive_types_fail_closed(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
    raw_value: object,
    value_type: str,
) -> None:
    _, evidence_documents = documents
    requested = (
        definition(
            "merchant_stated_return_window_days",
            value_type=value_type,
        ),
    )

    with pytest.raises(EvidenceCandidateValueError):
        to_resolved_facts(
            provider_candidates=(
                candidate(
                    requested[0].fact_name,
                    raw_value=raw_value,
                    evidence_reference=reference(evidence_documents[0]),
                ),
            ),
            requested_facts=requested,
            evidence_documents=evidence_documents,
        )


def test_missing_unrequested_and_duplicate_facts_are_rejected(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
) -> None:
    _, evidence_documents = documents
    requested = (
        definition("merchant_stated_product_description"),
    )
    requested_candidate = candidate(
        requested[0].fact_name,
        evidence_reference=reference(evidence_documents[0]),
    )

    with pytest.raises(EvidenceCandidateValidationError, match="missing"):
        to_resolved_facts(
            provider_candidates=(),
            requested_facts=requested,
            evidence_documents=evidence_documents,
        )
    with pytest.raises(
        EvidenceCandidateValidationError,
        match="unrequested",
    ):
        to_resolved_facts(
            provider_candidates=(
                requested_candidate,
                requested_candidate.model_copy(
                    update={"fact_name": "merchant_stated_extra"}
                ),
            ),
            requested_facts=requested,
            evidence_documents=evidence_documents,
        )
    with pytest.raises(
        EvidenceCandidateValidationError,
        match="duplicate fact",
    ):
        to_resolved_facts(
            provider_candidates=(
                requested_candidate,
                requested_candidate,
            ),
            requested_facts=requested,
            evidence_documents=evidence_documents,
        )


def test_duplicate_values_are_rejected_not_treated_as_conflict(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
) -> None:
    _, evidence_documents = documents
    requested = (
        definition(
            "merchant_stated_return_method",
            cardinality="MULTIPLE",
        ),
    )
    value = ExtractionCandidateValue(
        raw_value="email",
        references=(reference(evidence_documents[0]),),
    )
    duplicate_candidate = ExtractionCandidate(
        fact_name=requested[0].fact_name,
        status="CONFLICTING",
        values=(value, value),
        confidence=0.5,
    )

    with pytest.raises(EvidenceCandidateValueError, match="duplicate value"):
        to_resolved_facts(
            provider_candidates=(duplicate_candidate,),
            requested_facts=requested,
            evidence_documents=evidence_documents,
        )


def test_invalid_document_or_page_provenance_is_rejected(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
) -> None:
    _, evidence_documents = documents
    requested = (
        definition("merchant_stated_product_description"),
    )
    bad_reference = reference(
        evidence_documents[0],
        page_number=2,
    )

    with pytest.raises(
        EvidenceCandidateProvenanceError,
        match="does not exist",
    ):
        to_resolved_facts(
            provider_candidates=(
                candidate(
                    requested[0].fact_name,
                    evidence_reference=bad_reference,
                ),
            ),
            requested_facts=requested,
            evidence_documents=evidence_documents,
        )


def test_unknown_document_provenance_is_rejected(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
) -> None:
    _, evidence_documents = documents
    requested = (
        definition("merchant_stated_product_description"),
    )
    bad_reference = TextExtractionEvidenceReference(
        evidence_type="TEXT",
        document_id=uuid4(),
        file_name=evidence_documents[0].file_name,
        page_number=1,
        extraction_method="NATIVE_TEXT",
        supporting_text="Product: Carlton Armchair",
    )

    with pytest.raises(
        EvidenceCandidateProvenanceError,
        match="unknown evidence document",
    ):
        to_resolved_facts(
            provider_candidates=(
                candidate(
                    requested[0].fact_name,
                    evidence_reference=bad_reference,
                ),
            ),
            requested_facts=requested,
            evidence_documents=evidence_documents,
        )


@pytest.mark.parametrize(
    ("status", "raw_values"),
    [
        ("NOT_FOUND", ()),
        ("AMBIGUOUS", ("email",)),
        ("CONFLICTING", ("email", "web form")),
    ],
)
def test_non_found_statuses_survive_adaptation(
    documents: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
    ],
    status: str,
    raw_values: tuple[str, ...],
) -> None:
    _, evidence_documents = documents
    requested = (
        definition(
            "merchant_stated_return_method",
            cardinality="MULTIPLE",
        ),
    )
    evidence_reference = reference(evidence_documents[0])
    provider_candidate = ExtractionCandidate(
        fact_name=requested[0].fact_name,
        status=status,
        values=tuple(
            ExtractionCandidateValue(
                raw_value=raw_value,
                references=(evidence_reference,),
            )
            for raw_value in raw_values
        ),
        confidence=None if status == "NOT_FOUND" else 0.5,
    )

    result = to_resolved_facts(
        provider_candidates=(provider_candidate,),
        requested_facts=requested,
        evidence_documents=evidence_documents,
    )

    assert result[0].status == status
    assert tuple(
        resolved.value
        for resolved in result[0].resolved_values
    ) == raw_values
