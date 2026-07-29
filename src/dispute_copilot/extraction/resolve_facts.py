"""Narrow command for lean evidence extraction and fact admission."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from dispute_copilot.config import load_model_provider_settings
from dispute_copilot.domain.policy_selection import PolicySelection
from dispute_copilot.extraction.bundle_loader import DEFAULT_DATABASE_PATH
from dispute_copilot.extraction.errors import (
    DocumentConversionError,
    EvidenceBundleError,
    EvidenceExtractionError,
)
from dispute_copilot.extraction.fact_resolution_models import (
    CaseMetadataProvenance,
    EvidenceTextProvenance,
    FactResolutionResult,
    VisualObservationProvenance,
)
from dispute_copilot.extraction.fact_resolution_service import (
    resolve_selection_facts,
)
from dispute_copilot.extraction.fact_resolution_validation import (
    FactResolutionValidationError,
)
from dispute_copilot.extraction.provider_runtime import (
    create_extraction_runtime,
)
from dispute_copilot.extraction.provider_response_diagnostics import (
    ProviderResponseDiagnostics,
)
from dispute_copilot.policy.fact_models import FactDefinition
from dispute_copilot.policy.repository import (
    SQLitePolicyRepository,
    SQLitePolicySelectionRepository,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dispute_copilot.extraction.resolve_facts",
        description=(
            "Extract and deterministically admit the facts requested by "
            "one immutable policy selection."
        ),
    )
    parser.add_argument("selection_id", help="Policy-selection UUID.")
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=(
            "SQLite database path "
            f"(default: {DEFAULT_DATABASE_PATH})."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the complete immutable result as JSON.",
    )
    parser.add_argument(
        "--debug-provider-response",
        action="store_true",
        help=(
            "On provider-response validation failure, write the redacted "
            "raw response to a permission-restricted temporary file. "
            "The artifact may contain evidence-derived content."
        ),
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
        settings = load_model_provider_settings()
        runtime = create_extraction_runtime(
            settings,
            diagnostics=ProviderResponseDiagnostics(
                capture_raw_response=args.debug_provider_response,
            ),
        )
        result = resolve_selection_facts(
            selection_id=selection_id,
            extractor=runtime.extractor,
            vision_provider=runtime.vision_provider,
            database_path=args.database,
        )
    except (
        EvidenceExtractionError,
        EvidenceBundleError,
        DocumentConversionError,
        FactResolutionValidationError,
    ) as error:
        print(f"Fact resolution failed: {error}")
        return 1

    selection = SQLitePolicySelectionRepository(
        args.database
    ).get(selection_id)
    if selection is None:
        print(
            "Fact resolution failed: selection disappeared after "
            "resolution"
        )
        return 1
    definitions = {
        definition.fact_name: definition
        for definition in SQLitePolicyRepository(
            args.database
        ).list_fact_definitions(selection.rulebook_version)
    }
    _print_result(
        result=result,
        selection=selection,
        definitions=definitions,
        verbose=args.verbose,
    )
    return 0


def _print_result(
    *,
    result: FactResolutionResult,
    selection: PolicySelection,
    definitions: dict[str, FactDefinition],
    verbose: bool,
) -> None:
    print(f"Selection: {selection.selection_id}")
    print(f"Run: {selection.run_id}")
    print(f"Policy: {selection.policy_id}")
    print(f"Rulebook: {selection.rulebook_version}")
    print()
    print("Resolved facts:")

    for fact in result.facts:
        definition = definitions[fact.fact_name]
        print(f"- {fact.fact_name}")
        print(f"  Route: {definition.source_type}")
        print(f"  Status: {fact.status}")
        if fact.confidence is not None:
            print(f"  Confidence: {fact.confidence:.3f}")
        for resolved_value in fact.resolved_values:
            print(f"  Value: {resolved_value.value}")
            for provenance in resolved_value.provenance:
                _print_provenance(provenance)

    print()
    print(f"Status: {result.status}")
    print("Evaluation produced: NO")
    print("Recommendation produced: NO")

    if verbose:
        print()
        print(result.model_dump_json(indent=2))


def _print_provenance(
    provenance: (
        CaseMetadataProvenance
        | EvidenceTextProvenance
        | VisualObservationProvenance
    ),
) -> None:
    if isinstance(provenance, CaseMetadataProvenance):
        print(
            "  Provenance: "
            f"CaseRun {provenance.case_run_id} "
            f"field {provenance.field_path}"
        )
        return
    if isinstance(provenance, EvidenceTextProvenance):
        print(
            "  Provenance: "
            f"{provenance.file_name}, page {provenance.page_number}, "
            f"{provenance.extraction_method}"
        )
        print(f"  Support: {provenance.supporting_text}")
        return
    print(
        "  Provenance: "
        f"{provenance.file_name}, page {provenance.page_number}, VISION"
    )
    print(f"  Observation: {provenance.observation}")


if __name__ == "__main__":
    raise SystemExit(main())
