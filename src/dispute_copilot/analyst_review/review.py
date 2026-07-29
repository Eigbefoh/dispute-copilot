"""Offline CLI for immutable analyst-review package assembly."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from dispute_copilot.analyst_review.errors import AnalystReviewError
from dispute_copilot.analyst_review.registry import (
    DEFAULT_PRESENTATION_RULES_KEY,
    DEFAULT_PRESENTATION_RULES_ROOT,
    DEFAULT_PRESENTATION_RULES_VERSION,
)
from dispute_copilot.analyst_review.service import (
    create_analyst_review_package,
)
from dispute_copilot.extraction.bundle_loader import DEFAULT_DATABASE_PATH


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dispute_copilot.analyst_review.review",
        description=(
            "Assemble one persisted recommendation chain into an offline "
            "analyst-review package."
        ),
    )
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--recommendation-id")
    identity.add_argument("--selection-id")
    parser.add_argument(
        "--presentation-rules-key",
        default=DEFAULT_PRESENTATION_RULES_KEY,
    )
    parser.add_argument(
        "--presentation-rules-version",
        default=DEFAULT_PRESENTATION_RULES_VERSION,
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
    )
    parser.add_argument(
        "--presentation-rules-root",
        type=Path,
        default=DEFAULT_PRESENTATION_RULES_ROOT,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the complete structured package as JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        recommendation_id = (
            UUID(args.recommendation_id)
            if args.recommendation_id
            else None
        )
        selection_id = (
            UUID(args.selection_id) if args.selection_id else None
        )
    except ValueError as error:
        parser.error(f"Invalid UUID: {error}")

    try:
        package = create_analyst_review_package(
            recommendation_id=recommendation_id,
            selection_id=selection_id,
            presentation_rules_key=args.presentation_rules_key,
            presentation_rules_version=args.presentation_rules_version,
            database_path=args.database,
            presentation_rules_root=args.presentation_rules_root,
        )
    except AnalystReviewError as error:
        print(f"Analyst review package failed: {error}")
        return 1

    if args.json:
        print(package.model_dump_json(indent=2))
        return 0

    print(f"Review package: {package.review_package_id}")
    print(f"Case: {package.case_summary.case_id}")
    print(f"Policy: {package.case_summary.policy_key}")
    print(
        "Recommendation: "
        f"{package.recommendation.decision.value}"
    )
    print(
        "Caused by: "
        + ", ".join(package.recommendation.causal_requirement_ids)
    )
    print(f"Status: {package.case_summary.current_workflow_status}")
    print("\nRationale:")
    for sentence in package.rationale.sentences:
        print(sentence)
    print("\nRequirement checklist:")
    for item in package.requirement_checklist:
        print(
            f"- {item.requirement_id}: {item.status.value} — "
            f"{item.requirement_text}"
        )
    print("\nEvidence gaps:")
    for gap in package.evidence_gaps:
        print(
            f"- {gap.requirement_id}: {gap.display_label} "
            f"({gap.resolution_status})"
        )
    print("\nSupporting sources:")
    for source in package.supporting_sources:
        location = (
            f"{source.source_document}, page {source.page_number}"
            if source.source_document is not None
            else source.supporting_content
        )
        print(
            f"- {source.fact_name}: {source.support_type.value} — "
            f"{location}"
        )
    print("\nAnalyst review completed: NO")
    print("New decision produced: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
