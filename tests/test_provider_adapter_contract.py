"""Shared canonical boundaries for every registered fact adapter."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dispute_copilot.config import ModelProviderSettings
from dispute_copilot.extraction.anthropic_messages_adapter import (
    AnthropicMessagesEvidenceExtractor,
)
from dispute_copilot.extraction.errors import (
    EvidenceCandidateValidationError,
    InvalidProviderResponseError,
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
    OpenAIChatCompletionsEvidenceExtractor,
)
from dispute_copilot.extraction.openai_responses_adapter import (
    OpenAIResponsesEvidenceExtractor,
)
from dispute_copilot.policy.fact_models import FactDefinition


class RecordingCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=self.content,
                        refusal=None,
                    )
                )
            ]
        )


class RecordingResponses:
    def __init__(self, parsed: object) -> None:
        self.parsed = parsed
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.parsed)


class RecordingMessages:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[
                SimpleNamespace(type="text", text=self.text)
            ],
        )


@pytest.fixture
def contract_context(
    tmp_path: Path,
) -> tuple[
    EvidenceBundle,
    tuple[EvidenceDocument, ...],
    tuple[FactDefinition, ...],
]:
    path = tmp_path / "record.pdf"
    path.write_bytes(b"record")
    evidence_file = EvidenceFile(
        file_name=path.name,
        file_path=path,
        media_type="application/pdf",
        sha256="a" * 64,
        size_bytes=path.stat().st_size,
    )
    document = EvidenceDocument(
        document_id=uuid4(),
        file_name=path.name,
        source_sha256=evidence_file.sha256,
        media_type=evidence_file.media_type,
        pages=(
            EvidencePage(
                page_number=1,
                text="Carrier status: DELIVERED",
                extraction_method=ExtractionMethod.NATIVE_TEXT,
            ),
        ),
        converter_version="contract/pdf",
        created_at=datetime.now(timezone.utc),
    )
    requested = (
        FactDefinition(
            fact_name="carrier_reported_delivery_status",
            description="Carrier-reported delivery status.",
            value_type="STRING",
            source_type="EVIDENCE_TEXT_ASSERTION",
            cardinality="SINGLE",
        ),
    )
    bundle = EvidenceBundle(
        run_id=uuid4(),
        selection_id=uuid4(),
        policy_id="contract-policy",
        case_transaction_id="txn-contract",
        evidence_files=(evidence_file,),
    )
    return bundle, (document,), requested


def _settings(provider: str) -> ModelProviderSettings:
    endpoints = {
        "nebius": "https://api.tokenfactory.nebius.com/v1/",
        "openai": "https://api.openai.com/v1/",
        "anthropic": "https://api.anthropic.com/",
    }
    return ModelProviderSettings(
        provider=provider,
        model=f"{provider}-contract-model",
        api_key=f"{provider}-test-token",
        base_url=endpoints[provider],
    )


def test_controlled_valid_payload_has_one_canonical_result_shape(
    contract_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
) -> None:
    bundle, documents, requested = contract_context
    response = ExtractionResponse(
        candidates=(
            ExtractionCandidate(
                fact_name=requested[0].fact_name,
                status="NOT_FOUND",
                values=(),
                confidence=None,
            ),
        )
    )
    completions = RecordingCompletions(
        response.model_dump_json()
    )
    responses = RecordingResponses(response)
    messages = RecordingMessages(response.model_dump_json())
    adapters = (
        OpenAIChatCompletionsEvidenceExtractor(
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=completions)
            ),
            settings=_settings("nebius"),
        ),
        OpenAIResponsesEvidenceExtractor(
            client=SimpleNamespace(responses=responses),
            settings=_settings("openai"),
        ),
        AnthropicMessagesEvidenceExtractor(
            client=SimpleNamespace(messages=messages),
            settings=_settings("anthropic"),
        ),
    )

    results = tuple(
        adapter.extract_evidence_candidates(
            requested_facts=requested,
            evidence_bundle=bundle,
            evidence_documents=documents,
        )
        for adapter in adapters
    )

    assert results == (
        response.candidates,
        response.candidates,
        response.candidates,
    )
    assert all(
        isinstance(result[0], ExtractionCandidate)
        for result in results
    )

    nebius_request = completions.calls[0]["messages"][1][
        "content"
    ][0]["text"]
    openai_request = responses.calls[0]["input"][0]["content"][
        0
    ]["text"]
    anthropic_request = messages.calls[0]["messages"][0][
        "content"
    ][-1]["text"]
    assert nebius_request == openai_request == anthropic_request


def test_every_adapter_rejects_the_same_invalid_status_boundary(
    contract_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
) -> None:
    bundle, documents, requested = contract_context
    invalid = {
        "candidates": [
            {
                "fact_name": requested[0].fact_name,
                "status": "COMPLETE",
                "values": [],
                "confidence": None,
            }
        ]
    }
    completions = RecordingCompletions(json.dumps(invalid))
    responses = RecordingResponses(invalid)
    messages = RecordingMessages(json.dumps(invalid))
    adapters = (
        OpenAIChatCompletionsEvidenceExtractor(
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=completions)
            ),
            settings=_settings("nebius"),
        ),
        OpenAIResponsesEvidenceExtractor(
            client=SimpleNamespace(responses=responses),
            settings=_settings("openai"),
        ),
        AnthropicMessagesEvidenceExtractor(
            client=SimpleNamespace(messages=messages),
            settings=_settings("anthropic"),
        ),
    )

    for adapter in adapters:
        with pytest.raises(
            InvalidProviderResponseError,
        ) as captured:
            adapter.extract_evidence_candidates(
                requested_facts=requested,
                evidence_bundle=bundle,
                evidence_documents=documents,
            )
        message = str(captured.value)
        assert "candidates.0.status [literal_error]" in message
        assert "COMPLETE" not in message


def test_every_adapter_rejects_metadata_before_transport(
    contract_context: tuple[
        EvidenceBundle,
        tuple[EvidenceDocument, ...],
        tuple[FactDefinition, ...],
    ],
) -> None:
    bundle, documents, _ = contract_context
    completions = RecordingCompletions("")
    responses = RecordingResponses(None)
    messages = RecordingMessages("")
    adapters = (
        OpenAIChatCompletionsEvidenceExtractor(
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=completions)
            ),
            settings=_settings("nebius"),
        ),
        OpenAIResponsesEvidenceExtractor(
            client=SimpleNamespace(responses=responses),
            settings=_settings("openai"),
        ),
        AnthropicMessagesEvidenceExtractor(
            client=SimpleNamespace(messages=messages),
            settings=_settings("anthropic"),
        ),
    )
    metadata = (
        FactDefinition(
            fact_name="avs_result",
            description="AVS result.",
            value_type="STRING",
            source_type="CASE_METADATA",
            cardinality="SINGLE",
        ),
    )

    for adapter in adapters:
        with pytest.raises(
            EvidenceCandidateValidationError,
            match="metadata facts",
        ):
            adapter.extract_evidence_candidates(
                requested_facts=metadata,
                evidence_bundle=bundle,
                evidence_documents=documents,
            )

    assert completions.calls == []
    assert responses.calls == []
    assert messages.calls == []
