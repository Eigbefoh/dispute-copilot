"""Provider-neutral structured observations for image conversion."""

from __future__ import annotations

import json
import re

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
)

VISION_PROMPT_VERSION = "source-image-observations/1.0.0"
VISION_SCHEMA_VERSION = "vision-observation-result/1.0.0"

_PROHIBITED_PIXEL_INFERENCES = (
    re.compile(r"\bdefect[- ]free\b", re.IGNORECASE),
    re.compile(r"\b(?:does not|doesn't|cannot|can't|no)\s+wobbl", re.IGNORECASE),
    re.compile(r"\bno\s+hidden\s+(?:damage|defects?)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:fully|completely|exactly)?\s*matches?\s+"
        r"(?:the\s+)?(?:listing|order|specifications?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcustomer\s+(?:completed|refused)\b.*\breturn\b",
        re.IGNORECASE,
    ),
)


class VisionObservationResult(BaseModel):
    """Separate written claims, visible pixels, and view limitations."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
    )

    written_text_observations: tuple[
        StrictStr, ...
    ] = Field(max_length=100)
    visible_pixel_observations: tuple[
        StrictStr, ...
    ] = Field(max_length=100)
    limitations: tuple[StrictStr, ...] = Field(
        min_length=1,
        max_length=100,
    )

    @field_validator(
        "written_text_observations",
        "visible_pixel_observations",
        "limitations",
    )
    @classmethod
    def observations_are_bounded_and_unique(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        for value in values:
            if not value.strip():
                raise ValueError("vision observations cannot be blank")
            if value != value.strip():
                raise ValueError(
                    "vision observations cannot contain surrounding "
                    "whitespace"
                )
            if len(value) > 2000:
                raise ValueError(
                    "vision observations cannot exceed 2000 characters"
                )
        if len(values) != len(set(values)):
            raise ValueError("vision observations must be unique")
        return values

    @field_validator("visible_pixel_observations")
    @classmethod
    def pixels_do_not_claim_hidden_or_comparative_conclusions(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        for value in values:
            if any(
                pattern.search(value)
                for pattern in _PROHIBITED_PIXEL_INFERENCES
            ):
                raise ValueError(
                    "visible pixel observations cannot claim hidden "
                    "condition, operation, complete product matching, "
                    "or customer return behaviour"
                )
        return values


def render_vision_observations(
    result: VisionObservationResult,
) -> str:
    """Render one stable canonical page text without losing channel labels."""
    payload = {
        "written_text_observations": list(
            result.written_text_observations
        ),
        "visible_pixel_observations": list(
            result.visible_pixel_observations
        ),
        "limitations": list(result.limitations),
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
