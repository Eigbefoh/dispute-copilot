"""Explicit lookup of frozen recommendation mappings."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from dispute_copilot.recommendation.errors import MappingContractError
from dispute_copilot.recommendation.mapping_models import (
    RecommendationMappingManifest,
)

DEFAULT_MAPPING_ROOT = Path("data/recommendation-mappings")
SUPPORTED_MAPPING_SCHEMA_VERSION = "1.0.0"


def load_recommendation_mapping(
    *,
    mapping_key: str,
    mapping_version: str,
    mapping_root: Path = DEFAULT_MAPPING_ROOT,
) -> RecommendationMappingManifest:
    """Resolve one explicit key/version with no fallback."""
    if not mapping_key or not mapping_version:
        raise MappingContractError(
            "Mapping key and version must be explicit"
        )
    path = (
        mapping_root
        / mapping_key
        / mapping_version
        / "manifest.json"
    )
    try:
        content = path.read_bytes()
    except OSError as error:
        raise MappingContractError(
            f"Recommendation mapping is unavailable: {path}"
        ) from error
    try:
        manifest = RecommendationMappingManifest.model_validate_json(
            content
        )
    except ValidationError as error:
        raise MappingContractError(
            f"Recommendation mapping failed validation: {error}"
        ) from error
    if (
        manifest.mapping_key != mapping_key
        or manifest.mapping_version != mapping_version
    ):
        raise MappingContractError(
            "Resolved mapping identity does not match explicit request"
        )
    if (
        manifest.mapping_schema_version
        != SUPPORTED_MAPPING_SCHEMA_VERSION
    ):
        raise MappingContractError(
            "Unsupported recommendation mapping schema version: "
            f"{manifest.mapping_schema_version}"
        )
    return manifest
