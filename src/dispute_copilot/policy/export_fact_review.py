"""Export the deterministic starting inventory for a fact review."""

from __future__ import annotations

import argparse
import csv
from collections.abc import Sequence
from pathlib import Path

from dispute_copilot.policy.fact_catalogue import (
    REVIEW_COLUMNS,
    collect_fact_references,
)
from dispute_copilot.policy.rule_models import PolicyRecord


def export_fact_review(
    rulebook_directory: Path,
    output_path: Path,
) -> int:
    """Write one stable, blank review row per unique required fact."""
    policies = tuple(
        PolicyRecord.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(
            (rulebook_directory / "policies").glob("*.json")
        )
    )
    references = collect_fact_references(policies)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for fact_name, fact_references in references.items():
            writer.writerow(
                {
                    "original_fact_name": fact_name,
                    "definition": "",
                    "value_type": "",
                    "source_type": "",
                    "cardinality": "",
                    "policies_using_it": ";".join(fact_references),
                    "classification": "",
                    "decision": "",
                    "replacement_fact_names": "",
                    "review_notes": "",
                }
            )
    return len(references)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dispute_copilot.policy.export_fact_review",
        description="Export a deterministic policy fact-review inventory.",
    )
    parser.add_argument("rulebook_directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    count = export_fact_review(args.rulebook_directory, args.output)
    print(f"Facts exported: {count}")
    print(f"Output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
