"""Provider registry and runtime composition for live extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from dispute_copilot.config import (
    DEFAULT_NEBIUS_BASE_URL,
    ModelProviderSettings,
)
from dispute_copilot.extraction.errors import (
    ProviderConfigurationError,
    UnsupportedProviderError,
)
from dispute_copilot.extraction.evidence_extractor import (
    EvidenceCandidateExtractor,
)
from dispute_copilot.extraction.openai_chat_completions_adapter import (
    OpenAIChatCompletionsEvidenceExtractor,
)
from dispute_copilot.extraction.openai_responses_adapter import (
    OpenAIResponsesEvidenceExtractor,
    OpenAIResponsesVisionProvider,
    create_openai_responses_client,
)
from dispute_copilot.extraction.providers import VisionProvider
from dispute_copilot.extraction.provider_response_diagnostics import (
    ProviderResponseDiagnostics,
)


@dataclass(frozen=True)
class ExtractionRuntime:
    """Concrete dependencies consumed by existing orchestration."""

    extractor: EvidenceCandidateExtractor
    vision_provider: VisionProvider


RuntimeFactory = Callable[
    [
        ModelProviderSettings,
        Callable[..., Any] | None,
        ProviderResponseDiagnostics,
    ],
    ExtractionRuntime,
]


def create_nebius_runtime(
    settings: ModelProviderSettings,
    client_factory: Callable[..., Any] | None = None,
    diagnostics: ProviderResponseDiagnostics | None = None,
) -> ExtractionRuntime:
    """Compose Nebius using its supported structured-output route."""
    effective_settings = settings
    if settings.base_url is None:
        effective_settings = ModelProviderSettings(
            provider=settings.provider,
            model=settings.model,
            api_key=settings.api_key,
            base_url=DEFAULT_NEBIUS_BASE_URL,
        )
    client = create_openai_responses_client(
        effective_settings,
        client_factory=client_factory,
    )
    return ExtractionRuntime(
        extractor=OpenAIChatCompletionsEvidenceExtractor(
            client=client,
            settings=effective_settings,
            diagnostics=diagnostics,
        ),
        vision_provider=OpenAIResponsesVisionProvider(
            client=client,
            settings=effective_settings,
        ),
    )


def create_openai_compatible_runtime(
    settings: ModelProviderSettings,
    client_factory: Callable[..., Any] | None = None,
    diagnostics: ProviderResponseDiagnostics | None = None,
) -> ExtractionRuntime:
    """Compose an explicitly configured compatible endpoint."""
    if settings.base_url is None:
        raise ProviderConfigurationError(
            "Provider 'openai_compatible' requires MODEL_BASE_URL. No "
            "default endpoint is selected."
        )
    return _create_openai_responses_runtime(
        settings,
        client_factory,
        diagnostics,
    )


def _create_openai_responses_runtime(
    settings: ModelProviderSettings,
    client_factory: Callable[..., Any] | None,
    diagnostics: ProviderResponseDiagnostics | None,
) -> ExtractionRuntime:
    client = create_openai_responses_client(
        settings,
        client_factory=client_factory,
    )
    return ExtractionRuntime(
        extractor=OpenAIResponsesEvidenceExtractor(
            client=client,
            settings=settings,
            diagnostics=diagnostics,
        ),
        vision_provider=OpenAIResponsesVisionProvider(
            client=client,
            settings=settings,
        ),
    )


PROVIDER_FACTORIES: dict[str, RuntimeFactory] = {
    "nebius": create_nebius_runtime,
    "openai_compatible": create_openai_compatible_runtime,
}


def create_extraction_runtime(
    settings: ModelProviderSettings,
    *,
    client_factory: Callable[..., Any] | None = None,
    diagnostics: ProviderResponseDiagnostics | None = None,
) -> ExtractionRuntime:
    """Select exactly one registered provider without fallback."""
    factory = PROVIDER_FACTORIES.get(settings.provider)
    if factory is None:
        raise UnsupportedProviderError(
            f"Unsupported model provider '{settings.provider}'. "
            f"Registered providers: {sorted(PROVIDER_FACTORIES)}"
        )
    return factory(
        settings,
        client_factory,
        diagnostics or ProviderResponseDiagnostics(),
    )
