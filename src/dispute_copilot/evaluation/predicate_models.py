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
    FACT_IS_FOUND = "fact_is_found"
    FACT_EQUALS = "fact_equals"
    FACT_IN = "fact_in"
    DATE_ON_OR_BEFORE = "date_on_or_before"
    DATE_ON_OR_AFTER = "date_on_or_after"
    DAYS_BETWEEN_AT_MOST = "days_between_at_most"
    NUMERIC_AT_LEAST = "numeric_at_least"
    NUMERIC_AT_MOST = "numeric_at_most"


class ValueMatch(StrEnum):
    ANY = "ANY"
    ALL = "ALL"


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
    value_match: ValueMatch = ValueMatch.ANY

    @model_validator(mode="after")
    def shape_matches_operator(self) -> "PredicateNode":
        allowed_fields: dict[PredicateOperator, set[str]] = {
            PredicateOperator.ALL_OF: {"operator", "predicates"},
            PredicateOperator.ANY_OF: {"operator", "predicates"},
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
        }
        if composite:
            if not self.predicates:
                raise ValueError(
                    f"{self.operator} requires child predicates"
                )
            if any(
                value is not None
                for value in (
                    self.fact_name,
                    self.other_fact_name,
                    self.expected_value,
                    self.maximum_days,
                    self.numeric_bound,
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
        return self

    def _require_no_operands(self) -> None:
        if any(
            value is not None
            for value in (
                self.other_fact_name,
                self.expected_value,
                self.maximum_days,
                self.numeric_bound,
            )
        ) or self.expected_values:
            raise ValueError("fact_is_found accepts no operands")


class RequirementPredicate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str = Field(min_length=1)
    predicate: PredicateNode


class PolicyPredicates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str = Field(min_length=1)
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
