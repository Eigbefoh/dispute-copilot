"""Typed fail-closed recommendation errors."""


class RecommendationError(Exception):
    """Base error for deterministic recommendation mapping."""


class RecommendationInputError(RecommendationError, ValueError):
    """Raised when authoritative evaluation lineage is invalid."""


class MappingContractError(RecommendationError, ValueError):
    """Raised when a mapping manifest is absent or invalid."""


class RecommendationPersistenceConflictError(RecommendationError):
    """Raised when append-only recommendation history conflicts."""
