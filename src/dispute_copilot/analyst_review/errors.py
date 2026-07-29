"""Typed fail-closed errors for analyst-review package assembly."""


class AnalystReviewError(Exception):
    """Base error for deterministic analyst-review package assembly."""


class AnalystReviewInputError(AnalystReviewError, ValueError):
    """Raised when authoritative lineage or integrity is invalid."""


class PresentationRulesError(AnalystReviewError, ValueError):
    """Raised when explicit presentation rules are missing or invalid."""


class AnalystReviewPersistenceConflictError(AnalystReviewError):
    """Raised when append-only review-package history conflicts."""
