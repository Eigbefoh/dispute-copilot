"""Explicit lookup of frozen analyst-review presentation rules."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from dispute_copilot.analyst_review.errors import PresentationRulesError
from dispute_copilot.analyst_review.presentation_models import (
    PresentationRulesManifest,
)

DEFAULT_PRESENTATION_RULES_ROOT = Path("data/presentation-rules")
SUPPORTED_PRESENTATION_SCHEMA_VERSION = "1.0.0"
DEFAULT_PRESENTATION_RULES_KEY = "analyst-review-package-v1"
DEFAULT_PRESENTATION_RULES_VERSION = "1.1.0"


def load_presentation_rules(
    *,
    presentation_rules_key: str,
    presentation_rules_version: str,
    presentation_rules_root: Path = DEFAULT_PRESENTATION_RULES_ROOT,
) -> PresentationRulesManifest:
    """Resolve one explicit presentation identity with no latest fallback."""
    if not presentation_rules_key or not presentation_rules_version:
        raise PresentationRulesError(
            "Presentation-rules key and version must be explicit"
        )
    path = (
        presentation_rules_root
        / presentation_rules_key
        / presentation_rules_version
        / "manifest.json"
    )
    try:
        content = path.read_bytes()
    except OSError as error:
        raise PresentationRulesError(
            f"Presentation rules are unavailable: {path}"
        ) from error
    try:
        manifest = PresentationRulesManifest.model_validate_json(content)
    except ValidationError as error:
        raise PresentationRulesError(
            f"Presentation rules failed validation: {error}"
        ) from error
    if (
        manifest.presentation_rules_key != presentation_rules_key
        or manifest.presentation_rules_version
        != presentation_rules_version
    ):
        raise PresentationRulesError(
            "Resolved presentation identity does not match explicit request"
        )
    if (
        manifest.presentation_schema_version
        != SUPPORTED_PRESENTATION_SCHEMA_VERSION
    ):
        raise PresentationRulesError(
            "Unsupported presentation schema version: "
            f"{manifest.presentation_schema_version}"
        )
    return manifest
