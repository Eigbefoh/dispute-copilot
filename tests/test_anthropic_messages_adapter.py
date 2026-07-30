"""Contract tests for the native Anthropic Messages adapter."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import httpx
from anthropic import Anthropic

from dispute_copilot.config import ModelProviderSettings
from dispute_copilot.extraction.anthropic_messages_adapter import (
    ADAPTER_NAME,
    MAX_TOKENS,
    AnthropicMessagesEvidenceExtractor,
    AnthropicMessagesVisionProvider,
    create_anthropic_messages_client,
)
from dispute_copilot.extraction.errors import (
    EvidenceCandidateValidationError,
    InvalidProviderResponseError,
    ProviderRejectionError,
    StructuredOutputUnsupportedError,
)
from dispute_copilot.extraction.evidence_extraction_models import (
    ExtractionCandidate,
    ExtractionResponse,
    extraction_response_model_for,
)
from dispute_copilot.extraction.models import (
    EvidenceBundle,
    EvidenceDocument,
    EvidenceFile,
    EvidencePage,
    ExtractionMethod,
)
from dispute_copilot.extraction.provider_response_diagnostics import (
    ProviderResponseDiagnostics,
)
from dispute_copilot.extraction.vision_models import (
    VisionObservationResult,
    render_vision_observations,
)
from dispute_copilot.policy.fact_models import FactDefinition


class FakeMessages:
    def __init__(
        self,
        *,
        text: str | None = None,
        stop_reason: str = "end_turn",
        blocks: list[object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.text = text
        self.stop_reason = stop_reason
        self.blocks = blocks
        self.error = error
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        blocks = self.blocks
        if blocks is None:
            blocks = [
                SimpleNamespace(type="text", text=self.text)
            ]
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=blocks,
        )


class FakeClient:
    def __init__(self, messages: FakeMessages) -> None:
        self.messages = messages


class FakeProviderRejection(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        self.body = {
            "error": {
                "type": "invalid_request_error",
                "message": message,
            }
        }
        super().__init__(message)


def settings() -> ModelProviderSettings:
    return ModelProviderSettings(
        provider="anthropic",
        model="claude-test-model",
        api_key="test-only-anthropic-token",
        base_url="https://api.anthropic.com/",
    )


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
            case_transaction_id="txn-test",
            evidence_files=(pdf_file, image_file),
        ),
        documents,
        requested,
    )


def test_native_messages_request_uses_exact_schema_and_base64_images(
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
    messages = FakeMessages(text=expected.model_dump_json())
    extractor = AnthropicMessagesEvidenceExtractor(
        client=FakeClient(messages),
        settings=settings(),
    )

    result = extractor.extract_evidence_candidates(
        requested_facts=requested,
        evidence_bundle=bundle,
        evidence_documents=documents,
    )

    assert result == expected.candidates
    assert len(messages.calls) == 1
    call = messages.calls[0]
    assert call["model"] == "claude-test-model"
    assert call["max_tokens"] == MAX_TOKENS
    assert call["temperature"] == 0
    assert "do not independently transcribe" in str(
        call["system"]
    ).lower()
    assert call["output_config"] == {
        "format": {
            "type": "json_schema",
            "schema": extraction_response_model_for(
                requested
            ).model_json_schema(),
        }
    }
    content = call["messages"][0]["content"]
    image_blocks = [
        block for block in content if block["type"] == "image"
    ]
    assert len(image_blocks) == 1
    source = image_blocks[0]["source"]
    assert source["type"] == "base64"
    assert source["media_type"] == "image/png"
    assert base64.b64decode(source["data"]) == b"image"


def test_anthropic_sdk_serializes_output_config_without_transforming_schema(
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
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-test-model",
                "content": [
                    {
                        "type": "text",
                        "text": expected.model_dump_json(),
                    }
                ],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                },
            },
        )

    client = Anthropic(
        base_url="https://api.anthropic.com/",
        api_key="test-only-anthropic-token",
        http_client=httpx.Client(
            transport=httpx.MockTransport(handle_request)
        ),
    )
    extractor = AnthropicMessagesEvidenceExtractor(
        client=client,
        settings=settings(),
    )

    result = extractor.extract_evidence_candidates(
        requested_facts=requested,
        evidence_bundle=bundle,
        evidence_documents=documents,
    )

    assert result == expected.candidates
    assert captured_body["output_config"] == {
        "format": {
            "type": "json_schema",
            "schema": extraction_response_model_for(
                requested
            ).model_json_schema(),
        }
    }


def test_native_messages_identity_is_complete_and_secret_safe() -> None:
    extractor = AnthropicMessagesEvidenceExtractor(
        client=FakeClient(FakeMessages(text="")),
        settings=settings(),
    )

    identity = extractor.execution_identity

    assert identity.provider == "anthropic"
    assert identity.model == "claude-test-model"
    assert identity.adapter == ADAPTER_NAME
    assert identity.parameters["image_transport"] == "base64"
    assert identity.parameters["max_tokens"] == MAX_TOKENS
    assert settings().api_key.get_secret_value() not in (
        identity.canonical_json()
    )


def test_metadata_facts_are_rejected_before_messages_call(
    extraction_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
) -> None:
    bundle, documents, _ = extraction_context
    messages = FakeMessages(text="")
    extractor = AnthropicMessagesEvidenceExtractor(
        client=FakeClient(messages),
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

    assert messages.calls == []


def test_invalid_response_reports_safe_field_diagnostics(
    extraction_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
) -> None:
    bundle, documents, requested = extraction_context
    raw = json.dumps(
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
    extractor = AnthropicMessagesEvidenceExtractor(
        client=FakeClient(FakeMessages(text=raw)),
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
    assert "COMPLETE" not in message
    assert settings().api_key.get_secret_value() not in message


def test_missing_requested_fact_fails_complete_response(
    extraction_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
) -> None:
    bundle, documents, requested = extraction_context
    extractor = AnthropicMessagesEvidenceExtractor(
        client=FakeClient(
            FakeMessages(
                text=ExtractionResponse(
                    candidates=()
                ).model_dump_json()
            )
        ),
        settings=settings(),
    )

    with pytest.raises(
        InvalidProviderResponseError,
        match="missing requested fact",
    ):
        extractor.extract_evidence_candidates(
            requested_facts=requested,
            evidence_bundle=bundle,
            evidence_documents=documents,
        )


@pytest.mark.parametrize(
    ("messages", "error_type"),
    [
        (
            FakeMessages(text="{}", stop_reason="refusal"),
            ProviderRejectionError,
        ),
        (
            FakeMessages(text="{}", stop_reason="max_tokens"),
            InvalidProviderResponseError,
        ),
        (
            FakeMessages(
                blocks=[SimpleNamespace(type="tool_use")]
            ),
            InvalidProviderResponseError,
        ),
        (
            FakeMessages(
                blocks=[
                    SimpleNamespace(type="text", text="{}"),
                    SimpleNamespace(type="text", text="{}"),
                ]
            ),
            InvalidProviderResponseError,
        ),
    ],
)
def test_refusal_truncation_and_noncanonical_content_fail_closed(
    extraction_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
    messages: FakeMessages,
    error_type: type[Exception],
) -> None:
    bundle, documents, requested = extraction_context
    extractor = AnthropicMessagesEvidenceExtractor(
        client=FakeClient(messages),
        settings=settings(),
    )

    with pytest.raises(error_type):
        extractor.extract_evidence_candidates(
            requested_facts=requested,
            evidence_bundle=bundle,
            evidence_documents=documents,
        )


def test_schema_incompatibility_does_not_fallback(
    extraction_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
) -> None:
    bundle, documents, requested = extraction_context
    messages = FakeMessages(
        error=FakeProviderRejection(
            "output_config json_schema is not supported"
        )
    )
    extractor = AnthropicMessagesEvidenceExtractor(
        client=FakeClient(messages),
        settings=settings(),
    )

    with pytest.raises(StructuredOutputUnsupportedError):
        extractor.extract_evidence_candidates(
            requested_facts=requested,
            evidence_bundle=bundle,
            evidence_documents=documents,
        )

    assert len(messages.calls) == 1


def test_debug_response_capture_is_explicit_and_secret_safe(
    extraction_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
    tmp_path: Path,
) -> None:
    bundle, documents, requested = extraction_context
    secret = settings().api_key.get_secret_value()
    raw = json.dumps(
        {
            "candidates": [
                {
                    "fact_name": requested[0].fact_name,
                    "status": "COMPLETE",
                    "values": [],
                    "confidence": None,
                    "credential": secret,
                }
            ]
        }
    )
    extractor = AnthropicMessagesEvidenceExtractor(
        client=FakeClient(FakeMessages(text=raw)),
        settings=settings(),
        diagnostics=ProviderResponseDiagnostics(
            capture_raw_response=True,
            temporary_directory=tmp_path,
        ),
    )

    with pytest.raises(InvalidProviderResponseError) as captured:
        extractor.extract_evidence_candidates(
            requested_facts=requested,
            evidence_bundle=bundle,
            evidence_documents=documents,
        )

    message = str(captured.value)
    assert "Raw provider response captured" in message
    assert secret not in message
    artifacts = tuple(tmp_path.glob("*.json"))
    assert len(artifacts) == 1
    assert secret not in artifacts[0].read_text()


def test_native_vision_uses_shared_structured_contract(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "photo.png"
    image_path.write_bytes(b"image")
    observations = VisionObservationResult(
        written_text_observations=("Delivered 18 Apr 2025",),
        visible_pixel_observations=(
            "A front-facing sage armchair is visible.",
        ),
        limitations=("Only the front view is visible.",),
    )
    messages = FakeMessages(text=observations.model_dump_json())
    provider = AnthropicMessagesVisionProvider(
        client=FakeClient(messages),
        settings=settings(),
    )

    result = provider.describe_image(file_path=image_path)

    assert result == render_vision_observations(observations)
    call = messages.calls[0]
    assert call["output_config"] == {
        "format": {
            "type": "json_schema",
            "schema": VisionObservationResult.model_json_schema(),
        }
    }
    assert call["messages"][0]["content"][0]["type"] == "image"
    assert "defect-free" in str(call["system"])


def test_client_factory_receives_only_endpoint_and_key() -> None:
    received: dict[str, str] = {}

    def factory(**kwargs: str) -> object:
        received.update(kwargs)
        return object()

    result = create_anthropic_messages_client(
        settings(),
        client_factory=factory,
    )

    assert result is not None
    assert received == {
        "base_url": "https://api.anthropic.com/",
        "api_key": "test-only-anthropic-token",
    }
