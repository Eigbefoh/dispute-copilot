"""Controlled one-time importer for versioned executable policies."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from dispute_copilot.intake.hashing import calculate_sha256
from dispute_copilot.extraction.metadata_resolver import (
    metadata_resolver_names,
)
from dispute_copilot.policy.fact_catalogue import (
    FactCatalogueValidationError,
    load_fact_review,
    validate_fact_catalogue,
)
from dispute_copilot.policy.fact_models import (
    FactCatalogue,
    FactReview,
)
from dispute_copilot.policy.repository import (
    ConflictingFactDefinitionError,
    ConflictingPolicyKeyError,
    DuplicateFactDefinitionError,
    DuplicatePolicyError,
    SQLitePolicyRepository,
)
from dispute_copilot.policy.rule_models import (
    CanonicalTranscript,
    PolicyRecord,
    RulebookMetadata,
    validate_policy_citations,
)

DEFAULT_DATABASE_PATH = Path("data/dispute_copilot.db")


class RulebookImportError(Exception):
    """Raised when rulebook validation fails before persistence."""


@dataclass(frozen=True)
class ValidatedRulebook:
    """A fully checked rulebook batch ready for atomic persistence."""

    version: str
    transcript: CanonicalTranscript
    transcript_sha256: str
    policies: tuple[PolicyRecord, ...]
    policy_hashes: dict[str, str]
    fact_catalogue: FactCatalogue | None = None
    fact_catalogue_sha256: str | None = None
    fact_review: FactReview | None = None
    fact_review_sha256: str | None = None


def _resolve_artifact(
    rulebook_directory: Path,
    relative_path: Path,
) -> Path:
    root = rulebook_directory.resolve()
    candidate = (rulebook_directory / relative_path).resolve()
    if not candidate.is_relative_to(root):
        raise RulebookImportError(
            f"Artifact escapes rulebook directory: {relative_path}"
        )
    if not candidate.is_file():
        raise RulebookImportError(f"Artifact not found: {relative_path}")
    return candidate


def _validate_hash(
    path: Path,
    expected_sha256: str,
    artifact_label: str,
) -> str:
    actual_sha256 = calculate_sha256(path)
    if actual_sha256 != expected_sha256:
        raise RulebookImportError(
            f"{artifact_label} SHA-256 mismatch: expected "
            f"{expected_sha256}, got {actual_sha256}"
        )
    return actual_sha256


def _load_model(
    model_type,
    path: Path,
    artifact_label: str,
):
    try:
        return model_type.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError, ValueError) as error:
        raise RulebookImportError(
            f"Invalid {artifact_label} at {path}: {error}"
        ) from error


def validate_rulebook(rulebook_directory: Path) -> ValidatedRulebook:
    """Validate every rulebook artifact without touching SQLite."""
    if not rulebook_directory.is_dir():
        raise RulebookImportError(
            f"Rulebook directory not found: {rulebook_directory}"
        )

    metadata_path = rulebook_directory / "metadata.json"
    metadata = _load_model(
        RulebookMetadata,
        metadata_path,
        "rulebook metadata",
    )

    source_hashes: dict[str, str] = {}
    for source_metadata in metadata.source_documents:
        if (
            source_metadata.mime_type != "application/pdf"
            or not source_metadata.read_only
            or source_metadata.parse_during_case_run
        ):
            raise RulebookImportError(
                f"Invalid source controls for {source_metadata.filename}"
            )
        source_path = _resolve_artifact(
            rulebook_directory,
            source_metadata.path,
        )
        if source_path.name != source_metadata.filename:
            raise RulebookImportError(
                f"Source filename mismatch for {source_metadata.path}"
            )
        if source_path.stat().st_size != source_metadata.size_bytes:
            raise RulebookImportError(
                f"Source size mismatch for {source_metadata.filename}"
            )
        source_hashes[source_metadata.filename] = _validate_hash(
            source_path,
            source_metadata.sha256,
            f"source PDF {source_metadata.filename}",
        )

    transcript_metadata = metadata.canonical_transcript
    transcript_path = _resolve_artifact(
        rulebook_directory,
        transcript_metadata.path,
    )
    transcript_sha256 = _validate_hash(
        transcript_path,
        transcript_metadata.sha256,
        "canonical transcript",
    )
    transcript = _load_model(
        CanonicalTranscript,
        transcript_path,
        "canonical transcript",
    )
    if transcript.rulebook_version != metadata.rulebook_id:
        raise RulebookImportError(
            "Transcript rulebook_version does not match metadata"
        )
    if transcript.source_file not in source_hashes:
        raise RulebookImportError(
            f"Transcript source is not registered: "
            f"{transcript.source_file}"
        )
    if transcript.source_sha256 != source_hashes[transcript.source_file]:
        raise RulebookImportError(
            "Transcript source_sha256 does not match the verified PDF"
        )
    if transcript_metadata.source_sha256 != transcript.source_sha256:
        raise RulebookImportError(
            "Transcript metadata source_sha256 mismatch"
        )
    if (
        transcript_metadata.page_count != transcript.page_count
        or transcript_metadata.line_count != transcript.line_count
    ):
        raise RulebookImportError(
            "Transcript counts do not match rulebook metadata"
        )

    fact_catalogue: FactCatalogue | None = None
    fact_catalogue_sha256: str | None = None
    fact_review: FactReview | None = None
    fact_review_sha256: str | None = None
    if metadata.fact_catalogue is not None:
        catalogue_path = _resolve_artifact(
            rulebook_directory,
            metadata.fact_catalogue.path,
        )
        fact_catalogue_sha256 = _validate_hash(
            catalogue_path,
            metadata.fact_catalogue.sha256,
            "fact catalogue",
        )
        fact_catalogue = _load_model(
            FactCatalogue,
            catalogue_path,
            "fact catalogue",
        )
        if fact_catalogue.rulebook_version != metadata.rulebook_id:
            raise RulebookImportError(
                "Fact catalogue rulebook_version does not match metadata"
            )
        if len(fact_catalogue.facts) != (
            metadata.fact_catalogue.fact_count
        ):
            raise RulebookImportError(
                "Fact catalogue count does not match metadata"
            )

        if metadata.fact_review is None:
            raise RulebookImportError(
                "Fact review metadata is required with a catalogue"
            )
        review_path = _resolve_artifact(
            rulebook_directory,
            metadata.fact_review.path,
        )
        fact_review_sha256 = _validate_hash(
            review_path,
            metadata.fact_review.sha256,
            "fact review",
        )
        try:
            fact_review = load_fact_review(review_path)
        except FactCatalogueValidationError as error:
            raise RulebookImportError(str(error)) from error
        if len(fact_review.records) != metadata.fact_review.row_count:
            raise RulebookImportError(
                "Fact review count does not match metadata"
            )

    declared_policy_paths = {
        policy_metadata.path for policy_metadata in metadata.policies
    }
    discovered_policy_paths = {
        policy_path.relative_to(rulebook_directory)
        for policy_path in (rulebook_directory / "policies").rglob("*.json")
    }
    if declared_policy_paths != discovered_policy_paths:
        missing = declared_policy_paths - discovered_policy_paths
        undeclared = discovered_policy_paths - declared_policy_paths
        raise RulebookImportError(
            "Policy manifest mismatch; "
            f"missing={sorted(map(str, missing))}, "
            f"undeclared={sorted(map(str, undeclared))}"
        )

    policies: list[PolicyRecord] = []
    policy_hashes: dict[str, str] = {}
    seen_keys: set[tuple[str, str, str]] = set()
    seen_ids: set[str] = set()
    for policy_metadata in metadata.policies:
        policy_path = _resolve_artifact(
            rulebook_directory,
            policy_metadata.path,
        )
        policy_sha256 = _validate_hash(
            policy_path,
            policy_metadata.sha256,
            f"policy {policy_metadata.policy_id}",
        )
        if policy_metadata.transcript_sha256 != transcript_sha256:
            raise RulebookImportError(
                f"Policy {policy_metadata.policy_id} is linked to a "
                "different transcript"
            )
        if policy_metadata.fact_catalogue_sha256 != (
            fact_catalogue_sha256
        ):
            raise RulebookImportError(
                f"Policy {policy_metadata.policy_id} is linked to a "
                "different fact catalogue"
            )

        policy = _load_model(
            PolicyRecord,
            policy_path,
            f"policy {policy_metadata.policy_id}",
        )
        if policy.policy_id != policy_metadata.policy_id:
            raise RulebookImportError(
                f"Policy ID mismatch in {policy_metadata.path}"
            )
        if policy.rulebook_version != metadata.rulebook_id:
            raise RulebookImportError(
                f"Policy {policy.policy_id} rulebook_version mismatch"
            )
        try:
            validate_policy_citations(policy, transcript)
        except ValueError as error:
            raise RulebookImportError(
                f"Invalid citations in {policy.policy_id}: {error}"
            ) from error

        key = (
            policy.rulebook_version,
            policy.scheme,
            policy.reason_code,
        )
        if key in seen_keys:
            raise RulebookImportError(
                f"Duplicate policy key in rulebook: {key}"
            )
        if policy.policy_id in seen_ids:
            raise RulebookImportError(
                f"Duplicate policy_id in rulebook: {policy.policy_id}"
            )
        seen_keys.add(key)
        seen_ids.add(policy.policy_id)
        policies.append(policy)
        policy_hashes[policy.policy_id] = policy_sha256

    if fact_catalogue is not None:
        if fact_review is None:
            raise RulebookImportError(
                "Fact review is required with a catalogue"
            )
        try:
            validate_fact_catalogue(
                policies=tuple(policies),
                catalogue=fact_catalogue,
                review=fact_review,
                metadata_resolver_names=metadata_resolver_names(),
            )
        except FactCatalogueValidationError as error:
            raise RulebookImportError(
                f"Invalid fact catalogue: {error}"
            ) from error

    return ValidatedRulebook(
        version=metadata.rulebook_id,
        transcript=transcript,
        transcript_sha256=transcript_sha256,
        policies=tuple(policies),
        policy_hashes=policy_hashes,
        fact_catalogue=fact_catalogue,
        fact_catalogue_sha256=fact_catalogue_sha256,
        fact_review=fact_review,
        fact_review_sha256=fact_review_sha256,
    )


def import_rulebook(
    rulebook_directory: Path,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> ValidatedRulebook:
    """Validate a complete rulebook, then atomically persist its policies."""
    validated = validate_rulebook(rulebook_directory)
    repository = SQLitePolicyRepository(database_path)
    repository.save_many(
        policies=validated.policies,
        policy_hashes=validated.policy_hashes,
        transcript_sha256=validated.transcript_sha256,
        imported_at=datetime.now(timezone.utc),
        fact_catalogue=validated.fact_catalogue,
        fact_catalogue_sha256=validated.fact_catalogue_sha256,
    )
    return validated


def build_parser() -> argparse.ArgumentParser:
    """Build the controlled importer argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m dispute_copilot.policy.import_rulebook",
        description=(
            "Validate and atomically import a versioned rulebook."
        ),
    )
    parser.add_argument(
        "rulebook_directory",
        type=Path,
        help="Versioned rulebook directory containing metadata.json.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help="SQLite database path (default: data/dispute_copilot.db).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the controlled rulebook importer."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        imported = import_rulebook(
            args.rulebook_directory,
            args.database,
        )
    except (
        RulebookImportError,
        DuplicatePolicyError,
        ConflictingPolicyKeyError,
        DuplicateFactDefinitionError,
        ConflictingFactDefinitionError,
    ) as error:
        parser.exit(1, f"error: {error}\n")

    print(f"Imported rulebook: {imported.version}")
    print(f"Policies imported: {len(imported.policies)}")
    if imported.fact_catalogue is not None:
        print(
            f"Fact definitions imported: "
            f"{len(imported.fact_catalogue.facts)}"
        )
    print(f"Database: {args.database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
