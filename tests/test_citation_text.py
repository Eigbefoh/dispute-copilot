"""Tests for deliberately narrow evidence-citation text matching."""

from dispute_copilot.extraction.citation_text import (
    CITATION_TEXT_NORMALIZATION_VERSION,
    normalize_citation_text,
    supporting_text_occurs_on_page,
)


def test_normalization_is_versioned_and_only_nfkc_plus_whitespace() -> None:
    assert CITATION_TEXT_NORMALIZATION_VERSION == (
        "unicode-nfkc-whitespace-v1"
    )
    assert normalize_citation_text("  Ｃarlton\t\n Armchair  ") == (
        "Carlton Armchair"
    )


def test_shared_normalization_allows_only_deterministic_equivalence() -> None:
    page = "Carlton Armchair (Sage)\n— delivered 18 Apr 2025"

    assert supporting_text_occurs_on_page(
        supporting_text=(
            "Carlton Armchair (Sage) — delivered 18 Apr 2025"
        ),
        canonical_page_text=page,
    )


def test_matching_does_not_ignore_case_punctuation_or_paraphrase() -> None:
    page = "Carlton Armchair (Sage) — delivered 18 Apr 2025"

    for rejected in (
        "carlton Armchair (Sage) — delivered 18 Apr 2025",
        "Carlton Armchair Sage — delivered 18 Apr 2025",
        "A sage Carlton armchair was delivered on 18 April 2025",
    ):
        assert not supporting_text_occurs_on_page(
            supporting_text=rejected,
            canonical_page_text=page,
        )


def test_empty_supporting_text_never_matches() -> None:
    assert not supporting_text_occurs_on_page(
        supporting_text=" \n\t ",
        canonical_page_text="some canonical text",
    )
