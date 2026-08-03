"""Chat Completions transport for schema-constrained extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dispute_copilot.config import ModelProviderSettings
from dispute_copilot.extraction.errors import (
    EvidenceCandidateValidationError,
    InvalidProviderResponseError,
    ProviderRejectionError,
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

ADAPTER_NAME = "openai_chat_completions"
PROMPT_VERSION = "lean-evidence-extraction/0.5.0"
RESPONSE_SCHEMA_VERSION = "extraction-response/0.4.0"
MAX_TOKENS = 4096
TEMPERATURE = 0


class OpenAIChatCompletionsEvidenceExtractor:
    """Extract candidates through Chat Completions JSON Schema mode."""

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
                "max_tokens": MAX_TOKENS,
                "structured_output": (
                    "chat_completions_json_schema"
                ),
                "strict": True,
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
        content: list[dict[str, Any]] = [
            {
                "type": "text",
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
                    "type": "text",
                    "text": (
                        "The next image is admitted evidence "
                        f"document_id={document.document_id}, "
                        f"file_name={document.file_name}, page_number=1."
                    ),
                }
            )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_data_url(
                            evidence_file.file_path,
                            evidence_file.media_type,
                        ),
                        "detail": IMAGE_DETAIL,
                    },
                }
            )

        provider_response_model = extraction_response_model_for(
            requested_facts
        )
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": provider_response_model.__name__,
                "strict": True,
                "schema": provider_response_model.model_json_schema(),
            },
        }
        try:
            response = self.client.chat.completions.create(
                model=self.settings.model,
                messages=[
                    {
                        "role": "system",
                        "content": EXTRACTION_INSTRUCTIONS,
                    },
                    {
                        "role": "user",
                        "content": content,
                    },
                ],
                response_format=response_format,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
            )
        except Exception as error:
            raise_provider_invocation_error(
                operation="evidence extraction",
                error=error,
                settings=self.settings,
                adapter_name=ADAPTER_NAME,
                contract_description=(
                    "Chat Completions multimodal and JSON-schema "
                    "contract"
                ),
            )

        message = _first_response_message(response, self.settings)
        refusal = getattr(message, "refusal", None)
        if refusal:
            raise ProviderRejectionError(
                provider_prefix(self.settings)
                + " refused evidence extraction; no fallback was "
                "attempted."
            )
        content_text = getattr(message, "content", None)
        if not isinstance(content_text, str) or not content_text.strip():
            raise InvalidProviderResponseError(
                provider_prefix(self.settings)
                + " returned no structured extraction content; no "
                "fallback was attempted."
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


class OpenAIChatCompletionsVisionProvider:
    """Convert one image through strict Chat Completions JSON Schema."""

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
    def provider_version(self) -> str:
        identity = ModelExecutionIdentity(
            provider=self.settings.provider,
            model=self.settings.model,
            adapter=ADAPTER_NAME,
            prompt_version=VISION_PROMPT_VERSION,
            schema_version=VISION_SCHEMA_VERSION,
            parameters={
                "image_detail": IMAGE_DETAIL,
                "max_tokens": MAX_TOKENS,
                "structured_output": (
                    "chat_completions_json_schema"
                ),
                "strict": True,
                "temperature": TEMPERATURE,
            },
        )
        return identity.canonical_json()

    def describe_image(self, *, file_path: Path) -> str:
        media_type = media_type_for_image(file_path)
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": VisionObservationResult.__name__,
                "strict": True,
                "schema": VisionObservationResult.model_json_schema(),
            },
        }
        try:
            response = self.client.chat.completions.create(
                model=self.settings.model,
                messages=[
                    {
                        "role": "system",
                        "content": VISION_OBSERVATION_INSTRUCTIONS,
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Create the source-preserving "
                                    "structured observations for this "
                                    "evidence image."
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": image_data_url(
                                        file_path,
                                        media_type,
                                    ),
                                    "detail": IMAGE_DETAIL,
                                },
                            },
                        ],
                    },
                ],
                response_format=response_format,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
            )
        except Exception as error:
            raise_provider_invocation_error(
                operation="image description",
                error=error,
                settings=self.settings,
                adapter_name=ADAPTER_NAME,
                contract_description=(
                    "Chat Completions multimodal and JSON-schema "
                    "vision contract"
                ),
            )

        message = _first_response_message(response, self.settings)
        refusal = getattr(message, "refusal", None)
        if refusal:
            raise ProviderRejectionError(
                provider_prefix(self.settings)
                + " refused image conversion; no fallback was "
                "attempted."
            )
        content_text = getattr(message, "content", None)
        if not isinstance(content_text, str) or not content_text.strip():
            raise InvalidProviderResponseError(
                provider_prefix(self.settings)
                + " returned no structured image observations; no "
                "fallback was attempted."
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
            artifact_path = capture_temporary_raw_response(
                raw_response=content_text,
                settings=self.settings,
                adapter_name=ADAPTER_NAME,
                diagnostics=self.diagnostics,
            )
            raise InvalidProviderResponseError(
                provider_prefix(self.settings)
                + " image observations were rejected by the "
                "untrusted vision schema.\n"
                "Validation errors:\n"
                f"{summary}"
                f"{debug_artifact_note(artifact_path)}"
            ) from error
        return render_vision_observations(observations)


def _first_response_message(
    response: Any,
    settings: ModelProviderSettings,
) -> Any:
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise InvalidProviderResponseError(
            provider_prefix(settings)
            + " returned no Chat Completions choices."
        )
    message = getattr(choices[0], "message", None)
    if message is None:
        raise InvalidProviderResponseError(
            provider_prefix(settings)
            + " returned a choice without a message."
        )
    return message
