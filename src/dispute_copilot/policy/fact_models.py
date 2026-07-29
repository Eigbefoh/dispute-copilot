"""Immutable contracts for reviewed policy fact semantics."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FactValueType = Literal[
    "STRING",
    "DATE",
    "DATETIME",
    "INTEGER",
    "DECIMAL",
    "BOOLEAN",
    "ADDRESS",
]
FactSourceType = Literal[
    "EVIDENCE_TEXT_ASSERTION",
    "EVIDENCE_VISUAL_OBSERVATION",
    "CASE_METADATA",
]
FactCardinality = Literal["SINGLE", "MULTIPLE"]
FactClassification = Literal[
    "OBSERVABLE_ATOMIC_VALUE",
    "DOCUMENT_ASSERTION",
    "VISUAL_OBSERVATION",
    "CASE_METADATA",
    "COMPARISON",
    "POLICY_CONCLUSION",
    "RECOMMENDATION",
]
FactReviewDecision = Literal["KEEP", "RENAME", "SPLIT", "REMOVE"]


class FactDefinition(BaseModel):
    """One stable, source-aware value requested by a policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_name: str = Field(
        min_length=1,
        pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$",
    )
    description: str = Field(min_length=1)
    value_type: FactValueType
    source_type: FactSourceType
    cardinality: FactCardinality = "SINGLE"

    @model_validator(mode="after")
    def description_is_trimmed(self) -> "FactDefinition":
        if self.description != self.description.strip():
            raise ValueError("fact description must be trimmed")
        return self


class FactCatalogue(BaseModel):
    """The authoritative fact vocabulary for one rulebook version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rulebook_version: str = Field(min_length=1)
    facts: tuple[FactDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def fact_names_are_unique_and_sorted(self) -> "FactCatalogue":
        names = [fact.fact_name for fact in self.facts]
        if len(names) != len(set(names)):
            raise ValueError("fact_name values must be unique")
        if names != sorted(names):
            raise ValueError("facts must be sorted by fact_name")
        return self


class FactReviewRecord(BaseModel):
    """One completed human decision for an original policy fact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    original_fact_name: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    value_type: FactValueType
    source_type: FactSourceType
    cardinality: FactCardinality
    policies_using_it: tuple[str, ...] = Field(min_length=1)
    classification: FactClassification
    decision: FactReviewDecision
    replacement_fact_names: tuple[str, ...]
    review_notes: str = Field(min_length=1)


class FactReview(BaseModel):
    """The complete review provenance for an earlier fact vocabulary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    records: tuple[FactReviewRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def original_fact_names_are_unique_and_sorted(self) -> "FactReview":
        names = [record.original_fact_name for record in self.records]
        if len(names) != len(set(names)):
            raise ValueError(
                "original_fact_name values must be unique"
            )
        if names != sorted(names):
            raise ValueError(
                "review records must be sorted by original_fact_name"
            )
        return self
