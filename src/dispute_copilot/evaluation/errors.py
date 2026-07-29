"""Typed fail-closed errors for deterministic evaluation."""


class RequirementEvaluationError(Exception):
    """Base error for the offline evaluation phase."""


class EvaluationInputError(RequirementEvaluationError, ValueError):
    """Raised when immutable inputs fail lineage or integrity checks."""


class PredicateContractError(RequirementEvaluationError, ValueError):
    """Raised when executable policy predicates are invalid."""


class PredicateExecutionError(RequirementEvaluationError, ValueError):
    """Raised when a predicate cannot be deterministically evaluated."""


class EvaluationPersistenceConflictError(RequirementEvaluationError):
    """Raised when append-only evaluation history conflicts."""
