"""Offline CLI for deterministic policy-requirement evaluation."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from dispute_copilot.evaluation.errors import RequirementEvaluationError
from dispute_copilot.evaluation.predicate_catalogue import (
    DEFAULT_RULEBOOK_ROOT,
)
from dispute_copilot.evaluation.service import (
    evaluate_selection_requirements,
)
from dispute_copilot.extraction.bundle_loader import DEFAULT_DATABASE_PATH


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dispute_copilot.evaluation.evaluate_requirements",
        description=(
            "Evaluate admitted facts against one frozen policy without "
            "models or evidence access."
        ),
    )
    parser.add_argument("selection_id", help="Policy-selection UUID.")
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
    )
    parser.add_argument(
        "--rulebook-root",
        type=Path,
        default=DEFAULT_RULEBOOK_ROOT,
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the complete immutable evaluation JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        selection_id = UUID(args.selection_id)
    except ValueError:
        parser.error(f"Invalid selection ID: {args.selection_id}")

    try:
        result = evaluate_selection_requirements(
            selection_id=selection_id,
            database_path=args.database,
            rulebook_root=args.rulebook_root,
        )
    except RequirementEvaluationError as error:
        print(f"Requirement evaluation failed: {error}")
        return 1

    print(f"Evaluation: {result.evaluation_id}")
    print(f"Selection: {result.selection_id}")
    print(f"Resolution: {result.resolution_id}")
    print(f"Policy: {result.policy_id}")
    print()
    print("Requirement evaluations:")
    for item in result.ordered_requirement_results:
        print(
            f"- {item.requirement_id}: {item.status.value} "
            f"({item.predicate_result.value})"
        )
        print(f"  Basis: {', '.join(item.basis_codes)}")
        if item.missing_or_unresolved_facts:
            print(
                "  Unresolved: "
                + ", ".join(item.missing_or_unresolved_facts)
            )
    print()
    if result.aggregation_summary is not None:
        summary = result.aggregation_summary
        print(f"Aggregation: {summary.mode}")
        print(
            "  Satisfied: "
            f"{summary.satisfied_count}; unresolved: "
            f"{summary.unresolved_count}; maximum possible: "
            f"{summary.maximum_possible_satisfied}; minimum required: "
            f"{summary.minimum_required}"
        )
    print(f"Overall: {result.overall_status.value}")
    print(f"Status: {result.status}")
    print("Recommendation produced: NO")

    if args.verbose:
        print()
        print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
