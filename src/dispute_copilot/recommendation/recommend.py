"""Offline CLI for deterministic recommendation mapping."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from dispute_copilot.extraction.bundle_loader import DEFAULT_DATABASE_PATH
from dispute_copilot.recommendation.errors import RecommendationError
from dispute_copilot.recommendation.registry import DEFAULT_MAPPING_ROOT
from dispute_copilot.recommendation.service import create_recommendation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dispute_copilot.recommendation.recommend",
        description=(
            "Map one persisted requirement evaluation to an offline "
            "recommendation."
        ),
    )
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--selection-id")
    identity.add_argument("--evaluation-id")
    parser.add_argument("--mapping-key", required=True)
    parser.add_argument("--mapping-version", required=True)
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
    )
    parser.add_argument(
        "--mapping-root",
        type=Path,
        default=DEFAULT_MAPPING_ROOT,
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        selection_id = (
            UUID(args.selection_id) if args.selection_id else None
        )
        evaluation_id = (
            UUID(args.evaluation_id) if args.evaluation_id else None
        )
    except ValueError as error:
        parser.error(f"Invalid UUID: {error}")

    try:
        result = create_recommendation(
            selection_id=selection_id,
            evaluation_id=evaluation_id,
            mapping_key=args.mapping_key,
            mapping_version=args.mapping_version,
            database_path=args.database,
            mapping_root=args.mapping_root,
        )
    except RecommendationError as error:
        print(f"Recommendation mapping failed: {error}")
        return 1

    causal_ids = ", ".join(
        item.requirement_id
        for item in result.causal_requirement_results
    )
    print(f"Recommendation: {result.recommendation_id}")
    print(f"Evaluation: {result.evaluation_id}")
    print(f"Selection: {result.selection_id}")
    print(f"Policy: {result.policy_id}")
    print(f"Decision: {result.recommendation.value}")
    print(
        "Basis: "
        + ", ".join(code.value for code in result.basis_codes)
    )
    print(f"Caused by: {causal_ids}")
    print(f"Status: {result.status}")
    if args.verbose:
        print()
        print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
