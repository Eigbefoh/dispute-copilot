"""Versioned declarative predicate contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

PredicateScalar: TypeAlias = str | int | Decimal | bool | date | datetime


class PredicateOperator(StrEnum):
    ALL_OF = "all_of"
    ANY_OF = "any_of"
    NOT = "not"
    FACT_IS_FOUND = "fact_is_found"
    FACT_EQUALS = "fact_equals"
    FACT_IN = "fact_in"
    NORMALIZED_STRING_EQUALS = "normalized_string_equals"
    NORMALIZED_STRING_IN = "normalized_string_in"
    MATERIAL_ADDRESS_MATCH = "material_address_match"
    DATE_ON_OR_BEFORE = "date_on_or_before"
    DATE_ON_OR_AFTER = "date_on_or_after"
    DAYS_BETWEEN_AT_MOST = "days_between_at_most"
    NUMERIC_AT_LEAST = "numeric_at_least"
    NUMERIC_AT_MOST = "numeric_at_most"
    HOURS_BEFORE_LESS_THAN = "hours_before_less_than"
    UNRESOLVED_POLICY_CONDITION = "unresolved_policy_condition"


class ValueMatch(StrEnum):
    ANY = "ANY"
    ALL = "ALL"


class MismatchResult(StrEnum):
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class AggregationMode(StrEnum):
    ALL_APPLICABLE = "ALL_APPLICABLE"
    ANY_TWO = "ANY_TWO"


class PolicyAggregation(BaseModel):
    """Declarative policy-level requirement aggregation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: AggregationMode = AggregationMode.ALL_APPLICABLE


class PredicateNode(BaseModel):
    """One allow-listed AST node; no executable code is accepted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operator: PredicateOperator
    predicates: tuple["PredicateNode", ...] = ()
    fact_name: str | None = None
    other_fact_name: str | None = None
    expected_value: PredicateScalar | None = None
    expected_values: tuple[PredicateScalar, ...] = ()
    maximum_days: int | None = Field(default=None, ge=0)
    numeric_bound: Decimal | None = None
    numeric_bound_fact_name: str | None = None
    basis_code: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    value_match: ValueMatch = ValueMatch.ANY
    mismatch_result: MismatchResult = MismatchResult.FALSE

    @model_validator(mode="after")
    def shape_matches_operator(self) -> "PredicateNode":
        allowed_fields: dict[PredicateOperator, set[str]] = {
            PredicateOperator.ALL_OF: {"operator", "predicates"},
            PredicateOperator.ANY_OF: {"operator", "predicates"},
            PredicateOperator.NOT: {"operator", "predicates"},
            PredicateOperator.FACT_IS_FOUND: {
                "operator",
                "fact_name",
            },
            PredicateOperator.FACT_EQUALS: {
                "operator",
                "fact_name",
                "other_fact_name",
                "expected_value",
                "value_match",
            },
            PredicateOperator.FACT_IN: {
                "operator",
                "fact_name",
                "expected_values",
                "value_match",
            },
            PredicateOperator.NORMALIZED_STRING_EQUALS: {
                "operator",
                "fact_name",
                "other_fact_name",
                "expected_value",
                "value_match",
            },
            PredicateOperator.NORMALIZED_STRING_IN: {
                "operator",
                "fact_name",
                "expected_values",
                "value_match",
            },
            PredicateOperator.MATERIAL_ADDRESS_MATCH: {
                "operator",
                "fact_name",
                "other_fact_name",
                "value_match",
                "mismatch_result",
            },
            PredicateOperator.DATE_ON_OR_BEFORE: {
                "operator",
                "fact_name",
                "other_fact_name",
                "expected_value",
                "value_match",
            },
            PredicateOperator.DATE_ON_OR_AFTER: {
                "operator",
                "fact_name",
                "other_fact_name",
                "expected_value",
                "value_match",
            },
            PredicateOperator.DAYS_BETWEEN_AT_MOST: {
                "operator",
                "fact_name",
                "other_fact_name",
                "maximum_days",
                "value_match",
            },
            PredicateOperator.NUMERIC_AT_LEAST: {
                "operator",
                "fact_name",
                "numeric_bound",
                "value_match",
            },
            PredicateOperator.NUMERIC_AT_MOST: {
                "operator",
                "fact_name",
                "numeric_bound",
                "value_match",
            },
            PredicateOperator.HOURS_BEFORE_LESS_THAN: {
                "operator",
                "fact_name",
                "other_fact_name",
                "numeric_bound_fact_name",
                "value_match",
            },
            PredicateOperator.UNRESOLVED_POLICY_CONDITION: {
                "operator",
                "basis_code",
            },
        }
        unexpected = self.model_fields_set - allowed_fields[self.operator]
        if unexpected:
            raise ValueError(
                f"{self.operator} received unsupported fields: "
                f"{sorted(unexpected)}"
            )
        composite = self.operator in {
            PredicateOperator.ALL_OF,
            PredicateOperator.ANY_OF,
            PredicateOperator.NOT,
        }
        if composite:
            if not self.predicates:
                raise ValueError(
                    f"{self.operator} requires child predicates"
                )
            if (
                self.operator is PredicateOperator.NOT
                and len(self.predicates) != 1
            ):
                raise ValueError("not requires exactly one child predicate")
            if any(
                value is not None
                for value in (
                    self.fact_name,
                    self.other_fact_name,
                    self.expected_value,
                    self.maximum_days,
                    self.numeric_bound,
                    self.numeric_bound_fact_name,
                    self.basis_code,
                )
            ) or self.expected_values:
                raise ValueError(
                    f"{self.operator} accepts only child predicates"
                )
            return self

        if self.predicates:
            raise ValueError(
                f"{self.operator} cannot contain child predicates"
            )
        if self.operator is PredicateOperator.UNRESOLVED_POLICY_CONDITION:
            if not self.basis_code:
                raise ValueError(
                    "unresolved_policy_condition requires basis_code"
                )
            return self
        if not self.fact_name:
            raise ValueError(f"{self.operator} requires fact_name")

        if self.operator is PredicateOperator.FACT_IS_FOUND:
            self._require_no_operands()
        elif self.operator is PredicateOperator.FACT_EQUALS:
            if (self.expected_value is None) == (
                self.other_fact_name is None
            ):
                raise ValueError(
                    "fact_equals requires exactly one of expected_value "
                    "or other_fact_name"
                )
        elif self.operator is PredicateOperator.FACT_IN:
            if not self.expected_values:
                raise ValueError("fact_in requires expected_values")
        elif self.operator is PredicateOperator.NORMALIZED_STRING_EQUALS:
            if (self.expected_value is None) == (
                self.other_fact_name is None
            ):
                raise ValueError(
                    "normalized_string_equals requires exactly one of "
                    "expected_value or other_fact_name"
                )
        elif self.operator is PredicateOperator.NORMALIZED_STRING_IN:
            if not self.expected_values:
                raise ValueError(
                    "normalized_string_in requires expected_values"
                )
        elif self.operator is PredicateOperator.MATERIAL_ADDRESS_MATCH:
            if self.other_fact_name is None:
                raise ValueError(
                    "material_address_match requires other_fact_name"
                )
        elif self.operator in {
            PredicateOperator.DATE_ON_OR_BEFORE,
            PredicateOperator.DATE_ON_OR_AFTER,
        }:
            if (self.expected_value is None) == (
                self.other_fact_name is None
            ):
                raise ValueError(
                    f"{self.operator} requires exactly one date operand"
                )
        elif self.operator is PredicateOperator.DAYS_BETWEEN_AT_MOST:
            if self.other_fact_name is None or self.maximum_days is None:
                raise ValueError(
                    "days_between_at_most requires other_fact_name and "
                    "maximum_days"
                )
        elif self.operator in {
            PredicateOperator.NUMERIC_AT_LEAST,
            PredicateOperator.NUMERIC_AT_MOST,
        }:
            if self.numeric_bound is None:
                raise ValueError(
                    f"{self.operator} requires numeric_bound"
                )
        elif self.operator is PredicateOperator.HOURS_BEFORE_LESS_THAN:
            if (
                self.other_fact_name is None
                or self.numeric_bound_fact_name is None
            ):
                raise ValueError(
                    "hours_before_less_than requires other_fact_name and "
                    "numeric_bound_fact_name"
                )
        return self

    def _require_no_operands(self) -> None:
        if any(
            value is not None
            for value in (
                self.other_fact_name,
                self.expected_value,
                self.maximum_days,
                self.numeric_bound,
                self.numeric_bound_fact_name,
                self.basis_code,
            )
        ) or self.expected_values:
            raise ValueError("fact_is_found accepts no operands")


class RequirementPredicate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str = Field(min_length=1)
    applicability_predicate: PredicateNode | None = None
    predicate: PredicateNode


class PolicyPredicates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str = Field(min_length=1)
    aggregation: PolicyAggregation = PolicyAggregation()
    requirements: tuple[RequirementPredicate, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def requirement_ids_are_unique(self) -> "PolicyPredicates":
        ids = tuple(item.requirement_id for item in self.requirements)
        if len(ids) != len(set(ids)):
            raise ValueError("predicate requirement IDs must be unique")
        return self


class PredicateManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rulebook_version: str = Field(min_length=1)
    predicate_schema_version: str = Field(min_length=1)
    policies: tuple[PolicyPredicates, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def policy_ids_are_unique(self) -> "PredicateManifest":
        ids = tuple(policy.policy_id for policy in self.policies)
        if len(ids) != len(set(ids)):
            raise ValueError("predicate policy IDs must be unique")
        return self
