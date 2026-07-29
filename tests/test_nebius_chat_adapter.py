"""Tests for Nebius-compatible Chat Completions extraction."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import stat
from types import SimpleNamespace
from uuid import uuid4

import pytest
import httpx
from openai import OpenAI

from dispute_copilot.config import ModelProviderSettings
from dispute_copilot.extraction.errors import (
    EvidenceCandidateValidationError,
    InvalidProviderResponseError,
    StructuredOutputUnsupportedError,
)
from dispute_copilot.extraction.evidence_extraction_models import (
    ExtractionCandidate,
    ExtractionResponse,
)
from dispute_copilot.extraction.models import (
    EvidenceBundle,
    EvidenceDocument,
    EvidenceFile,
    EvidencePage,
    ExtractionMethod,
)
from dispute_copilot.extraction.openai_chat_completions_adapter import (
    MAX_TOKENS,
    OpenAIChatCompletionsEvidenceExtractor,
)
from dispute_copilot.extraction.provider_response_diagnostics import (
    ProviderResponseDiagnostics,
)
from dispute_copilot.policy.fact_models import FactDefinition


class FakeCompletions:
    def __init__(
        self,
        *,
        content: str | None,
        error: Exception | None = None,
        refusal: str | None = None,
    ) -> None:
        self.content = content
        self.error = error
        self.refusal = refusal
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        message = SimpleNamespace(
            content=self.content,
            refusal=self.refusal,
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)]
        )


class FakeClient:
    def __init__(self, completions: FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


class FakeProviderRejection(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        self.body = {
            "error": {
                "type": "bad_request",
                "message": message,
            }
        }
        super().__init__(message)


@pytest.fixture
def extraction_context(
    tmp_path: Path,
) -> tuple[
    EvidenceBundle,
    tuple[EvidenceDocument, ...],
    tuple[FactDefinition, ...],
]:
    pdf_path = tmp_path / "listing.pdf"
    pdf_path.write_bytes(b"listing")
    image_path = tmp_path / "photo.png"
    image_path.write_bytes(b"image")
    pdf_file = EvidenceFile(
        file_name=pdf_path.name,
        file_path=pdf_path,
        media_type="application/pdf",
        sha256="a" * 64,
        size_bytes=pdf_path.stat().st_size,
    )
    image_file = EvidenceFile(
        file_name=image_path.name,
        file_path=image_path,
        media_type="image/png",
        sha256="b" * 64,
        size_bytes=image_path.stat().st_size,
    )
    documents = (
        EvidenceDocument(
            document_id=uuid4(),
            file_name=pdf_file.file_name,
            source_sha256=pdf_file.sha256,
            media_type=pdf_file.media_type,
            pages=(
                EvidencePage(
                    page_number=1,
                    text="Product: Carlton Armchair",
                    extraction_method=ExtractionMethod.NATIVE_TEXT,
                ),
            ),
            converter_version="test/pdf",
            created_at=datetime.now(timezone.utc),
        ),
        EvidenceDocument(
            document_id=uuid4(),
            file_name=image_file.file_name,
            source_sha256=image_file.sha256,
            media_type=image_file.media_type,
            pages=(
                EvidencePage(
                    page_number=1,
                    text="A sage armchair is visible.",
                    extraction_method=ExtractionMethod.VISION,
                    image_width=100,
                    image_height=100,
                ),
            ),
            converter_version="test/image",
            created_at=datetime.now(timezone.utc),
        ),
    )
    requested = (
        FactDefinition(
            fact_name="merchant_stated_product_description",
            description="Merchant's stated product description.",
            value_type="STRING",
            source_type="EVIDENCE_TEXT_ASSERTION",
            cardinality="SINGLE",
        ),
    )
    return (
        EvidenceBundle(
            run_id=uuid4(),
            selection_id=uuid4(),
            policy_id="visa-13.3-test",
            evidence_files=(pdf_file, image_file),
        ),
        documents,
        requested,
    )


def settings() -> ModelProviderSettings:
    return ModelProviderSettings(
        provider="nebius",
        model="Qwen/Qwen2.5-VL-72B-Instruct",
        api_key="test-only-token",
        base_url="https://api.tokenfactory.nebius.com/v1/",
    )


def test_chat_adapter_sends_nebius_supported_schema_shape(
    extraction_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
) -> None:
    bundle, documents, requested = extraction_context
    expected = ExtractionResponse(
        candidates=(
            ExtractionCandidate(
                fact_name=requested[0].fact_name,
                status="NOT_FOUND",
                values=(),
                confidence=None,
            ),
        )
    )
    completions = FakeCompletions(
        content=expected.model_dump_json()
    )
    extractor = OpenAIChatCompletionsEvidenceExtractor(
        client=FakeClient(completions),
        settings=settings(),
    )

    candidates = extractor.extract_evidence_candidates(
        requested_facts=requested,
        evidence_bundle=bundle,
        evidence_documents=documents,
    )

    assert candidates == expected.candidates
    assert len(completions.calls) == 1
    call = completions.calls[0]
    assert call["model"] == "Qwen/Qwen2.5-VL-72B-Instruct"
    assert call["max_tokens"] == MAX_TOKENS
    assert call["temperature"] == 0
    response_format = call["response_format"]
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert set(json_schema) == {"name", "strict", "schema"}
    assert "schema_" not in json_schema
    assert json_schema["schema"] == (
        ExtractionResponse.model_json_schema()
    )
    messages = call["messages"]
    assert tuple(message["role"] for message in messages) == (
        "system",
        "user",
    )
    user_content = messages[1]["content"]
    image_items = [
        item for item in user_content
        if item["type"] == "image_url"
    ]
    assert len(image_items) == 1
    assert image_items[0]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_openai_sdk_serializes_schema_without_schema_alias(
    extraction_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
) -> None:
    bundle, documents, requested = extraction_context
    expected = ExtractionResponse(
        candidates=(
            ExtractionCandidate(
                fact_name=requested[0].fact_name,
                status="NOT_FOUND",
                values=(),
                confidence=None,
            ),
        )
    )
    captured_body: dict[str, object] = {}

    def handle_request(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "Qwen/Qwen2.5-VL-72B-Instruct",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": expected.model_dump_json(),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    client = OpenAI(
        base_url="https://models.example.test/v1/",
        api_key="test-only-token",
        http_client=httpx.Client(
            transport=httpx.MockTransport(handle_request)
        ),
    )
    extractor = OpenAIChatCompletionsEvidenceExtractor(
        client=client,
        settings=settings(),
    )

    result = extractor.extract_evidence_candidates(
        requested_facts=requested,
        evidence_bundle=bundle,
        evidence_documents=documents,
    )

    assert result == expected.candidates
    response_format = captured_body["response_format"]
    json_schema = response_format["json_schema"]
    assert "schema" in json_schema
    assert "schema_" not in json_schema


def test_chat_adapter_records_exact_execution_identity() -> None:
    extractor = OpenAIChatCompletionsEvidenceExtractor(
        client=FakeClient(FakeCompletions(content=None)),
        settings=settings(),
    )

    identity = extractor.execution_identity

    assert identity.provider == "nebius"
    assert identity.model == "Qwen/Qwen2.5-VL-72B-Instruct"
    assert identity.adapter == "openai_chat_completions"
    assert identity.parameters["structured_output"] == (
        "chat_completions_json_schema"
    )
    assert identity.parameters["max_tokens"] == MAX_TOKENS


def test_chat_adapter_rejects_malformed_json(
    extraction_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
) -> None:
    bundle, documents, requested = extraction_context
    extractor = OpenAIChatCompletionsEvidenceExtractor(
        client=FakeClient(
            FakeCompletions(content="not valid JSON")
        ),
        settings=settings(),
    )

    with pytest.raises(InvalidProviderResponseError) as captured:
        extractor.extract_evidence_candidates(
            requested_facts=requested,
            evidence_bundle=bundle,
            evidence_documents=documents,
        )

    message = str(captured.value)
    assert "untrusted extraction schema" in message
    assert "response [json_invalid]" in message
    assert "not valid JSON" not in message


def test_validation_error_lists_field_path_type_and_message(
    extraction_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
) -> None:
    bundle, documents, requested = extraction_context
    raw_response = json.dumps(
        {
            "candidates": [
                {
                    "fact_name": requested[0].fact_name,
                    "status": "COMPLETE",
                    "values": [],
                    "confidence": None,
                }
            ]
        }
    )
    extractor = OpenAIChatCompletionsEvidenceExtractor(
        client=FakeClient(
            FakeCompletions(content=raw_response)
        ),
        settings=settings(),
    )

    with pytest.raises(InvalidProviderResponseError) as captured:
        extractor.extract_evidence_candidates(
            requested_facts=requested,
            evidence_bundle=bundle,
            evidence_documents=documents,
        )

    message = str(captured.value)
    assert "candidates.0.status [literal_error]" in message
    assert (
        "Input should be 'FOUND', 'NOT_FOUND', 'AMBIGUOUS' or "
        "'CONFLICTING'"
    ) in message
    assert "COMPLETE" not in message
    assert "Raw provider response captured" not in message


@pytest.mark.parametrize(
    "reference",
    [
        {
            "evidence_type": "TEXT",
            "extraction_method": "VISION",
            "supporting_text": "Caption text",
            "visual_observation": "A chair is visible.",
        },
        {
            "evidence_type": "VISUAL",
            "extraction_method": "VISION",
            "visual_observation": "A chair is visible.",
            "supporting_text": "Caption text",
        },
    ],
)
def test_channel_mixing_reports_only_primary_reference_error(
    extraction_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
    reference: dict[str, str],
) -> None:
    bundle, documents, requested = extraction_context
    image_document = documents[1]
    raw_reference = {
        **reference,
        "document_id": str(image_document.document_id),
        "file_name": image_document.file_name,
        "page_number": 1,
    }
    raw_response = json.dumps(
        {
            "candidates": [
                {
                    "fact_name": requested[0].fact_name,
                    "status": "FOUND",
                    "values": [
                        {
                            "raw_value": "chair",
                            "references": [raw_reference],
                        }
                    ],
                    "confidence": 0.8,
                }
            ]
        }
    )
    extractor = OpenAIChatCompletionsEvidenceExtractor(
        client=FakeClient(
            FakeCompletions(content=raw_response)
        ),
        settings=settings(),
    )

    with pytest.raises(InvalidProviderResponseError) as captured:
        extractor.extract_evidence_candidates(
            requested_facts=requested,
            evidence_bundle=bundle,
            evidence_documents=documents,
        )

    message = str(captured.value)
    forbidden_field = (
        "visual_observation"
        if reference["evidence_type"] == "TEXT"
        else "supporting_text"
    )
    assert forbidden_field in message
    assert "[extra_forbidden]" in message
    assert "[too_short]" not in message


def test_missing_requested_fact_is_reported_before_admission(
    extraction_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
) -> None:
    bundle, documents, requested = extraction_context
    response = ExtractionResponse(candidates=())
    extractor = OpenAIChatCompletionsEvidenceExtractor(
        client=FakeClient(
            FakeCompletions(content=response.model_dump_json())
        ),
        settings=settings(),
    )

    with pytest.raises(InvalidProviderResponseError) as captured:
        extractor.extract_evidence_candidates(
            requested_facts=requested,
            evidence_bundle=bundle,
            evidence_documents=documents,
        )

    assert (
        'facts: missing requested fact '
        f'"{requested[0].fact_name}"'
    ) in str(captured.value)


def test_explicit_debug_mode_writes_redacted_temporary_artifact(
    extraction_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
    tmp_path: Path,
) -> None:
    bundle, documents, requested = extraction_context
    provider_settings = settings()
    secret = provider_settings.api_key.get_secret_value()
    evidence_text = "private evidence-derived provider output"
    raw_response = json.dumps(
        {
            "candidates": [
                {
                    "fact_name": requested[0].fact_name,
                    "status": "COMPLETE",
                    "values": [],
                }
            ],
            "provider_debug": (
                f"MODEL_API_KEY={secret}; {evidence_text}"
            ),
        }
    )
    debug_directory = tmp_path / "diagnostics"
    extractor = OpenAIChatCompletionsEvidenceExtractor(
        client=FakeClient(
            FakeCompletions(content=raw_response)
        ),
        settings=provider_settings,
        diagnostics=ProviderResponseDiagnostics(
            capture_raw_response=True,
            temporary_directory=debug_directory,
        ),
    )

    with pytest.raises(InvalidProviderResponseError) as captured:
        extractor.extract_evidence_candidates(
            requested_facts=requested,
            evidence_bundle=bundle,
            evidence_documents=documents,
        )

    message = str(captured.value)
    artifacts = tuple(debug_directory.iterdir())
    assert len(artifacts) == 1
    artifact = artifacts[0]
    artifact_text = artifact.read_text()
    assert str(artifact) in message
    assert evidence_text not in message
    assert secret not in message
    assert secret not in artifact_text
    assert "[REDACTED]" in artifact_text
    assert evidence_text in artifact_text
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600


def test_normal_mode_creates_no_raw_response_artifact(
    extraction_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
    tmp_path: Path,
) -> None:
    bundle, documents, requested = extraction_context
    debug_directory = tmp_path / "diagnostics"
    extractor = OpenAIChatCompletionsEvidenceExtractor(
        client=FakeClient(
            FakeCompletions(content="not valid JSON")
        ),
        settings=settings(),
        diagnostics=ProviderResponseDiagnostics(
            capture_raw_response=False,
            temporary_directory=debug_directory,
        ),
    )

    with pytest.raises(InvalidProviderResponseError):
        extractor.extract_evidence_candidates(
            requested_facts=requested,
            evidence_bundle=bundle,
            evidence_documents=documents,
        )

    assert not debug_directory.exists()


def test_chat_adapter_fails_closed_when_schema_is_unsupported(
    extraction_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
) -> None:
    bundle, documents, requested = extraction_context
    completions = FakeCompletions(
        content=None,
        error=FakeProviderRejection(
            "response_format json_schema is unsupported"
        ),
    )
    extractor = OpenAIChatCompletionsEvidenceExtractor(
        client=FakeClient(completions),
        settings=settings(),
    )

    with pytest.raises(
        StructuredOutputUnsupportedError,
        match="no provider/model substitution",
    ) as captured:
        extractor.extract_evidence_candidates(
            requested_facts=requested,
            evidence_bundle=bundle,
            evidence_documents=documents,
        )

    assert "openai_chat_completions" in str(captured.value)
    assert len(completions.calls) == 1


def test_chat_adapter_never_receives_metadata_facts(
    extraction_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
) -> None:
    bundle, documents, _ = extraction_context
    completions = FakeCompletions(content=None)
    extractor = OpenAIChatCompletionsEvidenceExtractor(
        client=FakeClient(completions),
        settings=settings(),
    )

    with pytest.raises(
        EvidenceCandidateValidationError,
        match="metadata facts",
    ):
        extractor.extract_evidence_candidates(
            requested_facts=(
                FactDefinition(
                    fact_name="avs_result",
                    description="AVS result.",
                    value_type="STRING",
                    source_type="CASE_METADATA",
                    cardinality="SINGLE",
                ),
            ),
            evidence_bundle=bundle,
            evidence_documents=documents,
        )

    assert completions.calls == []
