"""OpenAI Responses API-family transport for evidence extraction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from dispute_copilot.config import ModelProviderSettings
from dispute_copilot.extraction.errors import (
    EvidenceCandidateValidationError,
    InvalidProviderResponseError,
    ProviderConfigurationError,
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
    IMAGE_DETAIL,
    build_extraction_request,
    image_data_url,
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

ADAPTER_NAME = "openai_responses"
PROMPT_VERSION = "lean-evidence-extraction/0.5.0"
RESPONSE_SCHEMA_VERSION = "extraction-response/0.4.0"


def create_openai_responses_client(
    settings: ModelProviderSettings,
    *,
    client_factory: Callable[..., Any] | None = None,
) -> Any:
    """Build a client only for an explicitly configured endpoint."""
    if settings.base_url is None:
        raise ProviderConfigurationError(
            f"Provider '{settings.provider}' requires MODEL_BASE_URL for "
            f"the {ADAPTER_NAME} adapter."
        )
    if client_factory is None:
        try:
            from openai import OpenAI
        except ImportError as error:
            raise ProviderTransportUnavailableError(
                "The OpenAI-compatible transport SDK is not installed; "
                "install project dependencies before live extraction."
            ) from error
        client_factory = OpenAI
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


class OpenAIResponsesEvidenceExtractor:
    """Extract candidates through the OpenAI Responses API contract."""

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
                "image_detail": IMAGE_DETAIL,
                "structured_output": "pydantic_json_schema",
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
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": build_extraction_request(
                    requested_facts=requested_facts,
                    evidence_bundle=evidence_bundle,
                    evidence_documents=evidence_documents,
                ),
            }
        ]
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
                    "type": "input_text",
                    "text": (
                        "The next image is admitted evidence "
                        f"document_id={document.document_id}, "
                        f"file_name={document.file_name}, page_number=1."
                    ),
                }
            )
            content.append(
                {
                    "type": "input_image",
                    "image_url": image_data_url(
                        evidence_file.file_path,
                        evidence_file.media_type,
                    ),
                    "detail": IMAGE_DETAIL,
                }
            )

        provider_response_model = extraction_response_model_for(
            requested_facts
        )
        try:
            response = self.client.responses.parse(
                model=self.settings.model,
                instructions=EXTRACTION_INSTRUCTIONS,
                input=[
                    {
                        "role": "user",
                        "content": content,
                    }
                ],
                text_format=provider_response_model,
            )
        except ValidationError as error:
            summary = format_pydantic_validation_summary(
                error,
                settings=self.settings,
            )
            raise InvalidProviderResponseError(
                provider_prefix(self.settings)
                + " response was rejected by the untrusted extraction "
                "schema.\n"
                "Validation errors:\n"
                f"{summary}"
            ) from error
        except Exception as error:
            raise_provider_invocation_error(
                operation="evidence extraction",
                error=error,
                settings=self.settings,
                adapter_name=ADAPTER_NAME,
                contract_description=(
                    "Responses API, multimodal, or structured-output "
                    "contract"
                ),
            )

        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise InvalidProviderResponseError(
                provider_prefix(self.settings)
                + " returned no structured extraction response; no "
                "fallback was attempted."
            )
        try:
            extraction_response = (
                parsed
                if isinstance(parsed, ExtractionResponse)
                else ExtractionResponse.model_validate(parsed)
            )
        except ValidationError as error:
            summary = format_pydantic_validation_summary(
                error,
                settings=self.settings,
            )
            raw_response = getattr(response, "output_text", None)
            if not isinstance(raw_response, str):
                raw_response = json.dumps(parsed, default=str)
            artifact_path = capture_temporary_raw_response(
                raw_response=raw_response,
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
            candidates=extraction_response.candidates,
            requested_facts=requested_facts,
        )
        if coverage_summary is not None:
            raw_response = getattr(response, "output_text", None)
            if not isinstance(raw_response, str):
                raw_response = json.dumps(parsed, default=str)
            artifact_path = capture_temporary_raw_response(
                raw_response=raw_response,
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
        return extraction_response.candidates


class OpenAIResponsesVisionProvider:
    """Create source descriptions through the same API-family adapter."""

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
        identity = ModelExecutionIdentity(
            provider=self.settings.provider,
            model=self.settings.model,
            adapter=ADAPTER_NAME,
            prompt_version=VISION_PROMPT_VERSION,
            schema_version=VISION_SCHEMA_VERSION,
            parameters={
                "image_detail": IMAGE_DETAIL,
                "structured_output": "pydantic_json_schema",
            },
        )
        return identity.canonical_json()

    def describe_image(self, *, file_path: Path) -> str:
        media_type = media_type_for_image(file_path)
        try:
            response = self.client.responses.parse(
                model=self.settings.model,
                instructions=VISION_OBSERVATION_INSTRUCTIONS,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "Create the source-preserving structured "
                                    "observations for this evidence image."
                                ),
                            },
                            {
                                "type": "input_image",
                                "image_url": image_data_url(
                                    file_path,
                                    media_type,
                                ),
                                "detail": IMAGE_DETAIL,
                            },
                        ],
                    }
                ],
                text_format=VisionObservationResult,
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
        except Exception as error:
            raise_provider_invocation_error(
                operation="image description",
                error=error,
                settings=self.settings,
                adapter_name=ADAPTER_NAME,
                contract_description=(
                    "Responses API multimodal and structured-output "
                    "contract"
                ),
            )

        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise InvalidProviderResponseError(
                provider_prefix(self.settings)
                + " image conversion returned no structured "
                "observations; no unstructured fallback was attempted."
            )
        try:
            observations = (
                parsed
                if isinstance(parsed, VisionObservationResult)
                else VisionObservationResult.model_validate(parsed)
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
