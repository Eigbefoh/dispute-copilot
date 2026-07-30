"""Allow-listed, three-valued predicate interpreter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import re
import unicodedata

from dispute_copilot.evaluation.errors import PredicateExecutionError
from dispute_copilot.evaluation.models import FactBinding, TruthValue
from dispute_copilot.evaluation.predicate_models import (
    PredicateNode,
    PredicateOperator,
    ValueMatch,
)
from dispute_copilot.extraction.fact_resolution_models import ResolvedFact


@dataclass(frozen=True)
class PredicateOutcome:
    result: TruthValue
    basis_codes: tuple[str, ...]
    bindings: tuple[FactBinding, ...]
    unresolved_facts: tuple[str, ...]


class PredicateInterpreter:
    """Evaluate declarative predicates without dynamic execution."""

    def evaluate(
        self,
        predicate: PredicateNode,
        facts: dict[str, ResolvedFact],
    ) -> PredicateOutcome:
        if predicate.operator is PredicateOperator.ALL_OF:
            return self._evaluate_all_of(predicate, facts)
        if predicate.operator is PredicateOperator.ANY_OF:
            return self._evaluate_any_of(predicate, facts)
        if predicate.operator is PredicateOperator.NOT:
            return self._evaluate_not(predicate, facts)
        if (
            predicate.operator
            is PredicateOperator.UNRESOLVED_POLICY_CONDITION
        ):
            assert predicate.basis_code is not None
            return PredicateOutcome(
                result=TruthValue.UNKNOWN,
                basis_codes=(predicate.basis_code,),
                bindings=(),
                unresolved_facts=(),
            )
        if (
            predicate.operator
            is PredicateOperator.HOURS_BEFORE_LESS_THAN
        ):
            return self._evaluate_hours_before_less_than(
                predicate,
                facts,
            )
        return self._evaluate_leaf(predicate, facts)

    def _evaluate_hours_before_less_than(
        self,
        predicate: PredicateNode,
        facts: dict[str, ResolvedFact],
    ) -> PredicateOutcome:
        assert predicate.fact_name is not None
        assert predicate.other_fact_name is not None
        assert predicate.numeric_bound_fact_name is not None
        names = (
            predicate.fact_name,
            predicate.other_fact_name,
            predicate.numeric_bound_fact_name,
        )
        resolved = []
        unresolved = []
        for name in names:
            fact = facts.get(name)
            if fact is None:
                raise PredicateExecutionError(
                    f"predicate references unavailable fact '{name}'"
                )
            resolved.append(fact)
            outcome = _unresolved_outcome(fact, _binding(fact))
            if outcome is not None:
                unresolved.append(outcome)
        if unresolved:
            return _combine(
                tuple(
                    (
                        _leaf_outcome(
                            TruthValue.TRUE,
                            (_binding(fact),),
                        )
                        if _unresolved_outcome(
                            fact,
                            _binding(fact),
                        )
                        is None
                        else _unresolved_outcome(
                            fact,
                            _binding(fact),
                        )
                    )
                    for fact in resolved
                ),
                result=TruthValue.UNKNOWN,
                primary_code="LATE_CANCELLATION_OPERAND_NOT_ESTABLISHED",
            )
        cancellations, services, windows = (
            tuple(value.value for value in fact.resolved_values)
            for fact in resolved
        )
        comparisons = tuple(
            _hours_before_less_than(cancellation, service, window)
            for cancellation in cancellations
            for service in services
            for window in windows
        )
        result = _match_bools(comparisons, predicate.value_match)
        return _leaf_outcome(
            result,
            tuple(_binding(fact) for fact in resolved),
        )

    def _evaluate_not(
        self,
        predicate: PredicateNode,
        facts: dict[str, ResolvedFact],
    ) -> PredicateOutcome:
        child = self.evaluate(predicate.predicates[0], facts)
        result = {
            TruthValue.TRUE: TruthValue.FALSE,
            TruthValue.FALSE: TruthValue.TRUE,
            TruthValue.UNKNOWN: TruthValue.UNKNOWN,
        }[child.result]
        return _combine(
            (child,),
            result=result,
            primary_code=(
                "PREDICATE_TRUE"
                if result is TruthValue.TRUE
                else "PREDICATE_FALSE"
                if result is TruthValue.FALSE
                else "NEGATED_PREDICATE_NOT_ESTABLISHED"
            ),
        )

    def _evaluate_all_of(
        self,
        predicate: PredicateNode,
        facts: dict[str, ResolvedFact],
    ) -> PredicateOutcome:
        children = tuple(
            self.evaluate(child, facts)
            for child in predicate.predicates
        )
        results = {child.result for child in children}
        if TruthValue.FALSE in results:
            result = TruthValue.FALSE
            code = "PREDICATE_FALSE"
        elif TruthValue.UNKNOWN in results:
            result = TruthValue.UNKNOWN
            code = "PARTIAL_ALL_OF"
        else:
            result = TruthValue.TRUE
            code = "PREDICATE_TRUE"
        return _combine(children, result=result, primary_code=code)

    def _evaluate_any_of(
        self,
        predicate: PredicateNode,
        facts: dict[str, ResolvedFact],
    ) -> PredicateOutcome:
        children = tuple(
            self.evaluate(child, facts)
            for child in predicate.predicates
        )
        results = {child.result for child in children}
        if TruthValue.TRUE in results:
            result = TruthValue.TRUE
            code = "PREDICATE_TRUE"
        elif TruthValue.UNKNOWN in results:
            result = TruthValue.UNKNOWN
            code = "NO_ANY_OF_BRANCH_ESTABLISHED"
        else:
            result = TruthValue.FALSE
            code = "PREDICATE_FALSE"
        return _combine(children, result=result, primary_code=code)

    def _evaluate_leaf(
        self,
        predicate: PredicateNode,
        facts: dict[str, ResolvedFact],
    ) -> PredicateOutcome:
        assert predicate.fact_name is not None
        fact = facts.get(predicate.fact_name)
        if fact is None:
            raise PredicateExecutionError(
                f"predicate references unavailable fact "
                f"'{predicate.fact_name}'"
            )
        primary = _binding(fact)
        unresolved = _unresolved_outcome(fact, primary)
        if unresolved is not None:
            return unresolved

        if predicate.operator is PredicateOperator.FACT_IS_FOUND:
            return _leaf_outcome(TruthValue.TRUE, (primary,))

        other: ResolvedFact | None = None
        other_binding: FactBinding | None = None
        if predicate.other_fact_name is not None:
            other = facts.get(predicate.other_fact_name)
            if other is None:
                raise PredicateExecutionError(
                    "predicate references unavailable fact "
                    f"'{predicate.other_fact_name}'"
                )
            other_binding = _binding(other)
            other_unresolved = _unresolved_outcome(
                other,
                other_binding,
            )
            if other_unresolved is not None:
                return _combine(
                    (
                        _leaf_outcome(TruthValue.TRUE, (primary,)),
                        other_unresolved,
                    ),
                    result=TruthValue.UNKNOWN,
                    primary_code=other_unresolved.basis_codes[0],
                )

        left_values = tuple(
            item.value for item in fact.resolved_values
        )
        bindings = (
            (primary, other_binding)
            if other_binding is not None
            else (primary,)
        )
        clean_bindings = tuple(
            binding for binding in bindings if binding is not None
        )

        if predicate.operator is PredicateOperator.FACT_EQUALS:
            right_values = (
                tuple(item.value for item in other.resolved_values)
                if other is not None
                else (predicate.expected_value,)
            )
            result = _compare_pairs(
                left_values,
                right_values,
                predicate.value_match,
                lambda left, right: (
                    type(left) is type(right) and left == right
                ),
            )
        elif predicate.operator is PredicateOperator.FACT_IN:
            expected = predicate.expected_values
            result = _match_bools(
                tuple(
                    any(
                        type(value) is type(candidate)
                        and value == candidate
                        for candidate in expected
                    )
                    for value in left_values
                ),
                predicate.value_match,
            )
        elif predicate.operator is PredicateOperator.NORMALIZED_STRING_EQUALS:
            right_values = (
                tuple(item.value for item in other.resolved_values)
                if other is not None
                else (predicate.expected_value,)
            )
            result = _compare_pairs(
                left_values,
                right_values,
                predicate.value_match,
                _normalized_string_equals,
            )
        elif predicate.operator is PredicateOperator.NORMALIZED_STRING_IN:
            expected = predicate.expected_values
            result = _match_bools(
                tuple(
                    any(
                        _normalized_string_equals(value, candidate)
                        for candidate in expected
                    )
                    for value in left_values
                ),
                predicate.value_match,
            )
        elif predicate.operator is PredicateOperator.MATERIAL_ADDRESS_MATCH:
            assert other is not None
            right_values = tuple(
                item.value for item in other.resolved_values
            )
            result = _compare_pairs(
                left_values,
                right_values,
                predicate.value_match,
                _material_address_matches,
            )
            if (
                result is TruthValue.FALSE
                and predicate.mismatch_result.value == "UNKNOWN"
            ):
                return PredicateOutcome(
                    result=TruthValue.UNKNOWN,
                    basis_codes=("MATERIAL_ADDRESS_ATTRIBUTION_CONFLICT",),
                    bindings=clean_bindings,
                    unresolved_facts=(),
                )
        elif predicate.operator in {
            PredicateOperator.DATE_ON_OR_BEFORE,
            PredicateOperator.DATE_ON_OR_AFTER,
        }:
            right_values = (
                tuple(item.value for item in other.resolved_values)
                if other is not None
                else (predicate.expected_value,)
            )
            compare = (
                lambda left, right: left <= right
                if predicate.operator
                is PredicateOperator.DATE_ON_OR_BEFORE
                else left >= right
            )
            result = _compare_pairs(
                left_values,
                right_values,
                predicate.value_match,
                lambda left, right: _date_compare(
                    left,
                    right,
                    compare,
                ),
            )
        elif (
            predicate.operator
            is PredicateOperator.DAYS_BETWEEN_AT_MOST
        ):
            assert other is not None
            assert predicate.maximum_days is not None
            right_values = tuple(
                item.value for item in other.resolved_values
            )
            result = _compare_pairs(
                left_values,
                right_values,
                predicate.value_match,
                lambda left, right: _days_between_at_most(
                    left,
                    right,
                    predicate.maximum_days,
                ),
            )
        elif predicate.operator in {
            PredicateOperator.NUMERIC_AT_LEAST,
            PredicateOperator.NUMERIC_AT_MOST,
        }:
            assert predicate.numeric_bound is not None
            bound = predicate.numeric_bound
            result = _match_bools(
                tuple(
                    _numeric_compare(
                        value,
                        bound,
                        at_least=(
                            predicate.operator
                            is PredicateOperator.NUMERIC_AT_LEAST
                        ),
                    )
                    for value in left_values
                ),
                predicate.value_match,
            )
        else:
            raise PredicateExecutionError(
                f"unsupported predicate operator: {predicate.operator}"
            )
        return _leaf_outcome(result, clean_bindings)


def _binding(fact: ResolvedFact) -> FactBinding:
    provenance = tuple(
        source
        for value in fact.resolved_values
        for source in value.provenance
    )
    return FactBinding(
        fact_name=fact.fact_name,
        fact_status=fact.status,
        value_indexes=tuple(range(len(fact.resolved_values))),
        provenance_references=provenance,
    )


def _unresolved_outcome(
    fact: ResolvedFact,
    binding: FactBinding,
) -> PredicateOutcome | None:
    codes = {
        "NOT_FOUND": "REQUIRED_FACT_NOT_FOUND",
        "AMBIGUOUS": "REQUIRED_FACT_AMBIGUOUS",
        "CONFLICTING": "REQUIRED_FACT_CONFLICTING",
    }
    code = codes.get(fact.status)
    if code is None:
        return None
    return PredicateOutcome(
        result=TruthValue.UNKNOWN,
        basis_codes=(code,),
        bindings=(binding,),
        unresolved_facts=(fact.fact_name,),
    )


def _leaf_outcome(
    result: TruthValue,
    bindings: tuple[FactBinding, ...],
) -> PredicateOutcome:
    code = (
        "PREDICATE_TRUE"
        if result is TruthValue.TRUE
        else "PREDICATE_FALSE"
    )
    return PredicateOutcome(
        result=result,
        basis_codes=(code,),
        bindings=bindings,
        unresolved_facts=(),
    )


def _combine(
    outcomes: tuple[PredicateOutcome, ...],
    *,
    result: TruthValue,
    primary_code: str,
) -> PredicateOutcome:
    codes = _ordered_unique(
        (
            primary_code,
            *(
                code
                for outcome in outcomes
                for code in outcome.basis_codes
                if code not in {"PREDICATE_TRUE", "PREDICATE_FALSE"}
            ),
        )
    )
    bindings_by_name: dict[str, FactBinding] = {}
    for outcome in outcomes:
        for binding in outcome.bindings:
            existing = bindings_by_name.get(binding.fact_name)
            if existing is None:
                bindings_by_name[binding.fact_name] = binding
                continue
            indexes = tuple(
                sorted(
                    set(existing.value_indexes)
                    | set(binding.value_indexes)
                )
            )
            provenance = tuple(
                dict.fromkeys(
                    (
                        *existing.provenance_references,
                        *binding.provenance_references,
                    )
                )
            )
            bindings_by_name[binding.fact_name] = existing.model_copy(
                update={
                    "value_indexes": indexes,
                    "provenance_references": provenance,
                }
            )
    return PredicateOutcome(
        result=result,
        basis_codes=codes,
        bindings=tuple(bindings_by_name.values()),
        unresolved_facts=_ordered_unique(
            tuple(
                fact
                for outcome in outcomes
                for fact in outcome.unresolved_facts
            )
        ),
    )


def _ordered_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _match_bools(
    outcomes: tuple[bool, ...],
    value_match: ValueMatch,
) -> TruthValue:
    matched = (
        any(outcomes)
        if value_match is ValueMatch.ANY
        else all(outcomes)
    )
    return TruthValue.TRUE if matched else TruthValue.FALSE


def _compare_pairs(
    left_values: tuple[object, ...],
    right_values: tuple[object, ...],
    value_match: ValueMatch,
    compare: object,
) -> TruthValue:
    comparison = compare
    assert callable(comparison)
    if value_match is ValueMatch.ALL:
        outcomes = tuple(
            comparison(left, right)
            for left in left_values
            for right in right_values
        )
    else:
        outcomes = tuple(
            comparison(left, right)
            for left in left_values
            for right in right_values
        )
    return _match_bools(outcomes, value_match)


def _date_compare(
    left: object,
    right: object,
    compare: object,
) -> bool:
    if not isinstance(left, (date, datetime)) or not isinstance(
        right,
        (date, datetime),
    ):
        raise PredicateExecutionError(
            "date predicate received a non-date value"
        )
    if isinstance(left, datetime) != isinstance(right, datetime):
        left = left.date() if isinstance(left, datetime) else left
        right = right.date() if isinstance(right, datetime) else right
    comparison = compare
    assert callable(comparison)
    return bool(comparison(left, right))


def _days_between_at_most(
    earlier: object,
    later: object,
    maximum_days: int,
) -> bool:
    if not isinstance(earlier, (date, datetime)) or not isinstance(
        later,
        (date, datetime),
    ):
        raise PredicateExecutionError(
            "days_between_at_most received a non-date value"
        )
    if isinstance(earlier, datetime) != isinstance(later, datetime):
        earlier = (
            earlier.date() if isinstance(earlier, datetime) else earlier
        )
        later = later.date() if isinstance(later, datetime) else later
    delta = later - earlier
    return 0 <= delta.days <= maximum_days


def _numeric_compare(
    value: object,
    bound: Decimal,
    *,
    at_least: bool,
) -> bool:
    if type(value) not in {int, Decimal}:
        raise PredicateExecutionError(
            "numeric predicate received a non-numeric value"
        )
    numeric = Decimal(value)
    return numeric >= bound if at_least else numeric <= bound


def _normalized_string_equals(left: object, right: object) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        raise PredicateExecutionError(
            "normalized string predicate received a non-string value"
        )
    return _normalize_controlled_string(left) == _normalize_controlled_string(
        right
    )


def _normalize_controlled_string(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _hours_before_less_than(
    earlier: object,
    later: object,
    maximum_hours: object,
) -> bool:
    if not isinstance(earlier, datetime) or not isinstance(later, datetime):
        raise PredicateExecutionError(
            "hours_before_less_than received a non-DATETIME value"
        )
    if (
        earlier.tzinfo is None
        or earlier.utcoffset() is None
        or later.tzinfo is None
        or later.utcoffset() is None
    ):
        raise PredicateExecutionError(
            "hours_before_less_than requires timezone-aware values"
        )
    if type(maximum_hours) not in {int, Decimal}:
        raise PredicateExecutionError(
            "hours_before_less_than received a non-numeric window"
        )
    bound = Decimal(maximum_hours)
    if bound < 0:
        raise PredicateExecutionError(
            "hours_before_less_than requires a non-negative window"
        )
    elapsed_seconds = Decimal(str((later - earlier).total_seconds()))
    if elapsed_seconds < 0:
        raise PredicateExecutionError(
            "hours_before_less_than received reversed datetimes"
        )
    return elapsed_seconds / Decimal(3600) < bound


def _material_address_matches(left: object, right: object) -> bool:
    """Compare only normalized source strings; never infer components."""
    if not isinstance(left, str) or not isinstance(right, str):
        raise PredicateExecutionError(
            "material_address_match received a non-string value"
        )
    normalized_left = _normalize_address(left)
    normalized_right = _normalize_address(right)
    if not normalized_left or not normalized_right:
        raise PredicateExecutionError(
            "material_address_match received an empty normalized value"
        )
    return (
        normalized_left == normalized_right
        or normalized_left in normalized_right
        or normalized_right in normalized_left
    )


def _normalize_address(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^a-z0-9]", "", normalized)
