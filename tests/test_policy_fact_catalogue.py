"""Tests for reviewed, source-aware policy fact semantics."""

from __future__ import annotations

import csv
import inspect
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from dispute_copilot.domain.case import Case
from dispute_copilot.extraction.fact_router import (
    build_evidence_fact_request,
    build_fact_resolution_plan,
)
from dispute_copilot.extraction.metadata_resolver import (
    metadata_resolver_names,
    resolve_case_metadata,
)
from dispute_copilot.intake.hashing import calculate_sha256
from dispute_copilot.orchestration.service import create_case_run
from dispute_copilot.policy.export_fact_review import export_fact_review
from dispute_copilot.policy.fact_catalogue import (
    FactCatalogueValidationError,
    collect_fact_references,
    load_fact_review,
    prohibited_name_reason,
    validate_fact_catalogue,
    validate_fact_definition_semantics,
)
from dispute_copilot.policy.fact_models import (
    FactCatalogue,
    FactDefinition,
)
from dispute_copilot.policy.import_rulebook import (
    RulebookImportError,
    import_rulebook,
    validate_rulebook,
)
from dispute_copilot.policy.repository import (
    ConflictingFactDefinitionError,
    SQLitePolicyRepository,
    SQLitePolicySelectionRepository,
)
from dispute_copilot.policy.rule_models import (
    CanonicalTranscript,
    PolicyRecord,
    RulebookMetadata,
    validate_policy_citations,
)
from dispute_copilot.policy.selection_service import select_policy_for_run
from dispute_copilot.repositories.sqlite import SQLiteCaseRunRepository

PROJECT_ROOT = Path(__file__).parents[1]
CASES_PATH = PROJECT_ROOT / "data" / "cases.json"
EVIDENCE_DIRECTORY = PROJECT_ROOT / "data" / "evidence"
V1_DIRECTORY = (
    PROJECT_ROOT / "data" / "rulebooks" / "take-home-v1"
)
V2_DIRECTORY = (
    PROJECT_ROOT / "data" / "rulebooks" / "take-home-v2"
)


def load_policies(directory: Path) -> tuple[PolicyRecord, ...]:
    return tuple(
        PolicyRecord.model_validate_json(path.read_text())
        for path in sorted((directory / "policies").glob("*.json"))
    )


def load_catalogue() -> FactCatalogue:
    return FactCatalogue.model_validate_json(
        (V2_DIRECTORY / "facts" / "catalogue.json").read_text()
    )


def test_fact_definition_contract_is_strict_and_immutable() -> None:
    definition = FactDefinition(
        fact_name="carrier_reported_delivery_date",
        description="Delivery date asserted by a carrier record.",
        value_type="DATE",
        source_type="EVIDENCE_TEXT_ASSERTION",
        cardinality="SINGLE",
    )

    with pytest.raises(ValidationError, match="Instance is frozen"):
        definition.description = "changed"
    with pytest.raises(ValidationError):
        FactDefinition(
            fact_name="badName",
            description="Invalid name.",
            value_type="DATE",
            source_type="EVIDENCE_TEXT_ASSERTION",
        )
    with pytest.raises(ValidationError):
        FactDefinition(
            fact_name="carrier_reported_delivery_date",
            description="Invalid type.",
            value_type="date",
            source_type="EVIDENCE_TEXT_ASSERTION",
        )
    with pytest.raises(ValidationError):
        FactDefinition(
            fact_name="carrier_reported_delivery_date",
            description="Invalid source.",
            value_type="DATE",
            source_type="LLM",
        )
    with pytest.raises(ValidationError):
        FactDefinition(
            fact_name="carrier_reported_delivery_date",
            description="Invalid cardinality.",
            value_type="DATE",
            source_type="EVIDENCE_TEXT_ASSERTION",
            cardinality="MANY",
        )
    with pytest.raises(ValidationError):
        FactDefinition(
            fact_name="carrier_reported_delivery_date",
            description="Extra field.",
            value_type="DATE",
            source_type="EVIDENCE_TEXT_ASSERTION",
            unsupported=True,
        )


def test_catalogue_is_unique_sorted_and_immutable() -> None:
    catalogue = load_catalogue()

    assert len(catalogue.facts) == 67
    assert [fact.fact_name for fact in catalogue.facts] == sorted(
        fact.fact_name for fact in catalogue.facts
    )
    with pytest.raises(ValidationError, match="Instance is frozen"):
        catalogue.rulebook_version = "changed"
    with pytest.raises(ValidationError, match="unique"):
        FactCatalogue(
            rulebook_version="test",
            facts=(catalogue.facts[0], catalogue.facts[0]),
        )


def test_review_covers_all_51_original_facts_and_usages() -> None:
    original_references = collect_fact_references(
        load_policies(V1_DIRECTORY)
    )
    review = load_fact_review(
        V2_DIRECTORY / "facts" / "fact-review.csv"
    )
    reviewed_references = {
        record.original_fact_name: record.policies_using_it
        for record in review.records
    }

    assert len(original_references) == len(review.records) == 51
    assert reviewed_references == original_references
    assert all(record.definition for record in review.records)
    assert all(record.review_notes for record in review.records)


def test_non_observable_review_decisions_are_not_kept() -> None:
    review = load_fact_review(
        V2_DIRECTORY / "facts" / "fact-review.csv"
    )
    non_observable = {
        "COMPARISON",
        "POLICY_CONCLUSION",
        "RECOMMENDATION",
    }

    for record in review.records:
        if record.classification in non_observable:
            assert record.decision in {"REMOVE", "SPLIT"}


def test_every_final_policy_fact_has_exactly_one_reviewed_definition() -> None:
    policies = load_policies(V2_DIRECTORY)
    catalogue = load_catalogue()
    review = load_fact_review(
        V2_DIRECTORY / "facts" / "fact-review.csv"
    )

    validate_fact_catalogue(
        policies=policies,
        catalogue=catalogue,
        review=review,
        metadata_resolver_names=metadata_resolver_names(),
    )

    referenced = set(collect_fact_references(policies))
    defined = {fact.fact_name for fact in catalogue.facts}
    traced = {
        replacement
        for record in review.records
        for replacement in record.replacement_fact_names
    }
    assert referenced == defined == traced


def test_known_comparisons_conclusions_and_recommendations_are_absent() -> None:
    fact_names = {fact.fact_name for fact in load_catalogue().facts}

    assert fact_names.isdisjoint(
        {
            "description_match_evidence",
            "prior_billing_details_match",
            "issuer_miscoding_evidence",
            "proof_of_delivery",
            "product_matches_listing",
            "should_represent",
        }
    )
    assert all(
        prohibited_name_reason(fact_name) is None
        for fact_name in fact_names
    )


def test_name_lint_is_secondary_to_source_semantics() -> None:
    unsafe = FactDefinition(
        fact_name="valid_delivery",
        description="A hidden delivery conclusion.",
        value_type="BOOLEAN",
        source_type="EVIDENCE_TEXT_ASSERTION",
    )

    assert prohibited_name_reason(unsafe.fact_name) is None
    with pytest.raises(
        FactCatalogueValidationError,
        match="asserting source",
    ):
        validate_fact_definition_semantics(unsafe)


def test_assertions_and_visual_observations_preserve_source_semantics() -> None:
    catalogue = load_catalogue()

    text_facts = [
        fact
        for fact in catalogue.facts
        if fact.source_type == "EVIDENCE_TEXT_ASSERTION"
    ]
    visual_facts = [
        fact
        for fact in catalogue.facts
        if fact.source_type == "EVIDENCE_VISUAL_OBSERVATION"
    ]
    assert text_facts
    assert visual_facts
    assert all(
        any(
            qualifier in fact.fact_name
            for qualifier in (
                "_reported_",
                "_recorded_",
                "_stated_",
                "shipping_record_",
            )
        )
        for fact in text_facts
    )
    assert all(
        fact.fact_name.startswith("image_observed_")
        for fact in visual_facts
    )


def test_catalogue_supports_multi_valued_observations() -> None:
    definitions = {
        fact.fact_name: fact for fact in load_catalogue().facts
    }

    for fact_name in (
        "booking_record_reported_cancellation_date",
        "image_observed_visible_damage",
        "merchant_recorded_prior_transaction_date",
        "merchant_recorded_return_request_date",
        "merchant_reported_notification_date",
    ):
        assert definitions[fact_name].cardinality == "MULTIPLE"


def test_fact_router_is_disjoint_exhaustive_and_order_preserving() -> None:
    catalogue = load_catalogue()
    selected = (
        catalogue.facts[0],
        catalogue.facts[6],
        catalogue.facts[15],
        catalogue.facts[17],
    )

    plan = build_fact_resolution_plan(selected)

    assert plan.case_metadata_facts == (
        catalogue.facts[0],
        catalogue.facts[15],
    )
    assert plan.evidence_facts == (
        catalogue.facts[6],
        catalogue.facts[17],
    )
    assert build_evidence_fact_request(plan) == plan.evidence_facts
    assert set(plan.case_metadata_facts).isdisjoint(plan.evidence_facts)
    assert set(plan.case_metadata_facts) | set(plan.evidence_facts) == (
        set(selected)
    )


def test_case_metadata_is_resolved_directly_from_immutable_run() -> None:
    run = create_case_run(
        "CB-2025-0001",
        CASES_PATH,
        EVIDENCE_DIRECTORY,
    )
    definitions_by_name = {
        definition.fact_name: definition
        for definition in load_catalogue().facts
    }
    requested = tuple(
        definitions_by_name[name]
        for name in (
            "avs_result",
            "chargeback_date",
            "case_recorded_shipping_postcode",
        )
    )
    before = run.model_dump_json()

    resolved = resolve_case_metadata(
        run=run,
        definitions=requested,
    )

    assert [fact.value for fact in resolved] == [
        run.case_snapshot.transaction.avs_result,
        run.case_snapshot.chargeback_date,
        run.case_snapshot.transaction.shipping_address_postcode,
    ]
    assert run.model_dump_json() == before


def test_metadata_resolver_rejects_evidence_facts() -> None:
    run = create_case_run(
        "CB-2025-0001",
        CASES_PATH,
        EVIDENCE_DIRECTORY,
    )
    evidence_fact = next(
        fact
        for fact in load_catalogue().facts
        if fact.source_type == "EVIDENCE_TEXT_ASSERTION"
    )

    with pytest.raises(ValueError, match="cannot enter"):
        resolve_case_metadata(run=run, definitions=(evidence_fact,))


def test_metadata_resolution_has_no_probabilistic_dependencies() -> None:
    import dispute_copilot.extraction.metadata_resolver as resolver

    source = inspect.getsource(resolver).lower()
    for forbidden in ("openai", "ocrprovider", "visionprovider", "pypdf"):
        assert forbidden not in source


def test_review_export_is_deterministic_and_read_only(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    source_hashes_before = {
        path: calculate_sha256(path)
        for path in (V1_DIRECTORY / "policies").glob("*.json")
    }

    first_count = export_fact_review(V1_DIRECTORY, first)
    second_count = export_fact_review(V1_DIRECTORY, second)

    assert first_count == second_count == 51
    assert first.read_bytes() == second.read_bytes()
    with first.open(newline="") as exported:
        assert len(list(csv.DictReader(exported))) == 51
    assert {
        path: calculate_sha256(path)
        for path in source_hashes_before
    } == source_hashes_before


def test_v2_rulebook_schema_hashes_and_citations_are_valid() -> None:
    validated = validate_rulebook(V2_DIRECTORY)
    metadata = RulebookMetadata.model_validate_json(
        (V2_DIRECTORY / "metadata.json").read_text()
    )
    transcript = CanonicalTranscript.model_validate_json(
        (
            V2_DIRECTORY
            / "transcript"
            / "canonical-transcript.json"
        ).read_text()
    )

    assert validated.version == "take-home-v2"
    assert len(validated.policies) == 8
    assert validated.fact_catalogue is not None
    assert len(validated.fact_catalogue.facts) == 67
    assert metadata.fact_catalogue is not None
    assert metadata.fact_review is not None
    assert metadata.fact_catalogue.sha256 == calculate_sha256(
        V2_DIRECTORY / metadata.fact_catalogue.path
    )
    assert metadata.fact_review.sha256 == calculate_sha256(
        V2_DIRECTORY / metadata.fact_review.path
    )
    for policy in validated.policies:
        validate_policy_citations(policy, transcript)


def test_v2_import_round_trip_preserves_all_definitions(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    imported = import_rulebook(V2_DIRECTORY, database_path)
    repository = SQLitePolicyRepository(database_path)

    assert imported.fact_catalogue is not None
    assert repository.list_fact_definitions("take-home-v2") == (
        imported.fact_catalogue.facts
    )
    for definition in imported.fact_catalogue.facts:
        assert repository.get_fact_definition(
            "take-home-v2",
            definition.fact_name,
        ) == definition
    policy = repository.get_imported(
        "take-home-v2",
        "visa",
        "13.3",
    )
    assert policy is not None
    assert policy.fact_catalogue_sha256 == (
        imported.fact_catalogue_sha256
    )


def test_changed_catalogue_or_review_bytes_fail_hash_validation(
    tmp_path: Path,
) -> None:
    catalogue_copy = tmp_path / "catalogue-change"
    review_copy = tmp_path / "review-change"
    shutil.copytree(V2_DIRECTORY, catalogue_copy)
    shutil.copytree(V2_DIRECTORY, review_copy)
    with (catalogue_copy / "facts" / "catalogue.json").open("a") as file:
        file.write("\n")
    with (review_copy / "facts" / "fact-review.csv").open("a") as file:
        file.write("\n")

    with pytest.raises(RulebookImportError, match="catalogue SHA-256"):
        validate_rulebook(catalogue_copy)
    with pytest.raises(RulebookImportError, match="review SHA-256"):
        validate_rulebook(review_copy)


def test_unregistered_policy_fact_fails_before_persistence(
    tmp_path: Path,
) -> None:
    rulebook_copy = tmp_path / "undefined-fact"
    shutil.copytree(V2_DIRECTORY, rulebook_copy)
    policy_path = rulebook_copy / "policies" / "visa-13.3.json"
    policy = json.loads(policy_path.read_text())
    policy["requirements"][0]["required_facts"].append(
        "merchant_stated_unregistered_value"
    )
    policy_path.write_text(json.dumps(policy, indent=2) + "\n")
    metadata_path = rulebook_copy / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    for policy_metadata in metadata["policies"]:
        if policy_metadata["path"] == "policies/visa-13.3.json":
            policy_metadata["sha256"] = calculate_sha256(policy_path)
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    database_path = tmp_path / "test.db"

    with pytest.raises(
        RulebookImportError,
        match="undefined=.*merchant_stated_unregistered_value",
    ):
        import_rulebook(rulebook_copy, database_path)

    repository = SQLitePolicyRepository(database_path)
    assert repository.get("take-home-v2", "visa", "13.3") is None
    assert repository.list_fact_definitions("take-home-v2") == ()


def test_fact_conflict_rolls_back_policy_batch(tmp_path: Path) -> None:
    database_path = tmp_path / "test.db"
    SQLitePolicyRepository(database_path)
    first_definition = load_catalogue().facts[0]
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO fact_definitions (
                rulebook_version,
                fact_name,
                definition_sha256,
                definition_json,
                imported_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "take-home-v2",
                first_definition.fact_name,
                "0" * 64,
                first_definition.model_dump_json(),
                "2026-01-01T00:00:00+00:00",
            ),
        )

    with pytest.raises(ConflictingFactDefinitionError):
        import_rulebook(V2_DIRECTORY, database_path)

    with sqlite3.connect(database_path) as connection:
        policy_count = connection.execute(
            "SELECT COUNT(*) FROM policies"
        ).fetchone()[0]
    assert policy_count == 0


def test_v1_policy_selection_remains_unchanged_after_v2_import(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    import_rulebook(V1_DIRECTORY, database_path)
    run = create_case_run(
        "CB-2025-0001",
        CASES_PATH,
        EVIDENCE_DIRECTORY,
    )
    SQLiteCaseRunRepository(database_path).save(run)
    selection = select_policy_for_run(
        run.run_id,
        "take-home-v1",
        database_path,
    )
    before = selection.model_dump_json()

    import_rulebook(V2_DIRECTORY, database_path)

    retrieved = SQLitePolicySelectionRepository(database_path).get(
        selection.selection_id
    )
    assert retrieved is not None
    assert retrieved.model_dump_json() == before
    assert retrieved.policy_id == "visa-13.1-take-home-v1"


def test_v2_selection_freezes_reviewed_policy_snapshot(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    import_rulebook(V2_DIRECTORY, database_path)
    run = create_case_run(
        "CB-2025-0006",
        CASES_PATH,
        EVIDENCE_DIRECTORY,
    )
    SQLiteCaseRunRepository(database_path).save(run)

    selection = select_policy_for_run(
        run.run_id,
        "take-home-v2",
        database_path,
    )

    required_facts = {
        fact_name
        for requirement in selection.policy_snapshot.requirements
        for fact_name in requirement.required_facts
    }
    assert selection.policy_id == "visa-13.3-take-home-v2"
    assert "image_observed_item_colour" in required_facts
    assert "merchant_stated_product_description" in required_facts
    assert "description_match_evidence" not in required_facts


def test_v2_catalogue_covers_all_supplied_cases(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    import_rulebook(V2_DIRECTORY, database_path)
    repository = SQLitePolicyRepository(database_path)
    cases = [
        Case.model_validate(case_data)
        for case_data in json.loads(CASES_PATH.read_text())
    ]

    assert len(
        {(case.scheme, case.reason_code) for case in cases}
    ) == 8
    for case in cases:
        assert repository.get_imported(
            "take-home-v2",
            case.scheme,
            case.reason_code,
        ) is not None
