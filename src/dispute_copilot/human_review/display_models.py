"""Immutable contracts for honest, source-grounded evidence display."""

from __future__ import annotations

from enum import StrEnum
from hashlib import sha256
import json
from math import isfinite
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dispute_copilot.analyst_review.models import DisplayFactValue

DISPLAY_TARGET_SCHEMA_VERSION = "1.0.0"


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class EvidenceType(StrEnum):
    CASE_METADATA = "CASE_METADATA"
    NATIVE_TEXT_ASSERTION = "NATIVE_TEXT_ASSERTION"
    OCR_TEXT_ASSERTION = "OCR_TEXT_ASSERTION"
    VISUAL_MODEL_OBSERVATION = "VISUAL_MODEL_OBSERVATION"
    DETERMINISTIC_CONCLUSION = "DETERMINISTIC_CONCLUSION"
    EVIDENCE_GAP = "EVIDENCE_GAP"


class HighlightStatus(StrEnum):
    EXACT_REGION_AVAILABLE = "EXACT_REGION_AVAILABLE"
    DERIVED_NATIVE_TEXT_REGION = "DERIVED_NATIVE_TEXT_REGION"
    PAGE_ONLY = "PAGE_ONLY"
    WHOLE_IMAGE_CONTEXT = "WHOLE_IMAGE_CONTEXT"
    REGION_UNAVAILABLE = "REGION_UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CoordinateSpace(StrEnum):
    NORMALIZED_0_TO_1 = "NORMALIZED_0_TO_1"
    PDF_POINTS = "PDF_POINTS"
    PIXELS = "PIXELS"


class RegionSource(StrEnum):
    CAPTURED_DURING_EXTRACTION = "CAPTURED_DURING_EXTRACTION"
    DERIVED_FROM_NATIVE_PDF_TEXT = "DERIVED_FROM_NATIVE_PDF_TEXT"


class RegionPrecision(StrEnum):
    WORD = "WORD"
    LINE = "LINE"
    BLOCK = "BLOCK"
    OBJECT = "OBJECT"


class PageGeometry(StrictFrozenModel):
    """Display-oriented page geometry with a top-left origin."""

    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation_degrees: int = Field(default=0)

    @model_validator(mode="after")
    def geometry_is_finite_and_supported(self) -> "PageGeometry":
        if not isfinite(self.width) or not isfinite(self.height):
            raise ValueError("page geometry must be finite")
        if self.rotation_degrees not in {0, 90, 180, 270}:
            raise ValueError(
                "rotation_degrees must be 0, 90, 180, or 270"
            )
        return self


class BoundingBox(StrictFrozenModel):
    """One grounded top-left-origin region in the declared coordinate space."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float
    region_source: RegionSource
    region_precision: RegionPrecision

    @model_validator(mode="after")
    def coordinates_are_finite_and_ordered(self) -> "BoundingBox":
        coordinates = (self.x_min, self.y_min, self.x_max, self.y_max)
        if any(not isfinite(value) for value in coordinates):
            raise ValueError("bounding-box coordinates must be finite")
        if any(value < 0 for value in coordinates):
            raise ValueError("bounding-box coordinates cannot be negative")
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError(
                "bounding-box maximums must be greater than minimums"
            )
        return self


class EvidenceDisplayLineage(StrictFrozenModel):
    case_run_id: UUID
    policy_selection_id: UUID
    fact_resolution_id: UUID
    evaluation_id: UUID
    recommendation_id: UUID
    review_package_id: UUID


class EvidenceDisplayTarget(StrictFrozenModel):
    """Complete validated presentation instruction for one analyst click."""

    display_target_id: str = Field(
        pattern=r"^display-[0-9a-f]{64}$"
    )
    review_package_id: UUID
    case_id: str = Field(min_length=1)
    requirement_id: str | None
    fact_id: str | None
    gap_id: str | None
    source_document_id: UUID | None
    source_file_name: str | None
    source_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    page_number: int | None = Field(default=None, ge=1)
    page_count: int | None = Field(default=None, ge=1)
    label: str = Field(min_length=1)
    evidence_type: EvidenceType
    extracted_or_admitted_value: DisplayFactValue | None
    supporting_excerpt: str | None
    highlight_status: HighlightStatus
    coordinate_space: CoordinateSpace | None
    page_geometry: PageGeometry | None
    bounding_boxes: tuple[BoundingBox, ...]
    inspected_source_summaries: tuple[str, ...]
    analyst_message: str = Field(min_length=1)
    lineage: EvidenceDisplayLineage
    schema_version: str = Field(
        default=DISPLAY_TARGET_SCHEMA_VERSION,
        pattern=r"^\d+\.\d+\.\d+$",
    )
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_display_capability(self) -> "EvidenceDisplayTarget":
        is_source_target = self.evidence_type in {
            EvidenceType.NATIVE_TEXT_ASSERTION,
            EvidenceType.OCR_TEXT_ASSERTION,
            EvidenceType.VISUAL_MODEL_OBSERVATION,
        }
        source_values = (
            self.source_document_id,
            self.source_file_name,
            self.source_sha256,
            self.page_number,
            self.page_count,
            self.page_geometry,
        )
        if is_source_target and any(value is None for value in source_values):
            raise ValueError(
                "evidence display requires source, page, hash, and geometry"
            )
        if not is_source_target and any(
            value is not None for value in source_values
        ):
            raise ValueError(
                "non-source display targets cannot claim source location"
            )
        if (
            self.page_number is not None
            and self.page_count is not None
            and self.page_number > self.page_count
        ):
            raise ValueError("page_number exceeds verified page_count")

        precise = self.highlight_status in {
            HighlightStatus.EXACT_REGION_AVAILABLE,
            HighlightStatus.DERIVED_NATIVE_TEXT_REGION,
        }
        if precise and not self.bounding_boxes:
            raise ValueError(
                "a precise highlight requires at least one bounding box"
            )
        if not precise and self.bounding_boxes:
            raise ValueError(
                "bounding boxes are forbidden for a non-precise highlight"
            )
        if bool(self.bounding_boxes) != (self.coordinate_space is not None):
            raise ValueError(
                "coordinate_space is required exactly when boxes exist"
            )
        if self.bounding_boxes and self.page_geometry is None:
            raise ValueError("bounding boxes require page geometry")
        self._validate_box_bounds()

        if self.evidence_type is EvidenceType.EVIDENCE_GAP:
            if self.gap_id is None:
                raise ValueError("evidence-gap targets require gap_id")
            if self.highlight_status is not HighlightStatus.NOT_APPLICABLE:
                raise ValueError(
                    "evidence gaps cannot claim a highlight location"
                )
        elif self.gap_id is not None:
            raise ValueError("gap_id is reserved for evidence-gap targets")

        if (
            self.evidence_type
            is EvidenceType.VISUAL_MODEL_OBSERVATION
            and not self.bounding_boxes
            and self.highlight_status
            not in {
                HighlightStatus.WHOLE_IMAGE_CONTEXT,
                HighlightStatus.REGION_UNAVAILABLE,
            }
        ):
            raise ValueError(
                "visual observations without persisted coordinates must "
                "use whole-image or unavailable-region display"
            )
        expected_hash = calculate_display_target_content_hash(self)
        if self.content_hash != expected_hash:
            raise ValueError(
                "content_hash does not match evidence display target"
            )
        expected_id = f"display-{expected_hash}"
        if self.display_target_id != expected_id:
            raise ValueError(
                "display_target_id is not derived from target content"
            )
        return self

    def _validate_box_bounds(self) -> None:
        if not self.bounding_boxes:
            return
        assert self.coordinate_space is not None
        assert self.page_geometry is not None
        if self.coordinate_space is CoordinateSpace.NORMALIZED_0_TO_1:
            x_bound = 1.0
            y_bound = 1.0
        else:
            x_bound = self.page_geometry.width
            y_bound = self.page_geometry.height
        for box in self.bounding_boxes:
            if box.x_max > x_bound or box.y_max > y_bound:
                raise ValueError(
                    "bounding box falls outside declared coordinate bounds"
                )
            if (
                self.highlight_status
                is HighlightStatus.DERIVED_NATIVE_TEXT_REGION
                and box.region_source
                is not RegionSource.DERIVED_FROM_NATIVE_PDF_TEXT
            ):
                raise ValueError(
                    "derived highlight requires derived native-text regions"
                )
            if (
                self.highlight_status
                is HighlightStatus.EXACT_REGION_AVAILABLE
                and box.region_source
                is not RegionSource.CAPTURED_DURING_EXTRACTION
            ):
                raise ValueError(
                    "exact highlight requires extraction-captured regions"
                )


def create_evidence_display_target(
    **fields: Any,
) -> EvidenceDisplayTarget:
    """Construct a target whose identity and hash derive from its content."""
    draft = EvidenceDisplayTarget.model_construct(
        **fields,
        display_target_id="display-" + "0" * 64,
        content_hash="0" * 64,
    )
    content_hash = calculate_display_target_content_hash(draft)
    return EvidenceDisplayTarget(
        **fields,
        display_target_id=f"display-{content_hash}",
        content_hash=content_hash,
    )


def calculate_display_target_content_hash(
    target: EvidenceDisplayTarget,
) -> str:
    payload = target.model_dump(
        mode="json",
        exclude={"display_target_id", "content_hash"},
    )
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
