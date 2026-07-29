"""Secret-safe diagnostics for rejected untrusted provider responses."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from dispute_copilot.config import ModelProviderSettings
from dispute_copilot.extraction.evidence_extraction_models import (
    ExtractionCandidate,
)
from dispute_copilot.extraction.openai_adapter_common import (
    sanitise_provider_text,
)
from dispute_copilot.policy.fact_models import FactDefinition

MAX_VALIDATION_ERRORS = 25
MAX_ERROR_MESSAGE_LENGTH = 300


@dataclass(frozen=True)
class ProviderResponseDiagnostics:
    """Explicit controls for temporary raw-response capture."""

    capture_raw_response: bool = False
    temporary_directory: Path | None = None


def format_pydantic_validation_summary(
    error: ValidationError,
    *,
    settings: ModelProviderSettings,
) -> str:
    """Render field path, error type, and message without raw values."""
    rendered: list[str] = []
    errors = _without_derivative_length_errors(error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ))
    for item in errors[:MAX_VALIDATION_ERRORS]:
        path = _format_location(item.get("loc", ()))
        error_type = str(item.get("type", "validation_error"))
        message = sanitise_provider_text(
            str(item.get("msg", "validation failed")),
            settings=settings,
        )[:MAX_ERROR_MESSAGE_LENGTH]
        rendered.append(f"{path} [{error_type}]: {message}")
    omitted = len(errors) - len(rendered)
    if omitted > 0:
        rendered.append(
            f"response [truncated]: {omitted} additional validation "
            "error(s) omitted"
        )
    return "\n".join(rendered)


def format_candidate_coverage_summary(
    *,
    candidates: tuple[ExtractionCandidate, ...],
    requested_facts: tuple[FactDefinition, ...],
) -> str | None:
    """Describe missing, unrequested, or duplicate fact candidates."""
    requested_names = tuple(
        definition.fact_name for definition in requested_facts
    )
    candidate_names = tuple(
        candidate.fact_name for candidate in candidates
    )
    errors: list[str] = []
    seen: set[str] = set()
    duplicates: set[str] = set()
    for name in candidate_names:
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    for name in sorted(duplicates):
        errors.append(f'facts: duplicate returned fact "{name}"')
    for name in requested_names:
        if name not in seen:
            errors.append(f'facts: missing requested fact "{name}"')
    requested_set = set(requested_names)
    for name in candidate_names:
        if name not in requested_set:
            errors.append(f'facts: unrequested returned fact "{name}"')
    return "\n".join(errors) if errors else None


def capture_temporary_raw_response(
    *,
    raw_response: str,
    settings: ModelProviderSettings,
    adapter_name: str,
    diagnostics: ProviderResponseDiagnostics,
) -> Path | None:
    """Write a redacted debug artifact outside application persistence."""
    if not diagnostics.capture_raw_response:
        return None
    sanitised_response = sanitise_provider_text(
        raw_response,
        settings=settings,
        truncate=False,
    )
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": settings.provider,
        "model": settings.model,
        "adapter": adapter_name,
        "raw_provider_response": sanitised_response,
    }
    directory = diagnostics.temporary_directory
    try:
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            prefix="dispute-copilot-provider-response-",
            suffix=".json",
            dir=directory,
            text=True,
        )
        try:
            with os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
            ) as artifact:
                json.dump(payload, artifact, indent=2, sort_keys=True)
                artifact.write("\n")
        except Exception:
            Path(raw_path).unlink(missing_ok=True)
            raise
        path = Path(raw_path)
        path.chmod(0o600)
        return path
    except OSError:
        return None


def debug_artifact_note(path: Path | None) -> str:
    if path is None:
        return ""
    return (
        "\nRaw provider response captured in explicit debug mode: "
        f"{path}\n"
        "The temporary artifact may contain evidence-derived content; "
        "review locally and delete it when finished."
    )


def _format_location(location: object) -> str:
    if not isinstance(location, (tuple, list)) or not location:
        return "response"
    return ".".join(str(component) for component in location)


def _without_derivative_length_errors(
    errors: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Remove parent too_short noise caused by rejected child items."""
    filtered: list[dict[str, object]] = []
    locations = [
        tuple(item.get("loc", ()))
        for item in errors
    ]
    for item, location in zip(errors, locations, strict=True):
        if item.get("type") == "too_short" and any(
            len(other) > len(location)
            and other[: len(location)] == location
            for other in locations
        ):
            continue
        filtered.append(item)
    return filtered
