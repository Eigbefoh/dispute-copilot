"""Trusted local analyst identity resolution."""

from __future__ import annotations

import os

from dispute_copilot.human_review.decision_models import (
    TrustedAnalystIdentity,
)
from dispute_copilot.human_review.errors import DecisionValidationError

ANALYST_IDENTITY_ENV = "ANALYST_IDENTITY"


def resolve_trusted_analyst_identity() -> TrustedAnalystIdentity:
    """Resolve identity from server configuration, never browser input."""
    raw_identity = os.getenv(ANALYST_IDENTITY_ENV)
    if raw_identity is None or not raw_identity.strip():
        raise DecisionValidationError(
            f"{ANALYST_IDENTITY_ENV} must identify the local analyst"
        )
    try:
        return TrustedAnalystIdentity(subject=raw_identity.strip())
    except ValueError as error:
        raise DecisionValidationError(
            "Configured analyst identity is invalid"
        ) from error
