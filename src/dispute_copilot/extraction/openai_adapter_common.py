"""Shared, provider-neutral helpers for OpenAI API-family adapters."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from dispute_copilot.config import ModelProviderSettings
from dispute_copilot.extraction.errors import (
    EvidenceCandidateValidationError,
    ProviderEndpointError,
    ProviderRejectionError,
    StructuredOutputUnsupportedError,
)
from dispute_copilot.extraction.models import (
    EvidenceBundle,
    EvidenceDocument,
)
from dispute_copilot.policy.fact_models import FactDefinition

IMAGE_DETAIL = "high"

EXTRACTION_INSTRUCTIONS = """\
Role: Extract source-grounded evidence facts for a payment dispute.

Goal:
- Return exactly one result for every requested fact.
- Extract observations and document assertions only.

Status rules:
- FOUND: the evidence supports at least one value.
- NOT_FOUND: the supplied evidence does not establish the fact.
- AMBIGUOUS: at least one plausible value is visible but cannot be
  established reliably.
- CONFLICTING: the evidence supports at least two distinct competing
  values.

Evidence rules:
- Answer only the requested facts.
- Every value must reference a supplied document ID, file name, and page.
- Every reference must choose exactly one evidence_type: TEXT or VISUAL.
- A TEXT reference requires supporting_text and must not contain
  visual_observation.
- A VISUAL reference requires visual_observation and must not contain
  supporting_text.
- For TEXT evidence, supporting_text must be copied exclusively and verbatim
  from the supplied page's canonical_text field.
- Do not independently transcribe text from image pixels, paraphrase it,
  correct it, or synthesize a quotation. If visible writing is absent from
  canonical_text, do not cite that writing as TEXT evidence.
- Written words printed inside a PNG are still TEXT evidence. Use the page's
  supplied extraction_method, including VISION when that is how the image page
  was converted.
- Visual facts require a concise observation of visible non-text pixels, not
  a quotation or an inference from a caption.
- Written captions inside an image are source assertions. Do not use
  caption text to establish a visual fact about the depicted object.
- If visible writing and visible pixels both support a value, create two
  separate references. Never put both channel fields in one reference.
- Treat merchant and carrier text as source assertions, not proven truth.
- Do not add facts noticed incidentally.

The following examples show shape only. Always use the actual supplied
document ID, filename, page, and exact source content.

Valid TEXT reference example:
{
  "evidence_type": "TEXT",
  "document_id": "00000000-0000-0000-0000-000000000000",
  "file_name": "delivery_photo.png",
  "page_number": 1,
  "extraction_method": "VISION",
  "supporting_text": "Carlton Armchair (Sage) — delivered 18 Apr 2025"
}

Valid VISUAL reference example:
{
  "evidence_type": "VISUAL",
  "document_id": "00000000-0000-0000-0000-000000000000",
  "file_name": "delivery_photo.png",
  "page_number": 1,
  "extraction_method": "VISION",
  "visual_observation": "A front-facing sage armchair-like object is visible."
}

Prohibited conclusions:
- Do not decide whether Visa 13.3 or any requirement is satisfied.
- Do not compare the delivery image with the product listing.
- Do not infer that the exact ordered product was delivered.
- Do not infer that an item is defect-free from one front-view image.
- Do not decide whether a customer is truthful.
- Do not recommend REPRESENT, ACCEPT, or MANUAL_REVIEW.
- Do not return workflow status or a policy decision.
"""


def raise_provider_invocation_error(
    *,
    operation: str,
    error: Exception,
    settings: ModelProviderSettings,
    adapter_name: str,
    contract_description: str,
) -> None:
    """Translate transport failures without exposing credentials."""
    status = getattr(error, "status_code", None)
    provider_type, provider_message = _provider_error_details(error)
    provider_message = sanitise_provider_text(
        provider_message,
        settings=settings,
    )
    details = (
        f"{provider_prefix(settings)}, "
        f"adapter='{adapter_name}', "
        f"endpoint='{settings.base_url}', "
        f"http_status={status if status is not None else 'unknown'}, "
        f"provider_type={provider_type}, "
        f"message={provider_message}"
    )

    error_name = type(error).__name__.lower()
    if status is None and (
        "connection" in error_name
        or "timeout" in error_name
        or "connect" in provider_message.lower()
        or "timed out" in provider_message.lower()
    ):
        raise ProviderEndpointError(
            f"Configured provider endpoint could not be reached; {details}"
        ) from error

    compatibility_terms = (
        "response_format",
        "responses.parse",
        "json_schema",
        "structured output",
        "structured_output",
        "text_format",
        "text.format",
        "input_image",
        "image_url",
        "multimodal",
    )
    if any(
        term in provider_message.lower()
        for term in compatibility_terms
    ):
        raise StructuredOutputUnsupportedError(
            "The configured provider/model did not accept the required "
            f"{contract_description}; no provider/model substitution or "
            "unstructured fallback was attempted. "
            f"{details}"
        ) from error

    raise ProviderRejectionError(
        f"Configured provider rejected {operation}; {details}"
    ) from error


def provider_prefix(settings: ModelProviderSettings) -> str:
    return (
        f"Provider '{settings.provider}' using model "
        f"'{settings.model}'"
    )


def build_extraction_request(
    *,
    requested_facts: tuple[FactDefinition, ...],
    evidence_documents: tuple[EvidenceDocument, ...],
) -> str:
    facts = [
        {
            "fact_name": definition.fact_name,
            "description": definition.description,
            "value_type": definition.value_type,
            "source_type": definition.source_type,
            "cardinality": definition.cardinality,
        }
        for definition in requested_facts
    ]
    documents = [
        {
            "document_id": str(document.document_id),
            "file_name": document.file_name,
            "media_type": document.media_type,
            "pages": [
                {
                    "page_number": page.page_number,
                    "extraction_method": page.extraction_method.value,
                    "canonical_text": page.text,
                }
                for page in document.pages
            ],
        }
        for document in evidence_documents
    ]
    return (
        "Requested facts, in required response order:\n"
        f"{json.dumps(facts, indent=2)}\n\n"
        "Converted evidence documents:\n"
        f"{json.dumps(documents, indent=2)}"
    )


def validate_bundle_documents(
    *,
    evidence_bundle: EvidenceBundle,
    evidence_documents: tuple[EvidenceDocument, ...],
) -> None:
    if len(evidence_bundle.evidence_files) != len(evidence_documents):
        raise EvidenceCandidateValidationError(
            "evidence documents do not cover the complete bundle"
        )
    for evidence_file, document in zip(
        evidence_bundle.evidence_files,
        evidence_documents,
        strict=True,
    ):
        if (
            evidence_file.file_name != document.file_name
            or evidence_file.sha256 != document.source_sha256
            or evidence_file.media_type != document.media_type
        ):
            raise EvidenceCandidateValidationError(
                "evidence document does not match its admitted file: "
                f"{document.file_name}"
            )


def image_data_url(path: Path, media_type: str) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def media_type_for_image(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    raise EvidenceCandidateValidationError(
        f"unsupported visual evidence type: {path.name}"
    )


def _provider_error_details(error: Exception) -> tuple[str, str]:
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        error_body = body.get("error", body)
        if isinstance(error_body, dict):
            provider_type = str(
                error_body.get("type") or type(error).__name__
            )
            message = str(
                error_body.get("message")
                or getattr(error, "message", "")
                or "provider rejected the request"
            )
            return provider_type, message
    return (
        type(error).__name__,
        str(getattr(error, "message", "") or str(error)),
    )


def sanitise_provider_text(
    message: str,
    *,
    settings: ModelProviderSettings,
    truncate: bool = True,
) -> str:
    """Redact configured and labelled credentials from provider text."""
    sanitised = message.replace(
        settings.api_key.get_secret_value(),
        "[REDACTED]",
    )
    patterns = (
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
        r"(?i)(bearer\s+)[^\s,;]+",
        r"(?i)((?:api[_-]?key|model_api_key)\s*[:=]\s*)[^\s,;]+",
    )
    for pattern in patterns:
        sanitised = re.sub(pattern, r"\1[REDACTED]", sanitised)
    if truncate:
        sanitised = " ".join(sanitised.split())[:500]
    return sanitised or "provider returned an empty response"
