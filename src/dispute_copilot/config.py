"""Central, secret-safe model-provider configuration."""

from __future__ import annotations

import os
import warnings
from collections.abc import Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    ValidationError,
    field_validator,
)

from dispute_copilot.extraction.errors import (
    ProviderConfigurationError,
    UnsupportedProviderError,
)

DEFAULT_NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1/"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/"
SUPPORTED_MODEL_PROVIDERS = frozenset(
    {"nebius", "openai", "anthropic"}
)

GENERIC_ENV_NAMES = (
    "MODEL_PROVIDER",
    "MODEL_NAME",
    "MODEL_API_KEY",
    "MODEL_BASE_URL",
)
LEGACY_NEBIUS_ENV_NAMES = (
    "NEBIUS_API_KEY",
    "NEBIUS_MODEL",
    "NEBIUS_BASE_URL",
)


class ModelProviderSettings(BaseModel):
    """Frozen provider/model selection with a redacted credential."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(
        min_length=1,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    model: str = Field(min_length=1)
    api_key: SecretStr
    base_url: HttpUrl | None = None

    @field_validator("provider", mode="before")
    @classmethod
    def provider_is_normalized(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip().lower()

    @field_validator("model")
    @classmethod
    def model_is_exact_and_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model cannot be blank")
        if value != value.strip():
            raise ValueError(
                "model must not contain surrounding whitespace"
            )
        return value

    @field_validator("api_key")
    @classmethod
    def api_key_is_not_blank(cls, value: SecretStr) -> SecretStr:
        raw_value = value.get_secret_value()
        if not raw_value or not raw_value.strip():
            raise ValueError("api_key cannot be blank")
        return value

    @field_validator("base_url")
    @classmethod
    def base_url_is_secret_safe(
        cls,
        value: HttpUrl | None,
    ) -> HttpUrl | None:
        if value is None:
            return None
        if value.username or value.password:
            raise ValueError("base_url cannot contain credentials")
        if value.query or value.fragment:
            raise ValueError(
                "base_url cannot contain a query or fragment"
            )
        return value


def load_model_provider_settings(
    environ: Mapping[str, str] | None = None,
) -> ModelProviderSettings:
    """Load generic settings, with an isolated legacy Nebius bridge."""
    source = os.environ if environ is None else environ
    generic_present = {
        name for name in GENERIC_ENV_NAMES if source.get(name) is not None
    }
    legacy_present = {
        name
        for name in LEGACY_NEBIUS_ENV_NAMES
        if source.get(name) is not None
    }
    if generic_present and legacy_present:
        raise ProviderConfigurationError(
            "Do not mix MODEL_* settings with deprecated NEBIUS_* "
            "settings. Configure one provider format only."
        )
    if generic_present:
        return _load_generic_settings(source)
    if legacy_present:
        return _load_legacy_nebius_settings(source)
    raise ProviderConfigurationError(
        "MODEL_PROVIDER is required for live extraction."
    )


def _load_generic_settings(
    source: Mapping[str, str],
) -> ModelProviderSettings:
    provider = source.get("MODEL_PROVIDER")
    if provider is None or not provider.strip():
        raise ProviderConfigurationError(
            "MODEL_PROVIDER is required for live extraction."
        )
    normalized_provider = provider.strip().lower()
    if normalized_provider not in SUPPORTED_MODEL_PROVIDERS:
        raise UnsupportedProviderError(
            f"Unsupported model provider '{normalized_provider}'. "
            "Registered providers: "
            f"{sorted(SUPPORTED_MODEL_PROVIDERS)}"
        )
    model = source.get("MODEL_NAME")
    if model is None or not model.strip():
        raise ProviderConfigurationError(
            "MODEL_NAME is required. Configure the exact user-selected "
            "model ID."
        )
    api_key = source.get("MODEL_API_KEY")
    if api_key is None or not api_key.strip():
        raise ProviderConfigurationError(
            "MODEL_API_KEY is required for live extraction."
        )
    return _validate_settings(
        provider=normalized_provider,
        model=model,
        api_key=api_key,
        base_url=source.get("MODEL_BASE_URL"),
    )


def _load_legacy_nebius_settings(
    source: Mapping[str, str],
) -> ModelProviderSettings:
    api_key = source.get("NEBIUS_API_KEY")
    if api_key is None or not api_key.strip():
        raise ProviderConfigurationError(
            "Deprecated NEBIUS_API_KEY is required when using legacy "
            "Nebius configuration."
        )
    model = source.get("NEBIUS_MODEL")
    if model is None or not model.strip():
        raise ProviderConfigurationError(
            "Deprecated NEBIUS_MODEL is required when using legacy "
            "Nebius configuration."
        )
    warnings.warn(
        "NEBIUS_* settings are deprecated; migrate to MODEL_PROVIDER, "
        "MODEL_NAME, MODEL_API_KEY, and MODEL_BASE_URL.",
        DeprecationWarning,
        stacklevel=2,
    )
    return _validate_settings(
        provider="nebius",
        model=model,
        api_key=api_key,
        base_url=source.get(
            "NEBIUS_BASE_URL",
            DEFAULT_NEBIUS_BASE_URL,
        ),
    )


def _validate_settings(
    *,
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None,
) -> ModelProviderSettings:
    try:
        return ModelProviderSettings(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )
    except ValidationError as error:
        raise ProviderConfigurationError(
            "Model-provider configuration is invalid. Provider names "
            "must be lowercase identifiers, model values must be exact, "
            "and MODEL_BASE_URL must be a credential-free HTTP(S) URL."
        ) from error
