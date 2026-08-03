"""Shared visual-evidence semantics for every supported provider."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
from openai import OpenAI

from dispute_copilot.config import ModelProviderSettings
from dispute_copilot.extraction.anthropic_messages_adapter import (
    AnthropicMessagesVisionProvider,
)
from dispute_copilot.extraction.errors import (
    InvalidProviderResponseError,
    ProviderRejectionError,
)
from dispute_copilot.extraction.openai_chat_completions_adapter import (
    OpenAIChatCompletionsVisionProvider,
)
from dispute_copilot.extraction.openai_responses_adapter import (
    OpenAIResponsesVisionProvider,
)
from dispute_copilot.extraction.provider_response_diagnostics import (
    ProviderResponseDiagnostics,
)
from dispute_copilot.extraction.vision_models import (
    VisionObservationResult,
    render_vision_observations,
)

CHAIR_IMAGE = Path(
    "data/evidence/CB-2025-0006_delivery_photo.png"
)


class RecordingResponses:
    def __init__(self, parsed: object) -> None:
        self.parsed = parsed
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.parsed)


class RecordingChatCompletions:
    def __init__(
        self,
        text: str | None,
        *,
        refusal: str | None = None,
    ) -> None:
        self.text = text
        self.refusal = refusal
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=self.text,
                        refusal=self.refusal,
                    )
                )
            ]
        )


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


def _settings(provider: str) -> ModelProviderSettings:
    endpoints = {
        "nebius": "https://api.tokenfactory.nebius.com/v1/",
        "openai": "https://api.openai.com/v1/",
        "anthropic": "https://api.anthropic.com/",
    }
    return ModelProviderSettings(
        provider=provider,
        model=f"{provider}-vision-model",
        api_key=f"{provider}-test-token",
        base_url=endpoints[provider],
    )


def test_cb_0006_image_preserves_three_channels_for_every_provider() -> None:
    observations = VisionObservationResult(
        written_text_observations=(
            "Carlton Armchair (Sage) — delivered 18 Apr 2025",
        ),
        visible_pixel_observations=(
            "A front-facing, sage-coloured armchair is visible.",
        ),
        limitations=(
            "Only a front view is visible; hidden structural condition "
            "cannot be established.",
        ),
    )
    nebius_completions = RecordingChatCompletions(
        observations.model_dump_json()
    )
    openai_responses = RecordingResponses(observations)
    anthropic_messages = RecordingMessages(
        observations.model_dump_json()
    )
    providers = (
        OpenAIChatCompletionsVisionProvider(
            client=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=nebius_completions
                )
            ),
            settings=_settings("nebius"),
        ),
        OpenAIResponsesVisionProvider(
            client=SimpleNamespace(responses=openai_responses),
            settings=_settings("openai"),
        ),
        AnthropicMessagesVisionProvider(
            client=SimpleNamespace(messages=anthropic_messages),
            settings=_settings("anthropic"),
        ),
    )

    rendered = tuple(
        provider.describe_image(file_path=CHAIR_IMAGE)
        for provider in providers
    )

    expected = render_vision_observations(observations)
    assert rendered == (expected, expected, expected)
    nebius_identity = json.loads(providers[0].provider_version)
    assert nebius_identity["provider"] == "nebius"
    assert nebius_identity["adapter"] == "openai_chat_completions"
    assert nebius_identity["parameters"]["structured_output"] == (
        "chat_completions_json_schema"
    )
    payload = json.loads(expected)
    assert payload["written_text_observations"] == [
        "Carlton Armchair (Sage) — delivered 18 Apr 2025"
    ]
    assert payload["visible_pixel_observations"] == [
        "A front-facing, sage-coloured armchair is visible."
    ]
    assert payload["limitations"]
    forbidden = (
        "defect-free",
        "does not wobble",
        "no hidden damage",
        "matches the listing",
    )
    assert all(
        phrase not in expected.lower()
        for phrase in forbidden
    )

    nebius_call = nebius_completions.calls[0]
    response_format = nebius_call["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"] == (
        VisionObservationResult.model_json_schema()
    )
    assert "schema_" not in response_format["json_schema"]
    nebius_instructions = str(
        nebius_call["messages"][0]["content"]
    ).lower()
    assert "written_text_observations" in nebius_instructions
    assert "visible_pixel_observations" in nebius_instructions
    assert "limitations" in nebius_instructions
    image_block = nebius_call["messages"][1]["content"][1]
    assert image_block["type"] == "image_url"
    assert image_block["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )

    openai_call = openai_responses.calls[0]
    assert openai_call["text_format"] is VisionObservationResult
    openai_instructions = str(openai_call["instructions"]).lower()
    assert "written_text_observations" in openai_instructions
    assert "visible_pixel_observations" in openai_instructions
    assert "limitations" in openai_instructions
    anthropic_call = anthropic_messages.calls[0]
    assert (
        anthropic_call["output_config"]["format"]["schema"]
        == VisionObservationResult.model_json_schema()
    )


def test_openai_sdk_serializes_shared_vision_schema() -> None:
    observations = VisionObservationResult(
        written_text_observations=("Delivered 18 Apr 2025",),
        visible_pixel_observations=(
            "A front-facing sage armchair is visible.",
        ),
        limitations=("Only a front view is visible.",),
    )
    captured_body: dict[str, object] = {}

    def handle_request(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "error": None,
                "incomplete_details": None,
                "instructions": "test",
                "max_output_tokens": None,
                "model": "openai-vision-model",
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": (
                                    observations.model_dump_json()
                                ),
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "parallel_tool_calls": True,
                "previous_response_id": None,
                "reasoning": {
                    "effort": None,
                    "summary": None,
                },
                "store": True,
                "temperature": 1.0,
                "text": {
                    "format": {"type": "text"},
                    "verbosity": "medium",
                },
                "tool_choice": "auto",
                "tools": [],
                "top_p": 1.0,
                "truncation": "disabled",
                "usage": {
                    "input_tokens": 1,
                    "input_tokens_details": {
                        "cached_tokens": 0
                    },
                    "output_tokens": 1,
                    "output_tokens_details": {
                        "reasoning_tokens": 0
                    },
                    "total_tokens": 2,
                },
                "user": None,
                "metadata": {},
            },
        )

    client = OpenAI(
        api_key="test-only-openai-token",
        base_url="https://api.openai.com/v1/",
        http_client=httpx.Client(
            transport=httpx.MockTransport(handle_request)
        ),
    )
    provider = OpenAIResponsesVisionProvider(
        client=client,
        settings=_settings("openai"),
    )

    result = provider.describe_image(file_path=CHAIR_IMAGE)

    assert result == render_vision_observations(observations)
    response_format = captured_body["text"]["format"]
    assert response_format["type"] == "json_schema"
    assert response_format["strict"] is True
    assert response_format["schema"] == (
        VisionObservationResult.model_json_schema()
    )
    assert "schema_" not in response_format


def test_nebius_sdk_serializes_chat_completions_vision_schema() -> None:
    observations = VisionObservationResult(
        written_text_observations=("Delivered 18 Apr 2025",),
        visible_pixel_observations=(
            "A front-facing sage armchair is visible.",
        ),
        limitations=("Only a front view is visible.",),
    )
    captured_body: dict[str, object] = {}

    def handle_request(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_test",
                "object": "chat.completion",
                "created": 1,
                "model": "nebius-vision-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": observations.model_dump_json(),
                            "refusal": None,
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
        api_key="test-only-nebius-token",
        base_url="https://api.tokenfactory.nebius.com/v1/",
        http_client=httpx.Client(
            transport=httpx.MockTransport(handle_request)
        ),
    )
    provider = OpenAIChatCompletionsVisionProvider(
        client=client,
        settings=_settings("nebius"),
    )

    result = provider.describe_image(file_path=CHAIR_IMAGE)

    assert result == render_vision_observations(observations)
    response_format = captured_body["response_format"]
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert json_schema["strict"] is True
    assert json_schema["schema"] == (
        VisionObservationResult.model_json_schema()
    )
    assert "schema_" not in json_schema
    messages = captured_body["messages"]
    assert messages[1]["content"][1]["type"] == "image_url"


@pytest.mark.parametrize(
    "invalid",
    [
        {
            "written_text_observations": [],
            "visible_pixel_observations": [
                "The chair is defect-free."
            ],
            "limitations": ["Only a front view is visible."],
        },
        {
            "written_text_observations": [],
            "visible_pixel_observations": [
                "A sage armchair is visible."
            ],
        },
    ],
)
def test_invalid_visual_semantics_fail_without_document_output(
    invalid: dict[str, object],
) -> None:
    providers = (
        OpenAIChatCompletionsVisionProvider(
            client=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=RecordingChatCompletions(
                        json.dumps(invalid)
                    )
                )
            ),
            settings=_settings("nebius"),
        ),
        OpenAIResponsesVisionProvider(
            client=SimpleNamespace(
                responses=RecordingResponses(invalid)
            ),
            settings=_settings("openai"),
        ),
        AnthropicMessagesVisionProvider(
            client=SimpleNamespace(
                messages=RecordingMessages(json.dumps(invalid))
            ),
            settings=_settings("anthropic"),
        ),
    )

    for provider in providers:
        with pytest.raises(InvalidProviderResponseError):
            provider.describe_image(file_path=CHAIR_IMAGE)


def test_nebius_vision_refusal_fails_without_fallback() -> None:
    completions = RecordingChatCompletions(
        None,
        refusal="I cannot process this image.",
    )
    provider = OpenAIChatCompletionsVisionProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        ),
        settings=_settings("nebius"),
    )

    with pytest.raises(
        ProviderRejectionError,
        match="refused image conversion",
    ):
        provider.describe_image(file_path=CHAIR_IMAGE)

    assert len(completions.calls) == 1


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            SimpleNamespace(choices=[]),
            "no Chat Completions choices",
        ),
        (
            SimpleNamespace(
                choices=[SimpleNamespace(message=None)]
            ),
            "choice without a message",
        ),
    ],
)
def test_nebius_vision_missing_response_structure_fails_closed(
    response: SimpleNamespace,
    message: str,
) -> None:
    provider = OpenAIChatCompletionsVisionProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_: response
                )
            )
        ),
        settings=_settings("nebius"),
    )

    with pytest.raises(InvalidProviderResponseError, match=message):
        provider.describe_image(file_path=CHAIR_IMAGE)


def test_nebius_vision_missing_content_fails_closed() -> None:
    completions = RecordingChatCompletions(None)
    provider = OpenAIChatCompletionsVisionProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        ),
        settings=_settings("nebius"),
    )

    with pytest.raises(
        InvalidProviderResponseError,
        match="no structured image observations",
    ):
        provider.describe_image(file_path=CHAIR_IMAGE)


def test_nebius_vision_invalid_response_debug_artifact_is_secret_safe(
    tmp_path: Path,
) -> None:
    secret = "nebius-test-token"
    completions = RecordingChatCompletions(
        json.dumps(
            {
                "written_text_observations": [],
                "visible_pixel_observations": [],
                "limitations": [],
                "provider_note": secret,
            }
        )
    )
    provider = OpenAIChatCompletionsVisionProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        ),
        settings=_settings("nebius"),
        diagnostics=ProviderResponseDiagnostics(
            capture_raw_response=True,
            temporary_directory=tmp_path,
        ),
    )

    with pytest.raises(InvalidProviderResponseError) as caught:
        provider.describe_image(file_path=CHAIR_IMAGE)

    message = str(caught.value)
    assert secret not in message
    artifacts = tuple(tmp_path.glob("*.json"))
    assert len(artifacts) == 1
    artifact_text = artifacts[0].read_text(encoding="utf-8")
    assert secret not in artifact_text
    assert "[REDACTED]" in artifact_text
