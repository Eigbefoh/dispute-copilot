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
)
from dispute_copilot.extraction.openai_responses_adapter import (
    OpenAIResponsesVisionProvider,
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
    nebius_responses = RecordingResponses(observations)
    openai_responses = RecordingResponses(observations)
    anthropic_messages = RecordingMessages(
        observations.model_dump_json()
    )
    providers = (
        OpenAIResponsesVisionProvider(
            client=SimpleNamespace(responses=nebius_responses),
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

    for call in (
        nebius_responses.calls[0],
        openai_responses.calls[0],
    ):
        assert call["text_format"] is VisionObservationResult
        instructions = str(call["instructions"]).lower()
        assert "written_text_observations" in instructions
        assert "visible_pixel_observations" in instructions
        assert "limitations" in instructions
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
        OpenAIResponsesVisionProvider(
            client=SimpleNamespace(
                responses=RecordingResponses(invalid)
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
