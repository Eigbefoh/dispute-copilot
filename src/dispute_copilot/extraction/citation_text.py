"""Deterministic text equivalence for evidence citations."""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")

# This identifier makes the deliberately narrow matching rule auditable.
CITATION_TEXT_NORMALIZATION_VERSION = "unicode-nfkc-whitespace-v1"


def normalize_citation_text(value: str) -> str:
    """Apply only Unicode NFKC and whitespace-run normalization.

    Matching remains case-sensitive and punctuation-sensitive. This function
    does not perform fuzzy matching, spelling correction, or paraphrase
    detection.
    """
    unicode_normalized = unicodedata.normalize("NFKC", value)
    return _WHITESPACE.sub(" ", unicode_normalized).strip()


def supporting_text_occurs_on_page(
    *,
    supporting_text: str,
    canonical_page_text: str,
) -> bool:
    """Return whether a non-empty normalized quote occurs on the page."""
    normalized_support = normalize_citation_text(supporting_text)
    if not normalized_support:
        return False
    return normalized_support in normalize_citation_text(
        canonical_page_text
    )
