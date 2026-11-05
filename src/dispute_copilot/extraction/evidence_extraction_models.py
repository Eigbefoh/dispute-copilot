"""Strict untrusted response models for lean evidence extraction."""

from __future__ import annotations

from math import isfinite
from functools import reduce
from operator import or_
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    create_model,
    field_validator,
    model_validator,
)

from dispute_copilot.policy.fact_models import FactDefinition

CandidateRawValue = StrictStr | StrictInt | StrictBool


class UntrustedExtractionModel(BaseModel):
    """Forbid provider fields that are not part of the extraction contract."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
    )


class ExtractionEvidenceReferenceBase(UntrustedExtractionModel):
    """Fields shared by both provider-facing evidence channels."""

    document_id: UUID
    file_name: str = Field(min_length=1)
    page_number: int = Field(ge=1)


class TextExtractionEvidenceReference(
    ExtractionEvidenceReferenceBase,
):
    """Exact written text, including writing visible inside an image."""

    evidence_type: Literal["TEXT"]
    extraction_method: Literal["NATIVE_TEXT", "OCR", "VISION"]
    supporting_text: str = Field(
        min_length=1,
        description=(
            "One verbatim, contiguous substring copied exclusively from "
            "the referenced page's supplied canonical_text; never "
            "independently transcribed from pixels, paraphrased, "
            "corrected, or joined with a separate passage. Use multiple "
            "references when one value needs non-contiguous support."
        ),
    )

    @field_validator("supporting_text")
    @classmethod
    def supporting_text_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("supporting_text cannot be blank")
        return value


class VisualExtractionEvidenceReference(
    ExtractionEvidenceReferenceBase,
):
    """Observation derived only from visible non-text pixels."""

    evidence_type: Literal["VISUAL"]
    extraction_method: Literal["VISION"]
    visual_observation: str = Field(min_length=1)

    @field_validator("visual_observation")
    @classmethod
    def visual_observation_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("visual_observation cannot be blank")
        return value


ExtractionEvidenceReference: TypeAlias = Annotated[
    TextExtractionEvidenceReference
    | VisualExtractionEvidenceReference,
    Field(discriminator="evidence_type"),
]


class ExtractionCandidateValue(UntrustedExtractionModel):
    """One untrusted raw value with all provider-proposed references."""

    raw_value: CandidateRawValue
    references: tuple[ExtractionEvidenceReference, ...] = Field(
        min_length=1
    )

    @model_validator(mode="after")
    def references_are_unique(self) -> "ExtractionCandidateValue":
        serialized = [
            reference.model_dump_json()
            for reference in self.references
        ]
        if len(serialized) != len(set(serialized)):
            raise ValueError("evidence references must be unique")
        return self


class ExtractionCandidate(UntrustedExtractionModel):
    """One untrusted model outcome for exactly one requested fact."""

    fact_name: str = Field(
        min_length=1,
        pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$",
    )
    status: Literal[
        "FOUND",
        "NOT_FOUND",
        "AMBIGUOUS",
        "CONFLICTING",
    ]
    values: tuple[ExtractionCandidateValue, ...]
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def status_matches_values(self) -> "ExtractionCandidate":
        """Apply the existing resolution-status semantics at ingress."""
        value_count = len(self.values)
        if self.status == "FOUND" and value_count == 0:
            raise ValueError("FOUND requires at least one value")
        if self.status == "NOT_FOUND" and value_count != 0:
            raise ValueError("NOT_FOUND requires no values")
        if self.status == "AMBIGUOUS" and value_count == 0:
            raise ValueError(
                "AMBIGUOUS requires at least one candidate value"
            )
        if self.status == "CONFLICTING" and value_count < 2:
            raise ValueError(
                "CONFLICTING requires at least two candidate values"
            )
        if self.confidence is not None and not isfinite(self.confidence):
            raise ValueError("confidence must be finite")
        return self


class ExtractionResponse(UntrustedExtractionModel):
    """Complete structured response returned by the extraction model."""

    candidates: tuple[ExtractionCandidate, ...]


def extraction_response_model_for(
    requested_facts: tuple[FactDefinition, ...],
) -> type[BaseModel]:
    """Build a provider schema whose raw values match catalogue types.

    The returned model constrains provider generation only. Provider output
    is still parsed through ``ExtractionResponse`` and then through the
    deterministic catalogue-aware adapter before admission.
    """
    if not requested_facts:
        return ExtractionResponse

    raw_types: dict[str, Any] = {
        "STRING": StrictStr,
        "ADDRESS": StrictStr,
        "DATE": StrictStr,
        "DATETIME": StrictStr,
        "DECIMAL": StrictStr,
        "INTEGER": StrictInt,
        "BOOLEAN": StrictBool,
    }
    candidate_models: list[type[BaseModel]] = []
    for index, definition in enumerate(requested_facts):
        raw_type = raw_types[definition.value_type]
        value_model = create_model(
            f"RequestedValue{index}",
            __base__=UntrustedExtractionModel,
            raw_value=(raw_type, ...),
            references=(
                tuple[ExtractionEvidenceReference, ...],
                Field(min_length=1),
            ),
        )
        fact_literal = Literal[definition.fact_name]
        candidate_model = create_model(
            f"RequestedCandidate{index}",
            __base__=UntrustedExtractionModel,
            fact_name=(fact_literal, ...),
            status=(
                Literal[
                    "FOUND",
                    "NOT_FOUND",
                    "AMBIGUOUS",
                    "CONFLICTING",
                ],
                ...,
            ),
            values=(tuple[value_model, ...], ...),
            confidence=(float | None, Field(default=None, ge=0, le=1)),
        )
        candidate_models.append(candidate_model)

    candidate_union = reduce(or_, candidate_models)
    return create_model(
        "RequestedExtractionResponse",
        __base__=UntrustedExtractionModel,
        candidates=(tuple[candidate_union, ...], ...),
    )
