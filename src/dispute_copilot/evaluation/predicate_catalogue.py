"""Load and validate versioned executable requirement predicates."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from dispute_copilot.evaluation.errors import PredicateContractError
from dispute_copilot.evaluation.predicate_models import (
    PolicyPredicates,
    PredicateManifest,
    PredicateNode,
    PredicateOperator,
)
from dispute_copilot.policy.fact_models import FactCatalogue, FactDefinition
from dispute_copilot.policy.rule_models import PolicyRecord

DEFAULT_RULEBOOK_ROOT = Path("data/rulebooks")


def load_predicate_manifest(
    *,
    rulebook_version: str,
    rulebook_root: Path = DEFAULT_RULEBOOK_ROOT,
) -> tuple[PredicateManifest, str]:
    """Load only versioned policy data; never access evidence."""
    path = (
        rulebook_root
        / rulebook_version
        / "evaluation"
        / "predicates.json"
    )
    try:
        content = path.read_bytes()
    except OSError as error:
        raise PredicateContractError(
            f"Predicate manifest is unavailable: {path}"
        ) from error
    try:
        manifest = PredicateManifest.model_validate_json(content)
    except ValidationError as error:
        raise PredicateContractError(
            f"Predicate manifest failed schema validation: {error}"
        ) from error
    if manifest.rulebook_version != rulebook_version:
        raise PredicateContractError(
            "Predicate manifest rulebook version does not match request"
        )
    return manifest, sha256(content).hexdigest()


def select_and_validate_policy_predicates(
    *,
    manifest: PredicateManifest,
    policy: PolicyRecord,
    catalogue: FactCatalogue,
) -> PolicyPredicates:
    """Require exact policy/requirement/fact/type coverage."""
    matches = tuple(
        item
        for item in manifest.policies
        if item.policy_id == policy.policy_id
    )
    if len(matches) != 1:
        raise PredicateContractError(
            f"Expected exactly one predicate definition for "
            f"{policy.policy_id}; found {len(matches)}"
        )
    selected = matches[0]
    if (
        manifest.predicate_schema_version.endswith("/2.0.0")
        and "aggregation" not in selected.model_fields_set
    ):
        raise PredicateContractError(
            "Predicate schema 2.0.0 requires an explicit policy "
            "aggregation rule"
        )
    policy_ids = tuple(
        requirement.requirement_id
        for requirement in policy.requirements
    )
    predicate_ids = tuple(
        requirement.requirement_id
        for requirement in selected.requirements
    )
    if predicate_ids != policy_ids:
        raise PredicateContractError(
            "Predicate requirement IDs/order do not match frozen policy"
        )

    definitions = {
        definition.fact_name: definition
        for definition in catalogue.facts
    }
    policy_facts = {
        fact_name
        for requirement in policy.requirements
        for fact_name in requirement.required_facts
    }
    for requirement, executable in zip(
        policy.requirements,
        selected.requirements,
        strict=True,
    ):
        referenced = _validate_node(
            executable.predicate,
            definitions,
        )
        if executable.applicability_predicate is not None:
            referenced |= _validate_node(
                executable.applicability_predicate,
                definitions,
            )
        if referenced != set(requirement.required_facts):
            raise PredicateContractError(
                f"{requirement.requirement_id} predicate fact coverage "
                f"differs; expected={sorted(requirement.required_facts)}, "
                f"actual={sorted(referenced)}"
            )
        if not referenced <= policy_facts:
            raise PredicateContractError(
                f"{requirement.requirement_id} references a fact outside "
                "the frozen selected policy"
            )
    return selected


def _validate_node(
    node: PredicateNode,
    definitions: dict[str, FactDefinition],
) -> set[str]:
    if node.operator in {
        PredicateOperator.ALL_OF,
        PredicateOperator.ANY_OF,
        PredicateOperator.NOT,
    }:
        referenced: set[str] = set()
        for child in node.predicates:
            referenced |= _validate_node(child, definitions)
        return referenced

    if node.operator is PredicateOperator.UNRESOLVED_POLICY_CONDITION:
        return set()

    assert node.fact_name is not None
    definition = _definition(node.fact_name, definitions)
    referenced = {node.fact_name}
    other_definition: FactDefinition | None = None
    if node.other_fact_name is not None:
        other_definition = _definition(
            node.other_fact_name,
            definitions,
        )
        referenced.add(node.other_fact_name)
    numeric_bound_definition: FactDefinition | None = None
    if node.numeric_bound_fact_name is not None:
        numeric_bound_definition = _definition(
            node.numeric_bound_fact_name,
            definitions,
        )
        referenced.add(node.numeric_bound_fact_name)

    if node.operator is PredicateOperator.FACT_EQUALS:
        if other_definition is not None:
            if not _compatible_types(definition, other_definition):
                raise PredicateContractError(
                    "fact_equals uses incompatible facts: "
                    f"{node.fact_name}/{node.other_fact_name}"
                )
        else:
            _require_literal_type(
                node.expected_value,
                definition,
                node.operator,
            )
    elif node.operator is PredicateOperator.FACT_IN:
        for value in node.expected_values:
            _require_literal_type(value, definition, node.operator)
    elif node.operator is PredicateOperator.NORMALIZED_STRING_EQUALS:
        if definition.value_type not in {"STRING", "ADDRESS"}:
            raise PredicateContractError(
                "normalized_string_equals requires a string fact"
            )
        if other_definition is not None:
            if other_definition.value_type not in {"STRING", "ADDRESS"}:
                raise PredicateContractError(
                    "normalized_string_equals requires string operands"
                )
        elif type(node.expected_value) is not str:
            raise PredicateContractError(
                "normalized_string_equals requires a string literal"
            )
    elif node.operator is PredicateOperator.NORMALIZED_STRING_IN:
        if definition.value_type not in {"STRING", "ADDRESS"}:
            raise PredicateContractError(
                "normalized_string_in requires a string fact"
            )
        if any(type(value) is not str for value in node.expected_values):
            raise PredicateContractError(
                "normalized_string_in requires string literals"
            )
    elif node.operator is PredicateOperator.MATERIAL_ADDRESS_MATCH:
        if other_definition is None:
            raise PredicateContractError(
                "material_address_match requires two registered facts"
            )
        if (
            definition.value_type not in {"STRING", "ADDRESS"}
            or other_definition.value_type not in {"STRING", "ADDRESS"}
        ):
            raise PredicateContractError(
                "material_address_match requires STRING or ADDRESS facts"
            )
    elif node.operator in {
        PredicateOperator.DATE_ON_OR_BEFORE,
        PredicateOperator.DATE_ON_OR_AFTER,
        PredicateOperator.DAYS_BETWEEN_AT_MOST,
    }:
        if definition.value_type not in {"DATE", "DATETIME"}:
            raise PredicateContractError(
                f"{node.operator} requires DATE or DATETIME fact"
            )
        if other_definition is not None:
            if other_definition.value_type not in {"DATE", "DATETIME"}:
                raise PredicateContractError(
                    f"{node.operator} requires date-compatible operands"
                )
        elif node.expected_value is not None:
            if not isinstance(node.expected_value, (date, datetime)):
                raise PredicateContractError(
                    f"{node.operator} requires a date literal"
                )
    elif node.operator in {
        PredicateOperator.NUMERIC_AT_LEAST,
        PredicateOperator.NUMERIC_AT_MOST,
    } and definition.value_type not in {"INTEGER", "DECIMAL"}:
        raise PredicateContractError(
            f"{node.operator} requires INTEGER or DECIMAL fact"
        )
    elif node.operator is PredicateOperator.HOURS_BEFORE_LESS_THAN:
        if definition.value_type != "DATETIME":
            raise PredicateContractError(
                "hours_before_less_than requires a DATETIME first fact"
            )
        if (
            other_definition is None
            or other_definition.value_type != "DATETIME"
        ):
            raise PredicateContractError(
                "hours_before_less_than requires a DATETIME second fact"
            )
        if (
            numeric_bound_definition is None
            or numeric_bound_definition.value_type
            not in {"INTEGER", "DECIMAL"}
        ):
            raise PredicateContractError(
                "hours_before_less_than requires a numeric bound fact"
            )
    return referenced


def _definition(
    fact_name: str,
    definitions: dict[str, FactDefinition],
) -> FactDefinition:
    definition = definitions.get(fact_name)
    if definition is None:
        raise PredicateContractError(
            f"Predicate references unknown fact '{fact_name}'"
        )
    return definition


def _compatible_types(
    left: FactDefinition,
    right: FactDefinition,
) -> bool:
    if left.value_type == right.value_type:
        return True
    return {left.value_type, right.value_type} <= {"STRING", "ADDRESS"}


def _require_literal_type(
    value: object,
    definition: FactDefinition,
    operator: PredicateOperator,
) -> None:
    types: dict[str, type] = {
        "STRING": str,
        "ADDRESS": str,
        "INTEGER": int,
        "DECIMAL": Decimal,
        "BOOLEAN": bool,
        "DATE": date,
        "DATETIME": datetime,
    }
    expected = types[definition.value_type]
    if type(value) is not expected:
        raise PredicateContractError(
            f"{operator} literal for {definition.fact_name} must be "
            f"{definition.value_type}"
        )
