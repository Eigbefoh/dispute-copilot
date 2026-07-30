"""Source-derived semantic contracts that precede executable policy code."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SourceStructure(StrEnum):
    UNIVERSAL = "UNIVERSAL"
    ALTERNATIVE = "ALTERNATIVE"
    CONDITIONAL = "CONDITIONAL"
    EXCEPTION = "EXCEPTION"


class ExampleTreatment(StrEnum):
    NONE = "NONE"
    ILLUSTRATIVE = "ILLUSTRATIVE"
    SOURCE_CONDITIONS = "SOURCE_CONDITIONS"


class SemanticScenario(StrictFrozenModel):
    scenario_kind: Literal[
        "POSITIVE",
        "NEGATIVE",
        "MISSING",
        "CONFLICTING",
        "PARTIAL",
    ]
    expected_truth: Literal["TRUE", "FALSE", "UNKNOWN"]
    description: str = Field(min_length=1)


class RequirementSemanticContract(StrictFrozenModel):
    requirement_id: str = Field(min_length=1)
    source_page: int = Field(ge=1)
    source_text: str = Field(min_length=1)
    source_structure: SourceStructure
    applicability: str | None = None
    branches: tuple[str, ...] = Field(min_length=1)
    examples: tuple[str, ...] = ()
    example_treatment: ExampleTreatment
    semantic_expression: str = Field(min_length=1)
    configuration_blockers: tuple[str, ...] = ()
    scenarios: tuple[SemanticScenario, ...] = Field(min_length=5)

    @model_validator(mode="after")
    def scenario_matrix_is_complete(self) -> "RequirementSemanticContract":
        kinds = tuple(item.scenario_kind for item in self.scenarios)
        expected_kinds = (
            "POSITIVE",
            "NEGATIVE",
            "MISSING",
            "CONFLICTING",
            "PARTIAL",
        )
        if kinds != expected_kinds:
            raise ValueError(
                "semantic scenarios must be ordered positive, negative, "
                "missing, conflicting, partial"
            )
        expected_truth = {
            "POSITIVE": "TRUE",
            "NEGATIVE": "FALSE",
            "MISSING": "UNKNOWN",
            "CONFLICTING": "UNKNOWN",
            "PARTIAL": "UNKNOWN",
        }
        for scenario in self.scenarios:
            if scenario.expected_truth != expected_truth[
                scenario.scenario_kind
            ]:
                raise ValueError(
                    f"{scenario.scenario_kind} has invalid expected truth"
                )
        if (
            self.source_structure is SourceStructure.ALTERNATIVE
            and len(self.branches) < 2
        ):
            raise ValueError(
                "alternative requirements need at least two branches"
            )
        return self


class PolicySemanticContract(StrictFrozenModel):
    policy_id: str = Field(min_length=1)
    scheme: Literal["visa", "mastercard"]
    reason_code: str = Field(min_length=1)
    source_aggregation: Literal[
        "ALL_APPLICABLE",
        "ANY_TWO",
        "EXCEPTION_DEFAULT",
    ]
    requirements: tuple[RequirementSemanticContract, ...] = Field(
        min_length=1
    )

    @model_validator(mode="after")
    def requirement_ids_are_unique(self) -> "PolicySemanticContract":
        ids = tuple(item.requirement_id for item in self.requirements)
        if len(ids) != len(set(ids)):
            raise ValueError("requirement IDs must be unique per policy")
        return self


class PolicySemanticContractManifest(StrictFrozenModel):
    contract_schema_version: Literal[
        "source-policy-contracts/1.0.0"
    ]
    rulebook_version: Literal["take-home-v4"]
    source_document: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policies: tuple[PolicySemanticContract, ...] = Field(min_length=1)
    unresolved_policy_owner_decisions: tuple[str, ...]

    @model_validator(mode="after")
    def policy_keys_are_unique(self) -> "PolicySemanticContractManifest":
        keys = tuple(
            (item.scheme, item.reason_code) for item in self.policies
        )
        ids = tuple(item.policy_id for item in self.policies)
        if len(keys) != len(set(keys)) or len(ids) != len(set(ids)):
            raise ValueError("policy keys and IDs must be unique")
        return self
