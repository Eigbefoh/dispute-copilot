"""Isolated four-case proof through immutable analyst-review packages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from dispute_copilot.analyst_review.service import (
    create_analyst_review_package,
)
from dispute_copilot.evaluation.service import (
    evaluate_selection_requirements,
)
from dispute_copilot.extraction.evidence_extraction_models import (
    ExtractionCandidate,
    ExtractionCandidateValue,
    ExtractionEvidenceReference,
    TextExtractionEvidenceReference,
)
from dispute_copilot.extraction.fact_resolution_service import (
    resolve_selection_facts,
)
from dispute_copilot.extraction.fact_resolution_repository import (
    SQLiteFactResolutionRepository,
)
from dispute_copilot.extraction.fact_resolution_models import (
    EvidenceTextProvenance,
)
from dispute_copilot.extraction.models import EvidenceDocument
from dispute_copilot.extraction.provider_models import ModelExecutionIdentity
from dispute_copilot.human_review.display_adapter import (
    EvidencePresentationAdapter,
)
from dispute_copilot.intake.case_loader import load_case
from dispute_copilot.orchestration.service import create_case_run
from dispute_copilot.policy.fact_models import FactDefinition
from dispute_copilot.policy.import_rulebook import import_rulebook
from dispute_copilot.policy.selection_service import select_policy_for_run
from dispute_copilot.recommendation.service import create_recommendation
from dispute_copilot.repositories.sqlite import SQLiteCaseRunRepository

PROJECT_ROOT = Path(__file__).parents[1]
CASES_PATH = PROJECT_ROOT / "data" / "cases.json"
EVIDENCE_DIRECTORY = PROJECT_ROOT / "data" / "evidence"
RULEBOOK_ROOT = PROJECT_ROOT / "data" / "rulebooks"
RULEBOOK = RULEBOOK_ROOT / "take-home-v3"
MAPPING_ROOT = PROJECT_ROOT / "data" / "recommendation-mappings"
PRESENTATION_ROOT = PROJECT_ROOT / "data" / "presentation-rules"

EXPECTED = {
    "CB-2025-0001": {
        "policy_id": "visa-13.1-take-home-v3",
        "statuses": (
            "SATISFIED",
            "NOT_APPLICABLE",
            "SATISFIED",
            "SATISFIED",
        ),
        "recommendation": "RECOMMEND_REPRESENT",
        "aggregation": "ALL_APPLICABLE",
    },
    "CB-2025-0002": {
        "policy_id": "visa-13.1-take-home-v3",
        "statuses": (
            "NOT_ESTABLISHED",
            "NOT_APPLICABLE",
            "SATISFIED",
            "SATISFIED",
        ),
        "recommendation": "INSUFFICIENT_EVIDENCE_TO_REPRESENT",
        "aggregation": "ALL_APPLICABLE",
    },
    "CB-2025-0003": {
        "policy_id": "mastercard-4837-take-home-v3",
        "statuses": (
            "SATISFIED",
            "SATISFIED",
            "NOT_ESTABLISHED",
            "NOT_ESTABLISHED",
        ),
        "recommendation": "RECOMMEND_REPRESENT",
        "aggregation": "ANY_TWO",
    },
    "CB-2025-0004": {
        "policy_id": "mastercard-4837-take-home-v3",
        "statuses": (
            "NOT_SATISFIED",
            "NOT_SATISFIED",
            "NOT_SATISFIED",
            "NOT_ESTABLISHED",
        ),
        "recommendation": "RECOMMEND_DO_NOT_REPRESENT",
        "aggregation": "ANY_TWO",
    },
}


class CrossCaseVisionProvider:
    provider_version = "fixture-vision/cross-case-1"

    def describe_image(self, *, file_path: Path) -> str:
        assert file_path.name == "CB-2025-0002_tracking_screenshot.png"
        return (
            "DPDx tracking 15441288821100 reports DELIVERED on "
            "10 April 2025 to M1 7DR and says the parcel was left at "
            "the front porch. No signature is shown."
        )


class CrossCaseFixtureExtractor:
    """Test-only deterministic provider response; production stays generic."""

    execution_identity = ModelExecutionIdentity(
        provider="fixture",
        model="cross-case-evidence",
        adapter="fixture-adapter",
        prompt_version="policy-request/1",
        schema_version="fixture-schema/1",
        parameters={},
    )

    def __init__(self, case_id: str) -> None:
        self.case_id = case_id
        self.requested_names: tuple[str, ...] = ()
        self.document_names: tuple[str, ...] = ()

    def extract_evidence_candidates(
        self,
        *,
        requested_facts: tuple[FactDefinition, ...],
        evidence_bundle: object,
        evidence_documents: tuple[EvidenceDocument, ...],
    ) -> tuple[ExtractionCandidate, ...]:
        self.requested_names = tuple(
            fact.fact_name for fact in requested_facts
        )
        self.document_names = tuple(
            document.file_name for document in evidence_documents
        )
        candidates = {
            fact.fact_name: _not_found(fact.fact_name)
            for fact in requested_facts
        }
        documents = {
            document.file_name: document
            for document in evidence_documents
        }
        _populate_case_candidates(
            case_id=self.case_id,
            candidates=candidates,
            documents=documents,
        )
        return tuple(
            candidates[fact.fact_name] for fact in requested_facts
        )


@dataclass(frozen=True)
class CompletedCase:
    case_id: str
    run: object
    selection: object
    resolution: object
    evaluation: object
    recommendation: object
    package: object
    extractor: CrossCaseFixtureExtractor


def _complete_case(case_id: str, database_path: Path) -> CompletedCase:
    run = create_case_run(case_id, CASES_PATH, EVIDENCE_DIRECTORY)
    SQLiteCaseRunRepository(database_path).save(run)
    selection = select_policy_for_run(
        run.run_id,
        "take-home-v3",
        database_path,
    )
    extractor = CrossCaseFixtureExtractor(case_id)
    resolution = resolve_selection_facts(
        selection_id=selection.selection_id,
        extractor=extractor,
        vision_provider=CrossCaseVisionProvider(),
        database_path=database_path,
    )
    evaluation = evaluate_selection_requirements(
        selection_id=selection.selection_id,
        database_path=database_path,
        rulebook_root=RULEBOOK_ROOT,
    )
    recommendation = create_recommendation(
        selection_id=selection.selection_id,
        mapping_key="default-representment-eligibility-v1",
        mapping_version="1.0.0",
        database_path=database_path,
        mapping_root=MAPPING_ROOT,
    )
    package = create_analyst_review_package(
        selection_id=selection.selection_id,
        presentation_rules_key="analyst-review-package-v1",
        presentation_rules_version="1.1.0",
        database_path=database_path,
        presentation_rules_root=PRESENTATION_ROOT,
    )
    return CompletedCase(
        case_id=case_id,
        run=run,
        selection=selection,
        resolution=resolution,
        evaluation=evaluation,
        recommendation=recommendation,
        package=package,
        extractor=extractor,
    )


@pytest.fixture
def completed_cases(tmp_path: Path) -> tuple[Path, dict[str, CompletedCase]]:
    database_path = tmp_path / "cross-case.db"
    import_rulebook(RULEBOOK, database_path)
    completed = {
        case_id: _complete_case(case_id, database_path)
        for case_id in EXPECTED
    }
    return database_path, completed


def test_four_cases_reach_the_policy_driven_acceptance_matrix(
    completed_cases: tuple[Path, dict[str, CompletedCase]],
) -> None:
    _, completed = completed_cases
    for case_id, expected in EXPECTED.items():
        item = completed[case_id]
        assert item.selection.policy_id == expected["policy_id"]
        assert tuple(
            result.status.value
            for result in item.evaluation.ordered_requirement_results
        ) == expected["statuses"]
        assert (
            item.recommendation.recommendation.value
            == expected["recommendation"]
        )
        assert item.evaluation.aggregation_summary is not None
        assert (
            item.evaluation.aggregation_summary.mode
            == expected["aggregation"]
        )
        assert (
            item.package.aggregation.mode == expected["aggregation"]
        )
        assert item.package.case_summary.case_id == case_id

    mastercard_three = completed["CB-2025-0003"]
    assert mastercard_three.evaluation.aggregation_summary.satisfied_count == 2
    assert (
        mastercard_three.evaluation.aggregation_summary.minimum_required == 2
    )
    mastercard_four = completed["CB-2025-0004"]
    assert mastercard_four.evaluation.aggregation_summary.satisfied_count == 0
    assert mastercard_four.evaluation.aggregation_summary.unresolved_count == 1
    assert (
        mastercard_four.evaluation.aggregation_summary
        .maximum_possible_satisfied
        == 1
    )


def test_pairwise_policy_identity_and_cross_case_isolation(
    completed_cases: tuple[Path, dict[str, CompletedCase]],
) -> None:
    _, completed = completed_cases
    for left_id, right_id in (
        ("CB-2025-0001", "CB-2025-0002"),
        ("CB-2025-0003", "CB-2025-0004"),
    ):
        left = completed[left_id]
        right = completed[right_id]
        assert left.selection.policy_id == right.selection.policy_id
        assert (
            left.selection.policy_sha256
            == right.selection.policy_sha256
        )
        assert tuple(
            requirement.requirement_id
            for requirement in left.selection.policy_snapshot.requirements
        ) == tuple(
            requirement.requirement_id
            for requirement in right.selection.policy_snapshot.requirements
        )
        assert left.run.run_id != right.run.run_id
        assert left.selection.selection_id != right.selection.selection_id
        assert left.resolution.resolution_id != right.resolution.resolution_id
        assert left.package.review_package_id != right.package.review_package_id

    for case_id, item in completed.items():
        declared = set(
            load_case(case_id, CASES_PATH).merchant_evidence_documents
        )
        assert set(item.extractor.document_names) == declared
        evidence_sources = {
            provenance.file_name
            for fact in item.resolution.facts
            for value in fact.resolved_values
            for provenance in value.provenance
            if isinstance(provenance, EvidenceTextProvenance)
        }
        assert evidence_sources <= declared


def test_typed_evidence_dates_survive_json_persistence(
    completed_cases: tuple[Path, dict[str, CompletedCase]],
) -> None:
    database_path, completed = completed_cases
    source = completed["CB-2025-0001"]
    reloaded = SQLiteFactResolutionRepository(database_path).get(
        source.resolution.resolution_id
    )
    assert reloaded is not None
    delivery_date = next(
        fact
        for fact in reloaded.facts
        if fact.fact_name == "carrier_reported_delivery_date"
    )
    assert type(delivery_date.resolved_values[0].value) is date


def test_every_package_uses_the_same_integrity_checked_display_adapter(
    completed_cases: tuple[Path, dict[str, CompletedCase]],
) -> None:
    database_path, completed = completed_cases
    adapter = EvidencePresentationAdapter(
        database_path=database_path,
        project_root=PROJECT_ROOT,
    )
    for item in completed.values():
        for source in item.package.supporting_sources:
            if source.source_document is None:
                continue
            target = adapter.for_source(
                review_package_id=item.package.review_package_id,
                source_ref_id=source.source_ref_id,
            )
            assert target.target.case_id == item.case_id
            assert target.target.source_file_name == source.source_document
            assert target.target.bounding_boxes == ()


def test_cb_2025_0002_keeps_delivery_and_attribution_distinct(
    completed_cases: tuple[Path, dict[str, CompletedCase]],
) -> None:
    _, completed = completed_cases
    item = completed["CB-2025-0002"]
    first = item.evaluation.ordered_requirement_results[0]
    fourth = item.evaluation.ordered_requirement_results[3]
    assert first.status.value == "NOT_ESTABLISHED"
    assert "MATERIAL_ADDRESS_ATTRIBUTION_CONFLICT" in first.basis_codes
    assert "carrier_reported_signature_name" in (
        first.missing_or_unresolved_facts
    )
    assert fourth.status.value == "SATISFIED"
    source_names = {
        source.fact_name for source in item.package.supporting_sources
    }
    assert {
        "carrier_reported_delivery_address",
        "case_recorded_billing_postcode",
        "case_recorded_shipping_postcode",
        "avs_result",
        "case_recorded_issuer_narrative",
    } <= source_names


def test_cb_2025_0004_opinion_does_not_create_a_fifth_route(
    completed_cases: tuple[Path, dict[str, CompletedCase]],
) -> None:
    _, completed = completed_cases
    item = completed["CB-2025-0004"]
    requested = set(item.resolution.requested_facts)
    assert "merchant_recorded_first_transaction" in requested
    assert not any("legitimate" in fact_name for fact_name in requested)
    assert tuple(
        result.status.value
        for result in item.evaluation.ordered_requirement_results
    ) == EXPECTED["CB-2025-0004"]["statuses"]
    assert {
        source.source_document
        for source in item.package.supporting_sources
        if source.source_document is not None
    } == {"CB-2025-0004_internal_fraud_report.pdf"}


def test_execution_order_does_not_change_semantic_outcomes(
    tmp_path: Path,
) -> None:
    orders = (
        tuple(EXPECTED),
        tuple(reversed(tuple(EXPECTED))),
    )
    outcomes = []
    for index, order in enumerate(orders):
        database_path = tmp_path / f"order-{index}.db"
        import_rulebook(RULEBOOK, database_path)
        completed = {
            case_id: _complete_case(case_id, database_path)
            for case_id in order
        }
        outcomes.append(
            {
                case_id: (
                    completed[case_id].selection.policy_sha256,
                    tuple(
                        item.status.value
                        for item in completed[
                            case_id
                        ].evaluation.ordered_requirement_results
                    ),
                    completed[
                        case_id
                    ].recommendation.recommendation.value,
                )
                for case_id in EXPECTED
            }
        )
    assert outcomes[0] == outcomes[1]


def _populate_case_candidates(
    *,
    case_id: str,
    candidates: dict[str, ExtractionCandidate],
    documents: dict[str, EvidenceDocument],
) -> None:
    if case_id == "CB-2025-0001":
        order = documents["CB-2025-0001_carrier_tracking.pdf"]
        delivery = documents["CB-2025-0001_delivery_confirmation.pdf"]
        candidates.update(
            {
                "carrier_reported_tracking_number": _found(
                    "carrier_reported_tracking_number",
                    "RM98421144GB",
                    _text(delivery, "Tracking number: RM98421144GB"),
                ),
                "shipping_record_carrier_name": _found(
                    "shipping_record_carrier_name",
                    "Royal Mail Tracked 24",
                    _text(
                        order,
                        "Dispatched 23 March 2025 via Royal Mail Tracked 24",
                    ),
                ),
                "carrier_reported_delivery_status": _found(
                    "carrier_reported_delivery_status",
                    "DELIVERED",
                    _text(delivery, "DELIVERED — signed by WHITFORD"),
                ),
                "carrier_reported_delivery_address": _found(
                    "carrier_reported_delivery_address",
                    "22 Cavendish Court, London SW4 7QR",
                    _text(
                        delivery,
                        "22 Cavendish Court\nLondon SW4 7QR",
                    ),
                ),
                "carrier_reported_signature_name": _found(
                    "carrier_reported_signature_name",
                    "WHITFORD",
                    _text(delivery, "DELIVERED — signed by WHITFORD"),
                ),
                "carrier_reported_delivery_date": _found(
                    "carrier_reported_delivery_date",
                    "2025-03-24",
                    _text(delivery, "24 Mar 2025 11:47"),
                ),
            }
        )
        return
    if case_id == "CB-2025-0002":
        screenshot = documents["CB-2025-0002_tracking_screenshot.png"]
        support = screenshot.pages[0].text
        candidates.update(
            {
                "carrier_reported_tracking_number": _found(
                    "carrier_reported_tracking_number",
                    "15441288821100",
                    _text(screenshot, support),
                ),
                "shipping_record_carrier_name": _found(
                    "shipping_record_carrier_name",
                    "DPDx",
                    _text(screenshot, support),
                ),
                "carrier_reported_delivery_status": _found(
                    "carrier_reported_delivery_status",
                    "DELIVERED",
                    _text(screenshot, support),
                ),
                "carrier_reported_delivery_address": _found(
                    "carrier_reported_delivery_address",
                    "M1 7DR",
                    _text(screenshot, support),
                ),
                "carrier_reported_delivery_date": _found(
                    "carrier_reported_delivery_date",
                    "2025-04-10",
                    _text(screenshot, support),
                ),
            }
        )
        return
    if case_id == "CB-2025-0003":
        gateway = documents["CB-2025-0003_avs_cvv_log.pdf"]
        authentication = documents[
            "CB-2025-0003_3ds_authentication.pdf"
        ]
        candidates.update(
            {
                "merchant_recorded_avs_result": _found(
                    "merchant_recorded_avs_result",
                    "Y",
                    _text(gateway, "AVS check (full address)\nY\nFull match"),
                ),
                "merchant_recorded_cvv_result": _found(
                    "merchant_recorded_cvv_result",
                    "M",
                    _text(gateway, "CVV check\nM\nMatch"),
                ),
                "merchant_recorded_authentication_program": _found(
                    "merchant_recorded_authentication_program",
                    "Mastercard Identity Check",
                    _text(
                        authentication,
                        "Mastercard Identity Check — Authentication Record",
                    ),
                ),
                "merchant_recorded_authentication_status": _found(
                    "merchant_recorded_authentication_status",
                    "AUTHENTICATED",
                    _text(authentication, "AUTHENTICATED (frictionless)"),
                ),
            }
        )
        return
    if case_id == "CB-2025-0004":
        report = documents["CB-2025-0004_internal_fraud_report.pdf"]
        candidates["merchant_recorded_first_transaction"] = _found(
            "merchant_recorded_first_transaction",
            True,
            _text(report, "New (first transaction)"),
        )
        return
    raise AssertionError(f"unsupported test case: {case_id}")


def _text(
    document: EvidenceDocument,
    supporting_text: str,
) -> ExtractionEvidenceReference:
    return TextExtractionEvidenceReference(
        evidence_type="TEXT",
        document_id=document.document_id,
        file_name=document.file_name,
        page_number=1,
        extraction_method=document.pages[0].extraction_method.value,
        supporting_text=supporting_text,
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
        confidence=0.99,
    )


def _not_found(fact_name: str) -> ExtractionCandidate:
    return ExtractionCandidate(
        fact_name=fact_name,
        status="NOT_FOUND",
        values=(),
        confidence=None,
    )
