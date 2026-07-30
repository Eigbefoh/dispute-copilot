"""Command-line interface for Dispute Copilot."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from dispute_copilot import __version__
from dispute_copilot.intake.case_loader import CaseNotFoundError
from dispute_copilot.intake.evidence_registry import (
    MissingEvidenceError,
    UnsupportedEvidenceError,
)
from dispute_copilot.orchestration.service import create_case_run
from dispute_copilot.policy.repository import (
    DuplicatePolicySelectionError,
)
from dispute_copilot.policy.constants import DEFAULT_RULEBOOK_VERSION
from dispute_copilot.policy.selection_service import (
    CaseRunNotFoundError,
    CaseRunNotReadyForPolicySelectionError,
    ExactPolicyNotFoundError,
    select_policy_for_run,
)
from dispute_copilot.repositories.sqlite import SQLiteCaseRunRepository

CASES_PATH = Path("data/cases.json")
EVIDENCE_DIRECTORY = Path("data/evidence")
DATABASE_PATH = Path("data/dispute_copilot.db")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="dispute-copilot",
        description="Process and evaluate payment disputes.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "command_or_case_id",
        help=(
            "A case ID to intake, 'get-run', or 'select-policy'."
        ),
    )
    parser.add_argument(
        "run_id",
        nargs="?",
        help="Run UUID for get-run or select-policy.",
    )
    parser.add_argument(
        "--rulebook-version",
        default=DEFAULT_RULEBOOK_VERSION,
        help=(
            "Exact rulebook version for select-policy "
            f"(default: {DEFAULT_RULEBOOK_VERSION})."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print the complete immutable run snapshot as JSON.",
    )
    return parser


def _get_saved_run(
    parser: argparse.ArgumentParser,
    run_id_value: str | None,
    repository: SQLiteCaseRunRepository,
    verbose: bool,
) -> int:
    """Retrieve and print a saved case run."""
    if run_id_value is None:
        parser.error("get-run requires a run ID")

    try:
        run_id = UUID(run_id_value)
    except ValueError:
        parser.error(f"Invalid run ID: {run_id_value}")

    run = repository.get(run_id)
    if run is None:
        print(f"Run not found: {run_id}")
        return 1

    print(f"Run: {run.run_id}")
    print(f"Created at: {run.created_at.isoformat()}")
    print(f"Case: {run.case_snapshot.case_id}")
    print(f"Evidence files: {len(run.evidence_snapshot)}")
    for artifact in run.evidence_snapshot:
        print(f"✓ {artifact.filename}")
        print(f"  SHA-256: {artifact.sha256}")
    print(f"Status: {run.status}")

    if verbose:
        print()
        print(run.model_dump_json(indent=2))

    return 0


def _create_and_save_run(
    parser: argparse.ArgumentParser,
    case_id: str,
    repository: SQLiteCaseRunRepository,
    verbose: bool,
) -> int:
    """Create, persist, and print a new case run."""
    try:
        run = create_case_run(
            case_id=case_id,
            cases_path=CASES_PATH,
            evidence_directory=EVIDENCE_DIRECTORY,
        )
    except CaseNotFoundError as error:
        parser.error(str(error))
    except MissingEvidenceError as error:
        print("Status: MISSING_EVIDENCE")
        for filename in error.missing_filenames:
            print(f"Missing: {filename}")
        return 1
    except UnsupportedEvidenceError as error:
        print("Status: UNSUPPORTED_EVIDENCE")
        print(f"Unsupported: {error.filename} ({error.mime_type})")
        return 1

    repository.save(run)

    print(f"Run created: {run.run_id}")
    print(f"Case: {run.case_snapshot.case_id}")
    print(f"Evidence files: {len(run.evidence_snapshot)}")
    print(f"Status: {run.status}")
    print("Saved to SQLite")

    if verbose:
        print()
        print(run.model_dump_json(indent=2))

    return 0


def _select_policy(
    parser: argparse.ArgumentParser,
    run_id_value: str | None,
    rulebook_version: str,
    verbose: bool,
) -> int:
    """Select, snapshot, and persist an exact policy for a run."""
    if run_id_value is None:
        parser.error("select-policy requires a run ID")

    try:
        run_id = UUID(run_id_value)
    except ValueError:
        parser.error(f"Invalid run ID: {run_id_value}")

    try:
        selection = select_policy_for_run(
            run_id=run_id,
            rulebook_version=rulebook_version,
            database_path=DATABASE_PATH,
        )
    except (
        CaseRunNotFoundError,
        CaseRunNotReadyForPolicySelectionError,
        ExactPolicyNotFoundError,
        DuplicatePolicySelectionError,
    ) as error:
        print(f"Policy selection failed: {error}")
        return 1

    print(f"Selection created: {selection.selection_id}")
    print(f"Run: {selection.run_id}")
    print(f"Policy: {selection.policy_id}")
    print(f"Rulebook: {selection.rulebook_version}")
    print(f"Status: {selection.status}")
    print("Saved to SQLite")

    if verbose:
        print()
        print(selection.model_dump_json(indent=2))

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Dispute Copilot command-line interface."""
    parser = build_parser()
    args = parser.parse_args(argv)
    repository = SQLiteCaseRunRepository(DATABASE_PATH)

    if args.command_or_case_id == "get-run":
        return _get_saved_run(
            parser,
            args.run_id,
            repository,
            args.verbose,
        )

    if args.command_or_case_id == "select-policy":
        return _select_policy(
            parser,
            args.run_id,
            args.rulebook_version,
            args.verbose,
        )

    if args.run_id is not None:
        parser.error("Unexpected second argument for case intake")

    return _create_and_save_run(
        parser,
        args.command_or_case_id,
        repository,
        args.verbose,
    )


if __name__ == "__main__":
    raise SystemExit(main())
