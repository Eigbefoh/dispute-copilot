"""Native Anthropic Messages transport for evidence extraction."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from dispute_copilot.config import ModelProviderSettings
from dispute_copilot.extraction.errors import (
    EvidenceCandidateValidationError,
    InvalidProviderResponseError,
    ProviderConfigurationError,
    ProviderRejectionError,
    ProviderTransportUnavailableError,
)
from dispute_copilot.extraction.evidence_extraction_models import (
    ExtractionCandidate,
    ExtractionResponse,
    extraction_response_model_for,
)
from dispute_copilot.extraction.models import (
    EvidenceBundle,
    EvidenceDocument,
)
from dispute_copilot.extraction.openai_adapter_common import (
    EXTRACTION_INSTRUCTIONS,
    build_extraction_request,
    media_type_for_image,
    provider_prefix,
    raise_provider_invocation_error,
    validate_bundle_documents,
)
from dispute_copilot.extraction.provider_models import (
    ModelExecutionIdentity,
)
from dispute_copilot.extraction.provider_response_diagnostics import (
    ProviderResponseDiagnostics,
    capture_temporary_raw_response,
    debug_artifact_note,
    format_candidate_coverage_summary,
    format_pydantic_validation_summary,
)
from dispute_copilot.extraction.vision_models import (
    VISION_PROMPT_VERSION,
    VISION_SCHEMA_VERSION,
    VisionObservationResult,
    render_vision_observations,
)
from dispute_copilot.extraction.vision_prompt import (
    VISION_OBSERVATION_INSTRUCTIONS,
)
from dispute_copilot.policy.fact_models import FactDefinition

ADAPTER_NAME = "anthropic_messages"
PROMPT_VERSION = "lean-evidence-extraction/0.6.0"
RESPONSE_SCHEMA_VERSION = "extraction-response/0.4.0"
MAX_TOKENS = 4096
TEMPERATURE = 0


def create_anthropic_messages_client(
    settings: ModelProviderSettings,
    *,
    client_factory: Callable[..., Any] | None = None,
) -> Any:
    """Construct the native Anthropic client without exposing secrets."""
    if settings.base_url is None:
        raise ProviderConfigurationError(
            f"Provider '{settings.provider}' requires an effective "
            f"base URL for the {ADAPTER_NAME} adapter."
        )
    if client_factory is None:
        try:
            from anthropic import Anthropic
        except ImportError as error:
            raise ProviderTransportUnavailableError(
                "The Anthropic transport SDK is not installed; install "
                "project dependencies before live extraction."
            ) from error
        client_factory = Anthropic
    try:
        return client_factory(
            base_url=str(settings.base_url),
            api_key=settings.api_key.get_secret_value(),
        )
    except Exception as error:
        raise ProviderTransportUnavailableError(
            f"Provider '{settings.provider}' transport could not be "
            f"configured for adapter '{ADAPTER_NAME}'."
        ) from error


class AnthropicMessagesEvidenceExtractor:
    """Extract canonical candidates through native Anthropic Messages."""

    def __init__(
        self,
        *,
        client: Any,
        settings: ModelProviderSettings,
        diagnostics: ProviderResponseDiagnostics | None = None,
    ) -> None:
        self.client = client
        self.settings = settings
        self.diagnostics = diagnostics or ProviderResponseDiagnostics()

    @property
    def execution_identity(self) -> ModelExecutionIdentity:
        return ModelExecutionIdentity(
            provider=self.settings.provider,
            model=self.settings.model,
            adapter=ADAPTER_NAME,
            prompt_version=PROMPT_VERSION,
            schema_version=RESPONSE_SCHEMA_VERSION,
            parameters={
                "image_transport": "base64",
                "max_tokens": MAX_TOKENS,
                "structured_output": (
                    "anthropic_output_config_json_schema"
                ),
                "temperature": TEMPERATURE,
            },
        )

    def extract_evidence_candidates(
        self,
        *,
        requested_facts: tuple[FactDefinition, ...],
        evidence_bundle: EvidenceBundle,
        evidence_documents: tuple[EvidenceDocument, ...],
    ) -> tuple[ExtractionCandidate, ...]:
        if not requested_facts:
            return ()
        if any(
            definition.source_type == "CASE_METADATA"
            for definition in requested_facts
        ):
            raise EvidenceCandidateValidationError(
                "metadata facts cannot be sent to the evidence model"
            )

        validate_bundle_documents(
            evidence_bundle=evidence_bundle,
            evidence_documents=evidence_documents,
        )
        content = _anthropic_evidence_content(
            requested_facts=requested_facts,
            evidence_bundle=evidence_bundle,
            evidence_documents=evidence_documents,
        )
        provider_response_model = extraction_response_model_for(
            requested_facts
        )
        try:
            response = self.client.messages.create(
                model=self.settings.model,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                system=EXTRACTION_INSTRUCTIONS,
                messages=[{"role": "user", "content": content}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": (
                            provider_response_model.model_json_schema()
                        ),
                    }
                },
            )
        except Exception as error:
            raise_provider_invocation_error(
                operation="evidence extraction",
                error=error,
                settings=self.settings,
                adapter_name=ADAPTER_NAME,
                contract_description=(
                    "Anthropic Messages multimodal and exact "
                    "JSON-schema output contract"
                ),
            )

        content_text = _single_structured_text(
            response,
            settings=self.settings,
            operation="evidence extraction",
        )
        try:
            parsed = ExtractionResponse.model_validate_json(content_text)
        except ValidationError as error:
            summary = format_pydantic_validation_summary(
                error,
                settings=self.settings,
            )
            artifact_path = capture_temporary_raw_response(
                raw_response=content_text,
                settings=self.settings,
                adapter_name=ADAPTER_NAME,
                diagnostics=self.diagnostics,
            )
            raise InvalidProviderResponseError(
                provider_prefix(self.settings)
                + " response was rejected by the untrusted extraction "
                "schema.\n"
                "Validation errors:\n"
                f"{summary}"
                f"{debug_artifact_note(artifact_path)}"
            ) from error

        coverage_summary = format_candidate_coverage_summary(
            candidates=parsed.candidates,
            requested_facts=requested_facts,
        )
        if coverage_summary is not None:
            artifact_path = capture_temporary_raw_response(
                raw_response=content_text,
                settings=self.settings,
                adapter_name=ADAPTER_NAME,
                diagnostics=self.diagnostics,
            )
            raise InvalidProviderResponseError(
                provider_prefix(self.settings)
                + " response did not exactly cover the requested "
                "facts.\n"
                "Response consistency errors:\n"
                f"{coverage_summary}"
                f"{debug_artifact_note(artifact_path)}"
            )
        return parsed.candidates


class AnthropicMessagesVisionProvider:
    """Convert one image through the shared structured vision contract."""

    def __init__(
        self,
        *,
        client: Any,
        settings: ModelProviderSettings,
    ) -> None:
        self.client = client
        self.settings = settings

    @property
    def provider_version(self) -> str:
        return ModelExecutionIdentity(
            provider=self.settings.provider,
            model=self.settings.model,
            adapter=ADAPTER_NAME,
            prompt_version=VISION_PROMPT_VERSION,
            schema_version=VISION_SCHEMA_VERSION,
            parameters={
                "image_transport": "base64",
                "max_tokens": MAX_TOKENS,
                "structured_output": (
                    "anthropic_output_config_json_schema"
                ),
                "temperature": TEMPERATURE,
            },
        ).canonical_json()

    def describe_image(self, *, file_path: Path) -> str:
        media_type = media_type_for_image(file_path)
        try:
            response = self.client.messages.create(
                model=self.settings.model,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                system=VISION_OBSERVATION_INSTRUCTIONS,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            _anthropic_image_block(
                                file_path,
                                media_type,
                            ),
                            {
                                "type": "text",
                                "text": (
                                    "Create source-preserving structured "
                                    "observations for this evidence image."
                                ),
                            },
                        ],
                    }
                ],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": (
                            VisionObservationResult.model_json_schema()
                        ),
                    }
                },
            )
        except Exception as error:
            raise_provider_invocation_error(
                operation="image description",
                error=error,
                settings=self.settings,
                adapter_name=ADAPTER_NAME,
                contract_description=(
                    "Anthropic Messages multimodal and exact "
                    "JSON-schema output contract"
                ),
            )

        content_text = _single_structured_text(
            response,
            settings=self.settings,
            operation="image conversion",
        )
        try:
            observations = VisionObservationResult.model_validate_json(
                content_text
            )
        except ValidationError as error:
            summary = format_pydantic_validation_summary(
                error,
                settings=self.settings,
            )
            raise InvalidProviderResponseError(
                provider_prefix(self.settings)
                + " image observations were rejected by the "
                "untrusted vision schema.\n"
                "Validation errors:\n"
                f"{summary}"
            ) from error
        return render_vision_observations(observations)


def _anthropic_evidence_content(
    *,
    requested_facts: tuple[FactDefinition, ...],
    evidence_bundle: EvidenceBundle,
    evidence_documents: tuple[EvidenceDocument, ...],
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    documents_by_name = {
        document.file_name: document
        for document in evidence_documents
    }
    for evidence_file in evidence_bundle.evidence_files:
        if not evidence_file.media_type.startswith("image/"):
            continue
        document = documents_by_name[evidence_file.file_name]
        content.append(
            {
                "type": "text",
                "text": (
                    "The next image is admitted evidence "
                    f"document_id={document.document_id}, "
                    f"file_name={document.file_name}, page_number=1."
                ),
            }
        )
        content.append(
            _anthropic_image_block(
                evidence_file.file_path,
                evidence_file.media_type,
            )
        )
    content.append(
        {
            "type": "text",
            "text": build_extraction_request(
                requested_facts=requested_facts,
                evidence_bundle=evidence_bundle,
                evidence_documents=evidence_documents,
            ),
        }
    )
    return content


def _anthropic_image_block(
    file_path: Path,
    media_type: str,
) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(
                file_path.read_bytes()
            ).decode("ascii"),
        },
    }


def _single_structured_text(
    response: Any,
    *,
    settings: ModelProviderSettings,
    operation: str,
) -> str:
    stop_reason = _attribute(response, "stop_reason")
    if stop_reason == "refusal":
        raise ProviderRejectionError(
            provider_prefix(settings)
            + f" refused {operation}; no fallback was attempted."
        )
    if stop_reason == "max_tokens":
        raise InvalidProviderResponseError(
            provider_prefix(settings)
            + f" truncated {operation} at the configured token limit; "
            "no partial response was admitted."
        )

    content = _attribute(response, "content")
    if not isinstance(content, (list, tuple)) or len(content) != 1:
        raise InvalidProviderResponseError(
            provider_prefix(settings)
            + f" returned {operation} without exactly one structured "
            "text payload."
        )
    block = content[0]
    if _attribute(block, "type") != "text":
        raise InvalidProviderResponseError(
            provider_prefix(settings)
            + f" returned an unsupported {operation} content block."
        )
    text = _attribute(block, "text")
    if not isinstance(text, str) or not text.strip():
        raise InvalidProviderResponseError(
            provider_prefix(settings)
            + f" returned a blank {operation} payload."
        )
    return text.strip()


def _attribute(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)
