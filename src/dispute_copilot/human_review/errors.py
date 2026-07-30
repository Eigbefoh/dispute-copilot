"""Typed, analyst-safe errors for human review."""


class HumanReviewError(Exception):
    """Base error for evidence presentation and analyst decisions."""


class ReviewContextError(HumanReviewError, ValueError):
    """Raised when a persisted review lineage is absent or inconsistent."""


class EvidenceDisplayError(HumanReviewError, ValueError):
    """Base error for read-only evidence presentation."""


class UnlinkedEvidenceError(EvidenceDisplayError):
    """Raised when a request does not belong to the exact review package."""


class SourceIntegrityError(EvidenceDisplayError):
    """Raised when current evidence bytes differ from the intake snapshot."""


class InvalidEvidencePageError(EvidenceDisplayError):
    """Raised when a requested page falls outside the verified source."""


class UnavailableEvidenceRegionError(EvidenceDisplayError):
    """Raised when a claimed highlight is not grounded by provenance."""


class AnalystDecisionError(HumanReviewError, ValueError):
    """Base error for final analyst actions."""


class StaleReviewPackageError(AnalystDecisionError):
    """Raised when a browser submits against a stale package hash."""


class DecisionValidationError(AnalystDecisionError):
    """Raised when an action violates the versioned decision contract."""


class DecisionPersistenceConflictError(AnalystDecisionError):
    """Raised when append-only decision history or idempotency conflicts."""
