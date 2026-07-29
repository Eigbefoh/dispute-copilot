"""Repository-backed proof of the lean CB-2025-0006 extraction path."""

from __future__ import annotations

import os
import sqlite3
import json
from pathlib import Path
from uuid import uuid4

import pytest

from dispute_copilot.config import load_model_provider_settings
from dispute_copilot.extraction.bundle_loader import load_evidence_bundle
from dispute_copilot.extraction.evidence_extraction_models import (
    ExtractionCandidate,
    ExtractionCandidateValue,
    ExtractionEvidenceReference,
    TextExtractionEvidenceReference,
    VisualExtractionEvidenceReference,
)
from dispute_copilot.extraction.document_converter import DocumentConverter
from dispute_copilot.extraction.fact_resolution_service import (
    resolve_case_facts,
    resolve_selection_facts,
)
from dispute_copilot.extraction.fact_resolution_repository import (
    SQLiteFactResolutionRepository,
)
from dispute_copilot.extraction.models import EvidenceDocument
from dispute_copilot.extraction.provider_models import ModelExecutionIdentity
from dispute_copilot.extraction.provider_runtime import (
    create_extraction_runtime,
)
from dispute_copilot.orchestration.service import create_case_run
from dispute_copilot.policy.fact_models import (
    FactCatalogue,
    FactDefinition,
)
from dispute_copilot.policy.import_rulebook import import_rulebook
from dispute_copilot.policy.repository import (
    SQLitePolicyRepository,
    SQLitePolicySelectionRepository,
)
from dispute_copilot.policy.selection_service import select_policy_for_run
from dispute_copilot.repositories.sqlite import SQLiteCaseRunRepository
from dispute_copilot.evaluation.repository import (
    SQLiteEvaluationRepository,
)
from dispute_copilot.evaluation.errors import (
    EvaluationInputError,
    PredicateContractError,
)
from dispute_copilot.evaluation.guards import validate_evaluation_inputs
from dispute_copilot.evaluation.service import (
    evaluate_selection_requirements,
)
from dispute_copilot.recommendation.repository import (
    SQLiteRecommendationRepository,
)
from dispute_copilot.recommendation.errors import MappingContractError
from dispute_copilot.recommendation.service import create_recommendation

PROJECT_ROOT = Path(__file__).parents[1]
CASES_PATH = PROJECT_ROOT / "data" / "cases.json"
EVIDENCE_DIRECTORY = PROJECT_ROOT / "data" / "evidence"
RULEBOOK_DIRECTORY = (
    PROJECT_ROOT / "data" / "rulebooks" / "take-home-v2"
)

EXPECTED_FACTS = (
    "merchant_stated_product_description",
    "image_observed_item_colour",
    "image_observed_item_type",
    "image_observed_visible_damage",
    "image_observed_serial_number",
    "merchant_stated_return_method",
    "merchant_stated_return_window_days",
    "merchant_recorded_return_request_date",
    "merchant_recorded_post_complaint_usage_date",
    "merchant_recorded_merchandise_retention_status",
)


class FixtureVisionProvider:
    provider_version = "fixture-vision/lean-1"

    def describe_image(self, *, file_path: Path) -> str:
        assert file_path.name == "CB-2025-0006_delivery_photo.png"
        return (
            "A front-view delivery photograph shows a sage-coloured "
            "armchair. No serial number or visible damage is established."
        )


class FixtureEvidenceExtractor:
    execution_identity = ModelExecutionIdentity(
        provider="fixture",
        model="fixture-model",
        adapter="fixture-adapter",
        prompt_version="fixture-prompt/1",
        schema_version="fixture-schema/1",
        parameters={},
    )

    def __init__(self) -> None:
        self.requested_facts: tuple[FactDefinition, ...] = ()
        self.document_order: tuple[str, ...] = ()

    def extract_evidence_candidates(
        self,
        *,
        requested_facts: tuple[FactDefinition, ...],
        evidence_bundle: object,
        evidence_documents: tuple[EvidenceDocument, ...],
    ) -> tuple[ExtractionCandidate, ...]:
        self.requested_facts = requested_facts
        self.document_order = tuple(
            document.file_name for document in evidence_documents
        )
        documents = {
            document.file_name: document
            for document in evidence_documents
        }
        listing = documents["CB-2025-0006_product_listing.pdf"]
        photo = documents["CB-2025-0006_delivery_photo.png"]
        returns = documents["CB-2025-0006_returns_policy.pdf"]

        listing_quote = (
            "Product: Carlton Mid-Century Armchair, Sage Velvet"
        )
        return_method_quote = (
            "To initiate a return, contact\n"
            "returns@havenhome.example with your order number."
        )
        return_window_quote = (
            "you may return it within 14 days of delivery"
        )
        visual_observation = (
            "A front-view delivery photograph shows a sage-coloured "
            "armchair."
        )

        candidates = {
            "merchant_stated_product_description": _found(
                "merchant_stated_product_description",
                "Carlton Mid-Century Armchair, Sage Velvet",
                _text_reference(listing, listing_quote),
            ),
            "image_observed_item_colour": _found(
                "image_observed_item_colour",
                "sage",
                _visual_reference(photo, visual_observation),
            ),
            "image_observed_item_type": _found(
                "image_observed_item_type",
                "armchair",
                _visual_reference(photo, visual_observation),
            ),
            "image_observed_visible_damage": _not_found(
                "image_observed_visible_damage"
            ),
            "image_observed_serial_number": _not_found(
                "image_observed_serial_number"
            ),
            "merchant_stated_return_method": _found(
                "merchant_stated_return_method",
                "email returns@havenhome.example",
                _text_reference(returns, return_method_quote),
            ),
            "merchant_stated_return_window_days": _found(
                "merchant_stated_return_window_days",
                14,
                _text_reference(returns, return_window_quote),
            ),
            "merchant_recorded_return_request_date": _not_found(
                "merchant_recorded_return_request_date"
            ),
            "merchant_recorded_post_complaint_usage_date": _not_found(
                "merchant_recorded_post_complaint_usage_date"
            ),
            "merchant_recorded_merchandise_retention_status": _not_found(
                "merchant_recorded_merchandise_retention_status"
            ),
        }
        return tuple(
            candidates[definition.fact_name]
            for definition in requested_facts
        )


def _text_reference(
    document: EvidenceDocument,
    supporting_text: str,
) -> ExtractionEvidenceReference:
    return TextExtractionEvidenceReference(
        evidence_type="TEXT",
        document_id=document.document_id,
        file_name=document.file_name,
        page_number=1,
        extraction_method="NATIVE_TEXT",
        supporting_text=supporting_text,
    )


def _visual_reference(
    document: EvidenceDocument,
    observation: str,
) -> ExtractionEvidenceReference:
    return VisualExtractionEvidenceReference(
        evidence_type="VISUAL",
        document_id=document.document_id,
        file_name=document.file_name,
        page_number=1,
        extraction_method="VISION",
        visual_observation=observation,
    )


def _found(
    fact_name: str,
    raw_value: object,
    reference: ExtractionEvidenceReference,
) -> ExtractionCandidate:
    return ExtractionCandidate(
        fact_name=fact_name,
        status="FOUND",
        values=(
            ExtractionCandidateValue(
                raw_value=raw_value,
                references=(reference,),
            ),
        ),
        confidence=0.95,
    )


def _not_found(fact_name: str) -> ExtractionCandidate:
    return ExtractionCandidate(
        fact_name=fact_name,
        status="NOT_FOUND",
        values=(),
        confidence=None,
    )


def _create_v2_selection(database_path: Path) -> tuple[object, object]:
    import_rulebook(RULEBOOK_DIRECTORY, database_path)
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
    return run, selection


def test_cb_2025_0006_reaches_ready_for_evaluation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "test.db"
    run, selection = _create_v2_selection(database_path)
    run_bytes_before = run.model_dump_json()
    selection_bytes_before = selection.model_dump_json()
    extractor = FixtureEvidenceExtractor()

    result = resolve_selection_facts(
        selection_id=selection.selection_id,
        extractor=extractor,
        vision_provider=FixtureVisionProvider(),
        database_path=database_path,
    )

    assert result.status == "READY_FOR_EVALUATION"
    assert result.requested_facts == EXPECTED_FACTS
    assert tuple(fact.fact_name for fact in result.facts) == EXPECTED_FACTS
    identity = json.loads(result.resolver_version)
    assert identity == {
        "adapter": "fixture-adapter",
        "model": "fixture-model",
        "parameters": {},
        "prompt_version": "fixture-prompt/1",
        "provider": "fixture",
        "schema_version": "fixture-schema/1",
    }
    assert all(
        definition.source_type != "CASE_METADATA"
        for definition in extractor.requested_facts
    )
    assert tuple(
        definition.fact_name
        for definition in extractor.requested_facts
    ) == EXPECTED_FACTS
    assert extractor.document_order == (
        "CB-2025-0006_product_listing.pdf",
        "CB-2025-0006_delivery_photo.png",
        "CB-2025-0006_returns_policy.pdf",
    )

    retrieved_run = SQLiteCaseRunRepository(database_path).get(run.run_id)
    retrieved_selection = SQLitePolicySelectionRepository(
        database_path
    ).get(selection.selection_id)
    assert retrieved_run is not None
    assert retrieved_selection is not None
    assert retrieved_run.model_dump_json() == run_bytes_before
    assert (
        retrieved_selection.model_dump_json()
        == selection_bytes_before
    )

    with sqlite3.connect(database_path) as connection:
        document_count = connection.execute(
            "SELECT COUNT(*) FROM evidence_documents"
        ).fetchone()[0]
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert document_count == 3
    assert "fact_resolution_results" in table_names
    persisted_resolution = SQLiteFactResolutionRepository(
        database_path
    ).get_for_selection(selection.selection_id)
    assert persisted_resolution == result
    assert resolve_selection_facts(
        selection_id=selection.selection_id,
        extractor=object(),  # type: ignore[arg-type]
        vision_provider=object(),  # type: ignore[arg-type]
        database_path=database_path,
    ) == result

    policy_repository = SQLitePolicyRepository(database_path)
    imported_policy = policy_repository.get_imported(
        selection.rulebook_version,
        selection.scheme,
        selection.reason_code,
    )
    assert imported_policy is not None
    catalogue = FactCatalogue(
        rulebook_version=selection.rulebook_version,
        facts=policy_repository.list_fact_definitions(
            selection.rulebook_version
        ),
    )
    with pytest.raises(EvaluationInputError, match="lineage"):
        validate_evaluation_inputs(
            selection=selection,
            resolution=result.model_copy(
                update={"selection_id": uuid4()}
            ),
            imported_policy=imported_policy,
            catalogue=catalogue,
        )

    with pytest.raises(PredicateContractError):
        evaluate_selection_requirements(
            selection_id=selection.selection_id,
            database_path=database_path,
            rulebook_root=tmp_path / "missing-rulebooks",
        )
    failed_repository = SQLiteEvaluationRepository(database_path)
    assert failed_repository.get_for_selection(
        selection.selection_id
    ) is None
    assert failed_repository.effective_status(
        selection.selection_id
    ) is None

    original_read_bytes = Path.read_bytes
    evaluation_file_reads: list[Path] = []

    def permit_only_predicate_manifest(path: Path) -> bytes:
        evaluation_file_reads.append(path)
        assert path.name in {"predicates.json", "manifest.json"}
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", permit_only_predicate_manifest)

    evaluation = evaluate_selection_requirements(
        selection_id=selection.selection_id,
        database_path=database_path,
        rulebook_root=PROJECT_ROOT / "data" / "rulebooks",
    )

    assert tuple(
        item.requirement_id
        for item in evaluation.ordered_requirement_results
    ) == ("V13.3-R1", "V13.3-R2", "V13.3-R3")
    assert tuple(
        item.status.value
        for item in evaluation.ordered_requirement_results
    ) == ("SATISFIED", "NOT_ESTABLISHED", "NOT_ESTABLISHED")
    assert evaluation.overall_status.value == (
        "ONE_OR_MORE_REQUIREMENTS_NOT_ESTABLISHED"
    )
    assert evaluation.status == "READY_FOR_RECOMMENDATION"
    assert [path.name for path in evaluation_file_reads] == [
        "predicates.json"
    ]
    evaluation_repository = SQLiteEvaluationRepository(database_path)
    assert evaluation_repository.effective_status(
        selection.selection_id
    ) == (
        "READY_FOR_RECOMMENDATION"
    )
    assert evaluate_selection_requirements(
        selection_id=selection.selection_id,
        database_path=database_path,
        rulebook_root=PROJECT_ROOT / "data" / "rulebooks",
    ) == evaluation
    assert not hasattr(evaluation, "recommendation")
    assert not hasattr(evaluation, "decision")

    second_requirement = evaluation.ordered_requirement_results[1]
    assert second_requirement.missing_or_unresolved_facts == (
        "image_observed_visible_damage",
        "image_observed_serial_number",
    )
    assert {
        binding.fact_name
        for binding in second_requirement.fact_bindings
    } == {
        "image_observed_item_colour",
        "image_observed_item_type",
        "image_observed_visible_damage",
        "image_observed_serial_number",
    }

    with sqlite3.connect(database_path) as connection:
        evaluation_count = connection.execute(
            "SELECT COUNT(*) FROM requirement_evaluations"
        ).fetchone()[0]
        event_count = connection.execute(
            "SELECT COUNT(*) FROM workflow_events"
        ).fetchone()[0]
    assert evaluation_count == 1
    assert event_count == 1

    assert SQLiteCaseRunRepository(
        database_path
    ).get(run.run_id).model_dump_json() == run_bytes_before
    assert SQLitePolicySelectionRepository(
        database_path
    ).get(selection.selection_id).model_dump_json() == (
        selection_bytes_before
    )

    def reject_fact_repository_access(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "recommendation must not access FactResolutionResult"
        )

    monkeypatch.setattr(
        SQLiteFactResolutionRepository,
        "get_for_selection",
        reject_fact_repository_access,
    )

    with pytest.raises(MappingContractError):
        create_recommendation(
            selection_id=selection.selection_id,
            mapping_key="missing-explicit-mapping",
            mapping_version="1.0.0",
            database_path=database_path,
            mapping_root=(
                PROJECT_ROOT / "data" / "recommendation-mappings"
            ),
        )
    failed_recommendation_repository = (
        SQLiteRecommendationRepository(database_path)
    )
    assert failed_recommendation_repository.get_for_selection(
        selection.selection_id
    ) is None
    assert failed_recommendation_repository.effective_status(
        selection.selection_id
    ) == "READY_FOR_RECOMMENDATION"

    recommendation = create_recommendation(
        selection_id=selection.selection_id,
        mapping_key="default-representment-eligibility-v1",
        mapping_version="1.0.0",
        database_path=database_path,
        mapping_root=(
            PROJECT_ROOT / "data" / "recommendation-mappings"
        ),
    )

    assert recommendation.recommendation.value == (
        "INSUFFICIENT_EVIDENCE_TO_REPRESENT"
    )
    assert tuple(
        item.requirement_id
        for item in recommendation.causal_requirement_results
    ) == ("V13.3-R2", "V13.3-R3")
    assert recommendation.status == "READY_FOR_ANALYST_REVIEW"
    assert not hasattr(recommendation, "facts")
    assert not hasattr(recommendation, "provenance")
    assert [path.name for path in evaluation_file_reads] == [
        "predicates.json",
        "manifest.json",
        "manifest.json",
    ]
    assert create_recommendation(
        evaluation_id=evaluation.evaluation_id,
        mapping_key="default-representment-eligibility-v1",
        mapping_version="1.0.0",
        database_path=database_path,
        mapping_root=(
            PROJECT_ROOT / "data" / "recommendation-mappings"
        ),
    ) == recommendation

    recommendation_repository = SQLiteRecommendationRepository(
        database_path
    )
    assert recommendation_repository.effective_status(
        selection.selection_id
    ) == "READY_FOR_ANALYST_REVIEW"
    with sqlite3.connect(database_path) as connection:
        recommendation_count = connection.execute(
            "SELECT COUNT(*) FROM recommendation_results"
        ).fetchone()[0]
        final_event_count = connection.execute(
            "SELECT COUNT(*) FROM workflow_events"
        ).fetchone()[0]
    assert recommendation_count == 1
    assert final_event_count == 2
    assert "requirement_results" not in table_names
    assert "recommendations" not in table_names


def test_metadata_and_evidence_are_combined_in_policy_order(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "mixed.db"
    run, selection = _create_v2_selection(database_path)
    first_requirement = (
        selection.policy_snapshot.requirements[0].model_copy(
            update={
                "required_facts": (
                    "avs_result",
                    "merchant_stated_product_description",
                )
            }
        )
    )
    policy_snapshot = selection.policy_snapshot.model_copy(
        update={"requirements": (first_requirement,)}
    )
    mixed_selection = selection.model_copy(
        update={"policy_snapshot": policy_snapshot}
    )
    bundle = load_evidence_bundle(
        selection_id=selection.selection_id,
        database_path=database_path,
    )
    documents = tuple(
        DocumentConverter(
            vision_provider=FixtureVisionProvider()
        ).convert(evidence_file)
        for evidence_file in bundle.evidence_files
    )
    catalogue = FactCatalogue.model_validate_json(
        (
            RULEBOOK_DIRECTORY / "facts" / "catalogue.json"
        ).read_text()
    )
    extractor = FixtureEvidenceExtractor()

    result = resolve_case_facts(
        case_run=run,
        selection=mixed_selection,
        catalogue=catalogue,
        evidence_bundle=bundle,
        evidence_documents=documents,
        extractor=extractor,
    )

    assert result.requested_facts == (
        "avs_result",
        "merchant_stated_product_description",
    )
    assert tuple(fact.resolver_type for fact in result.facts) == (
        "CASE_METADATA",
        "EVIDENCE_EXTRACTION",
    )
    assert tuple(
        definition.fact_name
        for definition in extractor.requested_facts
    ) == ("merchant_stated_product_description",)


LIVE_PROVIDER_CONFIGURED = (
    os.getenv("RUN_MODEL_PROVIDER_INTEGRATION") == "1"
    and bool(os.getenv("MODEL_PROVIDER"))
    and bool(os.getenv("MODEL_API_KEY"))
    and bool(os.getenv("MODEL_NAME"))
)


@pytest.mark.integration
@pytest.mark.skipif(
    not LIVE_PROVIDER_CONFIGURED,
    reason=(
        "live provider integration requires explicit enablement and "
        "MODEL_PROVIDER, MODEL_API_KEY, and MODEL_NAME"
    ),
)
def test_real_provider_cb_2025_0006_contract(tmp_path: Path) -> None:
    database_path = tmp_path / "real-model.db"
    _, selection = _create_v2_selection(database_path)
    settings = load_model_provider_settings()
    runtime = create_extraction_runtime(settings)

    result = resolve_selection_facts(
        selection_id=selection.selection_id,
        extractor=runtime.extractor,
        vision_provider=runtime.vision_provider,
        database_path=database_path,
    )

    assert result.status == "READY_FOR_EVALUATION"
    assert result.requested_facts == EXPECTED_FACTS
    assert tuple(fact.fact_name for fact in result.facts) == EXPECTED_FACTS
