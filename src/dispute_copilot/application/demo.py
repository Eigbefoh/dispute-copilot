"""One-command entry point for live or cached analyst demonstrations."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from dispute_copilot.application.models import ExecutionMode
from dispute_copilot.application.service import (
    DEFAULT_RULEBOOK_VERSION,
    DisputeCopilotApplication,
)
from dispute_copilot.human_review.demo_app import create_demo_app
from dispute_copilot.human_review.errors import HumanReviewError
from dispute_copilot.human_review.identity import (
    resolve_trusted_analyst_identity,
)

DEFAULT_DEMO_DATABASE = Path("data/v4-live-validation.db")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dispute_copilot.application.demo",
        description="Launch the multi-case analyst-review demonstration.",
    )
    parser.add_argument(
        "--mode",
        choices=("live", "cached"),
        required=True,
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DEMO_DATABASE,
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("."),
    )
    parser.add_argument(
        "--rulebook-version",
        default=DEFAULT_RULEBOOK_VERSION,
    )
    parser.add_argument("--server-name", default="127.0.0.1")
    parser.add_argument("--server-port", type=int, default=7860)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mode = (
        ExecutionMode.LIVE
        if args.mode == "live"
        else ExecutionMode.CACHED_VIEW
    )
    try:
        application = DisputeCopilotApplication(
            database_path=args.database,
            project_root=args.project_root,
            execution_mode=mode,
            rulebook_version=args.rulebook_version,
        )
        app = create_demo_app(
            application=application,
            analyst_identity=resolve_trusted_analyst_identity(),
        )
    except (ValueError, HumanReviewError) as error:
        print(f"Analyst demo failed: {error}")
        return 1
    app.launch(
        server_name=args.server_name,
        server_port=args.server_port,
        share=False,
        show_error=False,
        inbrowser=False,
        blocked_paths=[
            str((args.project_root / "data").resolve())
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
