"""Typed, secret-safe failures at the application boundary."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re


class ApplicationError(Exception):
    """Base error for application-service contract failures."""


class ApplicationPersistenceError(ApplicationError):
    """Raised for invalid attempt or batch persistence transitions."""


class CachedExecutionDisabledError(ApplicationError):
    """Raised when execution is attempted in cached-view mode."""


class CaseCatalogueError(ApplicationError):
    """Raised when the configured case catalogue cannot be validated."""


class CaseExecutionNotFoundError(ApplicationError):
    """Raised when a requested application attempt does not exist."""


@dataclass(frozen=True)
class SanitizedExecutionError:
    error_type: str
    message: str


_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret)\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"\b(?:sk|v1)\.[A-Za-z0-9._-]{12,}\b"),
)


def sanitize_execution_error(
    error: Exception,
    *,
    stage: str,
) -> SanitizedExecutionError:
    """Return a bounded diagnostic without credentials or evidence content."""
    error_type = type(error).__name__
    stage_name = re.sub(r"[^A-Z0-9_]", "_", stage.upper())[:80]
    message = (
        f"Case execution failed during {stage_name} "
        f"({error_type})."
    )
    # Defence in depth: application errors may carry a deliberately public
    # message, but it still passes through credential redaction.
    if isinstance(error, ApplicationError) and str(error).strip():
        message = str(error).strip()[:500]
    for name in ("MODEL_API_KEY", "NEBIUS_API_KEY"):
        secret = os.environ.get(name)
        if secret:
            message = message.replace(secret, "[REDACTED]")
    for pattern in _CREDENTIAL_PATTERNS:
        message = pattern.sub(
            lambda match: (
                (match.group(1) if match.lastindex else "")
                + "[REDACTED]"
            ),
            message,
        )
    return SanitizedExecutionError(
        error_type=error_type[:120],
        message=message[:500],
    )
