"""Immutable contracts admitted to deterministic requirement evaluation."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from math import isfinite
from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

FactValue: TypeAlias = str | int | Decimal | bool | date | datetime


class StrictFrozenModel(BaseModel):
    """Strict immutable base for the fact-resolution boundary."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class CaseMetadataProvenance(StrictFrozenModel):
    """Exact deterministic field used from an immutable case run."""

    source_type: Literal["CASE_METADATA"]
    case_run_id: UUID
    field_path: str = Field(min_length=1)


class EvidenceTextProvenance(StrictFrozenModel):
    """Page-grounded writing from native, OCR, or visual conversion."""

    source_type: Literal["EVIDENCE_TEXT_ASSERTION"]
    document_id: UUID
    file_name: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    extraction_method: Literal["NATIVE_TEXT", "OCR", "VISION"]
    supporting_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def supporting_text_is_not_blank(self) -> "EvidenceTextProvenance":
        if not self.supporting_text.strip():
            raise ValueError("supporting_text cannot be blank")
        return self


class VisualObservationProvenance(StrictFrozenModel):
    """Source-grounded observation from a visual evidence page."""

    source_type: Literal["EVIDENCE_VISUAL_OBSERVATION"]
    document_id: UUID
    file_name: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    observation: str = Field(min_length=1)

    @model_validator(mode="after")
    def observation_is_not_blank(self) -> "VisualObservationProvenance":
        if not self.observation.strip():
            raise ValueError("observation cannot be blank")
        return self


FactProvenance: TypeAlias = Annotated[
    CaseMetadataProvenance
    | EvidenceTextProvenance
    | VisualObservationProvenance,
    Field(discriminator="source_type"),
]


class ResolvedValue(StrictFrozenModel):
    """One candidate value inseparably linked to its supporting sources."""

    value: FactValue
    value_type: Literal[
        "STRING",
        "DATE",
        "DATETIME",
        "INTEGER",
        "DECIMAL",
        "BOOLEAN",
        "ADDRESS",
    ] | None = None
    provenance: tuple[FactProvenance, ...] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def restore_persisted_runtime_type(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        value_type = data.get("value_type")
        value = data.get("value")
        restored = value
        if isinstance(value, str):
            if value_type == "DATE":
                restored = date.fromisoformat(value)
            elif value_type == "DATETIME":
                restored = datetime.fromisoformat(value)
            elif value_type == "DECIMAL":
                restored = Decimal(value)
        provenance = data.get("provenance")
        if isinstance(provenance, list):
            provenance = tuple(provenance)
        return {
            **data,
            "value": restored,
            "provenance": provenance,
        }

    @model_validator(mode="after")
    def provenance_records_are_unique(self) -> "ResolvedValue":
        serialized = [
            provenance.model_dump_json()
            for provenance in self.provenance
        ]
        if len(serialized) != len(set(serialized)):
            raise ValueError("provenance records must be unique")
        return self


class ResolvedFact(StrictFrozenModel):
    """One explicit resolution outcome for one requested policy fact."""

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
    resolved_values: tuple[ResolvedValue, ...]
    resolver_type: Literal[
        "CASE_METADATA",
        "EVIDENCE_EXTRACTION",
    ]
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def status_and_candidates_are_consistent(self) -> "ResolvedFact":
        value_count = len(self.resolved_values)
        if self.status == "FOUND" and value_count == 0:
            raise ValueError("FOUND requires at least one resolved value")
        if self.status == "NOT_FOUND" and value_count != 0:
            raise ValueError("NOT_FOUND requires no resolved values")
        if self.status == "AMBIGUOUS" and value_count == 0:
            raise ValueError(
                "AMBIGUOUS requires at least one candidate value"
            )
        if self.status == "CONFLICTING" and value_count < 2:
            raise ValueError(
                "CONFLICTING requires at least two candidate values"
            )

        value_keys = [
            (type(resolved.value), resolved.value)
            for resolved in self.resolved_values
        ]
        if len(value_keys) != len(set(value_keys)):
            raise ValueError(
                "candidate values must be distinct; combine supporting "
                "provenance for the same value"
            )

        if self.confidence is not None and not isfinite(self.confidence):
            raise ValueError("confidence must be finite")
        if self.resolver_type == "CASE_METADATA":
            if self.confidence is not None:
                raise ValueError(
                    "deterministic metadata facts require confidence=None"
                )
            if any(
                not isinstance(
                    provenance,
                    CaseMetadataProvenance,
                )
                for value in self.resolved_values
                for provenance in value.provenance
            ):
                raise ValueError(
                    "metadata facts require metadata provenance"
                )
        elif any(
            isinstance(provenance, CaseMetadataProvenance)
            for value in self.resolved_values
            for provenance in value.provenance
        ):
            raise ValueError(
                "evidence facts cannot use metadata provenance"
            )
        return self


class FactResolutionResult(StrictFrozenModel):
    """Complete validated boundary presented to future evaluation."""

    resolution_id: UUID
    run_id: UUID
    selection_id: UUID
    policy_id: str = Field(min_length=1)
    rulebook_version: str = Field(min_length=1)
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_facts: tuple[str, ...] = Field(min_length=1)
    facts: tuple[ResolvedFact, ...] = Field(min_length=1)
    resolver_version: str = Field(min_length=1)
    created_at: datetime
    status: Literal["READY_FOR_EVALUATION"]
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def result_is_complete_ordered_and_aware(
        self,
    ) -> "FactResolutionResult":
        if len(self.requested_facts) != len(set(self.requested_facts)):
            raise ValueError("requested_facts must be unique")
        fact_names = [fact.fact_name for fact in self.facts]
        if len(fact_names) != len(set(fact_names)):
            raise ValueError("resolved fact names must be unique")
        if tuple(fact_names) != self.requested_facts:
            raise ValueError(
                "facts must exactly match requested_facts in order"
            )
        if (
            self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise ValueError("created_at must be timezone-aware")
        expected_hash = calculate_fact_resolution_content_hash(self)
        if self.content_hash != expected_hash:
            raise ValueError(
                "content_hash does not match fact-resolution content"
            )
        return self


def calculate_fact_resolution_content_hash(
    result: FactResolutionResult,
) -> str:
    """Hash semantic resolution content, excluding identity and time."""
    payload = result.model_dump(
        mode="json",
        exclude={
            "resolution_id",
            "created_at",
            "content_hash",
        },
    )
    for fact in payload["facts"]:
        for value in fact["resolved_values"]:
            if value.get("value_type") is None:
                value.pop("value_type", None)
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
