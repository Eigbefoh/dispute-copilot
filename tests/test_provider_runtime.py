"""Tests for provider-neutral configuration and runtime selection."""

from __future__ import annotations

import inspect
import json
from uuid import uuid4

import pytest

from dispute_copilot.config import (
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_NEBIUS_BASE_URL,
    DEFAULT_OPENAI_BASE_URL,
    ModelProviderSettings,
    load_model_provider_settings,
)
from dispute_copilot.extraction import resolve_facts
from dispute_copilot.extraction.errors import (
    ProviderConfigurationError,
    UnsupportedProviderError,
)
from dispute_copilot.extraction.anthropic_messages_adapter import (
    AnthropicMessagesEvidenceExtractor,
    AnthropicMessagesVisionProvider,
)
from dispute_copilot.extraction.openai_chat_completions_adapter import (
    OpenAIChatCompletionsEvidenceExtractor,
    OpenAIChatCompletionsVisionProvider,
)
from dispute_copilot.extraction.openai_responses_adapter import (
    OpenAIResponsesEvidenceExtractor,
    OpenAIResponsesVisionProvider,
)
from dispute_copilot.extraction.provider_runtime import (
    PROVIDER_FACTORIES,
    create_extraction_runtime,
)
from dispute_copilot.extraction.provider_response_diagnostics import (
    ProviderResponseDiagnostics,
)


def test_generic_settings_preserve_exact_provider_and_model() -> None:
    settings = load_model_provider_settings(
        {
            "MODEL_PROVIDER": "nebius",
            "MODEL_NAME": "user/chosen-model-id",
            "MODEL_API_KEY": "test-only-token",
            "MODEL_BASE_URL": DEFAULT_NEBIUS_BASE_URL,
        }
    )

    assert settings.provider == "nebius"
    assert settings.model == "user/chosen-model-id"
    assert str(settings.base_url) == DEFAULT_NEBIUS_BASE_URL


def test_nebius_runtime_uses_registered_default_endpoint() -> None:
    settings = ModelProviderSettings(
        provider="nebius",
        api_key="test-only-token",
        model="user/chosen-model-id",
    )
    received: dict[str, str] = {}
    expected_client = object()

    def fake_factory(**kwargs: str) -> object:
        received.update(kwargs)
        return expected_client

    runtime = create_extraction_runtime(
        settings,
        client_factory=fake_factory,
    )

    assert runtime.extractor.client is expected_client
    assert runtime.vision_provider.client is expected_client
    assert received == {
        "base_url": DEFAULT_NEBIUS_BASE_URL,
        "api_key": "test-only-token",
    }
    assert runtime.extractor.settings.model == "user/chosen-model-id"
    assert "api.openai.com" not in received["base_url"]


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        (
            {
                "MODEL_NAME": "user/chosen-model-id",
                "MODEL_API_KEY": "test-only-token",
            },
            "MODEL_PROVIDER is required",
        ),
        (
            {
                "MODEL_PROVIDER": "nebius",
                "MODEL_NAME": "user/chosen-model-id",
            },
            "MODEL_API_KEY is required",
        ),
        (
            {
                "MODEL_PROVIDER": "nebius",
                "MODEL_API_KEY": "test-only-token",
            },
            "MODEL_NAME is required",
        ),
    ],
)
def test_missing_generic_settings_fail_clearly(
    environment: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ProviderConfigurationError, match=message):
        load_model_provider_settings(environment)


@pytest.mark.parametrize(
    "base_url",
    [
        "not-a-url",
        "ftp://api.example.test/v1/",
        "https://user:password@example.com/v1/",
        "https://api.example.test/v1/?token=value",
    ],
)
def test_invalid_provider_base_url_fails_closed(
    base_url: str,
) -> None:
    with pytest.raises(
        ProviderConfigurationError,
        match="configuration is invalid",
    ):
        load_model_provider_settings(
            {
                "MODEL_PROVIDER": "openai",
                "MODEL_NAME": "user/chosen-model-id",
                "MODEL_API_KEY": "test-only-token",
                "MODEL_BASE_URL": base_url,
            }
        )


def test_model_api_key_is_redacted_from_settings_repr() -> None:
    secret = "test-only-secret-never-display"
    settings = ModelProviderSettings(
        provider="nebius",
        api_key=secret,
        model="user/chosen-model-id",
    )

    assert secret not in repr(settings)
    assert secret not in str(settings)
    assert "**********" in repr(settings)


def test_legacy_nebius_settings_are_isolated_and_deprecated() -> None:
    with pytest.warns(DeprecationWarning, match="deprecated"):
        settings = load_model_provider_settings(
            {
                "NEBIUS_API_KEY": "test-only-token",
                "NEBIUS_MODEL": "user/chosen-model-id",
            }
        )

    assert settings.provider == "nebius"
    assert settings.model == "user/chosen-model-id"
    assert str(settings.base_url) == DEFAULT_NEBIUS_BASE_URL


def test_generic_and_legacy_settings_cannot_be_mixed() -> None:
    with pytest.raises(
        ProviderConfigurationError,
        match="Do not mix",
    ):
        load_model_provider_settings(
            {
                "MODEL_PROVIDER": "nebius",
                "MODEL_NAME": "user/chosen-model-id",
                "MODEL_API_KEY": "test-only-token",
                "NEBIUS_API_KEY": "legacy-token",
            }
        )


def test_openai_runtime_uses_official_default_endpoint() -> None:
    settings = ModelProviderSettings(
        provider="openai",
        model="user/chosen-model-id",
        api_key="test-only-token",
    )
    received: dict[str, str] = {}

    def fake_factory(**kwargs: str) -> object:
        received.update(kwargs)
        return object()

    runtime = create_extraction_runtime(
        settings,
        client_factory=fake_factory,
    )

    assert received["base_url"] == DEFAULT_OPENAI_BASE_URL
    assert runtime.extractor.settings.provider == "openai"


def test_openai_runtime_preserves_explicit_endpoint_and_model() -> None:
    settings = ModelProviderSettings(
        provider="openai",
        model="user/chosen-model-id",
        api_key="test-only-token",
        base_url="https://models.example.test/v1/",
    )
    received: dict[str, str] = {}

    def fake_factory(**kwargs: str) -> object:
        received.update(kwargs)
        return object()

    runtime = create_extraction_runtime(
        settings,
        client_factory=fake_factory,
    )

    assert received["base_url"] == "https://models.example.test/v1/"
    assert runtime.extractor.settings.model == "user/chosen-model-id"
    assert runtime.extractor.execution_identity.provider == "openai"


def test_registered_routes_use_exact_native_adapters() -> None:
    def fake_factory(**_: str) -> object:
        return object()

    nebius_runtime = create_extraction_runtime(
        ModelProviderSettings(
            provider="nebius",
            model="nebius-model",
            api_key="test-token",
        ),
        client_factory=fake_factory,
    )
    openai_runtime = create_extraction_runtime(
        ModelProviderSettings(
            provider="openai",
            model="openai-model",
            api_key="test-token",
        ),
        client_factory=fake_factory,
    )
    anthropic_runtime = create_extraction_runtime(
        ModelProviderSettings(
            provider="anthropic",
            model="anthropic-model",
            api_key="test-token",
        ),
        client_factory=fake_factory,
    )

    assert isinstance(
        nebius_runtime.extractor,
        OpenAIChatCompletionsEvidenceExtractor,
    )
    assert isinstance(
        nebius_runtime.vision_provider,
        OpenAIChatCompletionsVisionProvider,
    )
    assert isinstance(
        openai_runtime.extractor,
        OpenAIResponsesEvidenceExtractor,
    )
    assert isinstance(
        openai_runtime.vision_provider,
        OpenAIResponsesVisionProvider,
    )
    assert isinstance(
        anthropic_runtime.extractor,
        AnthropicMessagesEvidenceExtractor,
    )
    assert isinstance(
        anthropic_runtime.vision_provider,
        AnthropicMessagesVisionProvider,
    )
    nebius_identity = json.loads(
        nebius_runtime.extractor.execution_identity.canonical_json()
    )
    openai_identity = json.loads(
        openai_runtime.extractor.execution_identity.canonical_json()
    )
    anthropic_identity = json.loads(
        anthropic_runtime.extractor.execution_identity.canonical_json()
    )
    assert nebius_identity["provider"] == "nebius"
    assert nebius_identity["model"] == "nebius-model"
    assert openai_identity["provider"] == "openai"
    assert openai_identity["model"] == "openai-model"
    assert anthropic_identity["provider"] == "anthropic"
    assert anthropic_identity["model"] == "anthropic-model"
    assert nebius_identity["adapter"] == "openai_chat_completions"
    assert openai_identity["adapter"] == "openai_responses"
    assert anthropic_identity["adapter"] == "anthropic_messages"
    for field in ("prompt_version", "schema_version"):
        assert nebius_identity[field] == openai_identity[field]
        assert nebius_identity[field] == anthropic_identity[field]
    assert nebius_identity["prompt_version"] == (
        "lean-evidence-extraction/0.6.0"
    )
    assert "test-token" not in json.dumps(
        (nebius_identity, openai_identity, anthropic_identity)
    )
    assert set(PROVIDER_FACTORIES) == {
        "nebius",
        "openai",
        "anthropic",
    }


def test_anthropic_runtime_uses_official_default_endpoint() -> None:
    received: dict[str, str] = {}

    def fake_factory(**kwargs: str) -> object:
        received.update(kwargs)
        return object()

    runtime = create_extraction_runtime(
        ModelProviderSettings(
            provider="anthropic",
            model="anthropic-model",
            api_key="test-token",
        ),
        client_factory=fake_factory,
    )

    assert received["base_url"] == DEFAULT_ANTHROPIC_BASE_URL
    assert runtime.extractor.settings.provider == "anthropic"


def test_debug_diagnostics_are_explicitly_injected() -> None:
    diagnostics = ProviderResponseDiagnostics(
        capture_raw_response=True,
    )
    runtime = create_extraction_runtime(
        ModelProviderSettings(
            provider="nebius",
            model="nebius-model",
            api_key="test-token",
        ),
        client_factory=lambda **_: object(),
        diagnostics=diagnostics,
    )

    assert runtime.extractor.diagnostics is diagnostics
    assert runtime.vision_provider.diagnostics is diagnostics


def test_cli_debug_provider_response_flag_is_opt_in() -> None:
    selection_id = str(uuid4())

    normal = resolve_facts.build_parser().parse_args([selection_id])
    debug = resolve_facts.build_parser().parse_args(
        [selection_id, "--debug-provider-response"]
    )

    assert normal.debug_provider_response is False
    assert debug.debug_provider_response is True


def test_unknown_provider_fails_before_transport_creation() -> None:
    settings = ModelProviderSettings(
        provider="unsupported",
        model="user/chosen-model-id",
        api_key="test-only-token",
    )
    transport_called = False

    def fail_if_called(**_: str) -> object:
        nonlocal transport_called
        transport_called = True
        raise AssertionError("unsupported provider must not make a client")

    with pytest.raises(
        UnsupportedProviderError,
        match="Unsupported model provider 'unsupported'",
    ):
        create_extraction_runtime(
            settings,
            client_factory=fail_if_called,
        )

    assert transport_called is False


def test_generic_configuration_rejects_removed_compatible_route() -> None:
    with pytest.raises(
        UnsupportedProviderError,
        match="openai_compatible",
    ):
        load_model_provider_settings(
            {
                "MODEL_PROVIDER": "openai_compatible",
                "MODEL_NAME": "user/chosen-model-id",
                "MODEL_API_KEY": "test-only-token",
                "MODEL_BASE_URL": "https://models.example.test/v1/",
            }
        )


def test_cli_missing_configuration_stops_before_resolution(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in (
        "MODEL_PROVIDER",
        "MODEL_NAME",
        "MODEL_API_KEY",
        "MODEL_BASE_URL",
        "NEBIUS_API_KEY",
        "NEBIUS_MODEL",
        "NEBIUS_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    resolution_called = False

    def fail_if_called(**_: object) -> object:
        nonlocal resolution_called
        resolution_called = True
        raise AssertionError("evidence resolution must not run")

    monkeypatch.setattr(
        resolve_facts,
        "resolve_selection_facts",
        fail_if_called,
    )

    exit_code = resolve_facts.main([str(uuid4())])

    assert exit_code == 1
    assert resolution_called is False
    assert "MODEL_PROVIDER is required" in capsys.readouterr().out


def test_cli_source_has_no_provider_specific_composition() -> None:
    source = inspect.getsource(resolve_facts).lower()

    assert "nebius" not in source
    assert "openairesponses" not in source
    assert "create_extraction_runtime" in source
