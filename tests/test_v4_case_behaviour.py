"""Fixture-backed proof of the approved take-home-v4 case behaviour."""

from __future__ import annotations

from pathlib import Path

from dispute_copilot.application.models import ExecutionMode, ExecutionStatus
from dispute_copilot.application.service import DisputeCopilotApplication
from dispute_copilot.extraction.evidence_extraction_models import (
    ExtractionCandidate,
    ExtractionCandidateValue,
    ExtractionEvidenceReference,
    TextExtractionEvidenceReference,
    VisualExtractionEvidenceReference,
)
from dispute_copilot.extraction.models import EvidenceDocument
from dispute_copilot.extraction.provider_models import ModelExecutionIdentity
from dispute_copilot.extraction.provider_runtime import ExtractionRuntime
from dispute_copilot.policy.fact_models import FactDefinition

PROJECT_ROOT = Path(__file__).parents[1]

EXPECTED = {
    "CB-2025-0001": "RECOMMEND_REPRESENT",
    "CB-2025-0002": "INSUFFICIENT_EVIDENCE_TO_REPRESENT",
    "CB-2025-0003": "RECOMMEND_REPRESENT",
    "CB-2025-0004": "RECOMMEND_DO_NOT_REPRESENT",
    "CB-2025-0005": "RECOMMEND_REPRESENT",
    "CB-2025-0006": "INSUFFICIENT_EVIDENCE_TO_REPRESENT",
    "CB-2025-0007": "RECOMMEND_REPRESENT",
    "CB-2025-0008": "INSUFFICIENT_EVIDENCE_TO_REPRESENT",
    "CB-2025-0009": "RECOMMEND_REPRESENT",
    "CB-2025-0010": "RECOMMEND_DO_NOT_REPRESENT",
}
EXPECTED_STATUSES = {
    "CB-2025-0001": (
        "SATISFIED",
        "NOT_APPLICABLE",
        "SATISFIED",
        "SATISFIED",
    ),
    "CB-2025-0002": (
        "NOT_ESTABLISHED",
        "NOT_APPLICABLE",
        "SATISFIED",
        "SATISFIED",
    ),
    "CB-2025-0003": (
        "SATISFIED",
        "SATISFIED",
        "NOT_ESTABLISHED",
        "NOT_ESTABLISHED",
    ),
    "CB-2025-0004": (
        "NOT_SATISFIED",
        "NOT_SATISFIED",
        "NOT_SATISFIED",
        "NOT_ESTABLISHED",
    ),
    "CB-2025-0005": ("SATISFIED", "SATISFIED", "SATISFIED"),
    "CB-2025-0006": (
        "SATISFIED",
        "NOT_ESTABLISHED",
        "NOT_ESTABLISHED",
    ),
    "CB-2025-0007": ("SATISFIED", "SATISFIED", "SATISFIED"),
    "CB-2025-0008": (
        "SATISFIED",
        "NOT_ESTABLISHED",
        "NOT_ESTABLISHED",
        "NOT_ESTABLISHED",
    ),
    "CB-2025-0009": (
        "SATISFIED",
        "SATISFIED",
        "SATISFIED",
        "SATISFIED",
    ),
    "CB-2025-0010": ("NOT_ESTABLISHED",),
}


class V4VisionProvider:
    provider_version = "fixture-vision/v4-acceptance-1"

    def describe_image(self, *, file_path: Path) -> str:
        if file_path.name == "CB-2025-0002_tracking_screenshot.png":
            return (
                "DPDx tracking 15441288821100 reports DELIVERED on "
                "10 April 2025 to M1 7DR. No order reference or signature "
                "is displayed."
            )
        if file_path.name == "CB-2025-0006_delivery_photo.png":
            return (
                "HavenHome — Delivery Photo\n"
                "Carlton Armchair (Sage) — delivered 18 Apr 2025\n"
                "Photo taken by driver at delivery (front view only)"
            )
        raise AssertionError(file_path.name)


class V4FixtureExtractor:
    execution_identity = ModelExecutionIdentity(
        provider="fixture",
        model="v4-case-acceptance",
        adapter="fixture-adapter",
        prompt_version="fixture-prompt/v4",
        schema_version="fixture-schema/v4",
        parameters={},
    )

    def extract_evidence_candidates(
        self,
        *,
        requested_facts: tuple[FactDefinition, ...],
        evidence_bundle: object,
        evidence_documents: tuple[EvidenceDocument, ...],
    ) -> tuple[ExtractionCandidate, ...]:
        documents = {
            document.file_name: document
            for document in evidence_documents
        }
        case_id = next(iter(documents)).split("_", 1)[0]
        candidates = {
            definition.fact_name: _not_found(definition.fact_name)
            for definition in requested_facts
        }
        _populate(case_id, candidates, documents)
        return tuple(
            candidates[definition.fact_name]
            for definition in requested_facts
        )


def _runtime() -> ExtractionRuntime:
    return ExtractionRuntime(
        extractor=V4FixtureExtractor(),
        vision_provider=V4VisionProvider(),
    )


def test_default_application_executes_take_home_v4_end_to_end(
    tmp_path: Path,
) -> None:
    application = DisputeCopilotApplication(
        database_path=tmp_path / "default-v4.db",
        project_root=PROJECT_ROOT,
        runtime_factory=_runtime,
    )

    result = application.run_case("CB-2025-0004")

    assert result.package is not None
    assert result.package.case_summary.policy_version == "take-home-v4"
    assert result.package.case_summary.policy_key == (
        "mastercard-4837-take-home-v4"
    )
    assert result.package.recommendation.decision.value == (
        "RECOMMEND_DO_NOT_REPRESENT"
    )


def test_all_ten_cases_match_the_approved_v4_behaviour(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "v4-acceptance.db"
    application = DisputeCopilotApplication(
        database_path=database_path,
        project_root=PROJECT_ROOT,
        rulebook_version="take-home-v4",
        runtime_factory=_runtime,
    )

    events = tuple(application.run_all_cases())

    assert len(events) == 30
    for case_id, expected in EXPECTED.items():
        view = application.get_case_view(case_id)
        assert view.status is ExecutionStatus.COMPLETED
        assert view.package is not None
        assert view.package.recommendation.decision.value == expected
        assert tuple(
            item.status.value
            for item in view.package.requirement_checklist
        ) == EXPECTED_STATUSES[case_id]
        assert view.package.case_summary.policy_version == "take-home-v4"
        assert view.suggested_action is not None

    case_four = application.get_case_view("CB-2025-0004")
    assert tuple(
        item.status.value
        for item in case_four.package.requirement_checklist
    ) == (
        "NOT_SATISFIED",
        "NOT_SATISFIED",
        "NOT_SATISFIED",
        "NOT_ESTABLISHED",
    )
    assert "confidence" not in case_four.resolution.requested_facts
    assert case_four.suggested_action.action.value == (
        "REQUEST_SPECIFIC_EVIDENCE"
    )
    assert "MC4837-R4" in (
        case_four.suggested_action.causal_requirement_ids
    )

    case_six = application.get_case_view("CB-2025-0006")
    visible_names = {
        fact.fact_name for fact in case_six.resolution.facts
    }
    assert "image_observed_visible_damage" not in visible_names
    assert "image_observed_serial_number" not in visible_names
    requirement_two = case_six.package.requirement_checklist[1]
    assert requirement_two.status.value == "NOT_ESTABLISHED"
    assert (
        "LIMITED_VIEW_CANNOT_ESTABLISH_MATERIAL_ITEM_CONDITION"
        in requirement_two.evaluation_basis_codes
    )
    assert any(
        "condition assessment" in item
        for item in case_six.suggested_action.requested_items
    )

    case_seven = application.get_case_view("CB-2025-0007")
    delivery_source = next(
        source
        for source in case_seven.package.supporting_sources
        if source.fact_name == "carrier_reported_delivery_address"
    )
    assert delivery_source.page_number == 8
    requirement_three = case_seven.package.requirement_checklist[2]
    assert requirement_three.status.value == "SATISFIED"
    assert {
        reference.fact_name
        for reference in requirement_three.supporting_fact_refs
    } == {
        "carrier_reported_delivery_address",
        "case_recorded_shipping_postcode",
        "case_recorded_billing_postcode",
    }

    case_ten = application.get_case_view("CB-2025-0010")
    assert case_ten.package.requirement_checklist[0].status.value == (
        "NOT_ESTABLISHED"
    )
    assert case_ten.package.recommendation.decision.value == (
        "RECOMMEND_DO_NOT_REPRESENT"
    )

    def forbidden_runtime() -> ExtractionRuntime:
        raise AssertionError("cached reopening must not create a provider")

    cached = DisputeCopilotApplication(
        database_path=database_path,
        project_root=PROJECT_ROOT,
        rulebook_version="take-home-v4",
        execution_mode=ExecutionMode.CACHED_VIEW,
        runtime_factory=forbidden_runtime,
    )
    reopened = cached.get_case_view("CB-2025-0006")
    assert reopened.status is ExecutionStatus.COMPLETED
    assert reopened.execution_mode is ExecutionMode.CACHED_VIEW
    assert reopened.package == case_six.package
    assert reopened.suggested_action == case_six.suggested_action


def _populate(
    case_id: str,
    candidates: dict[str, ExtractionCandidate],
    documents: dict[str, EvidenceDocument],
) -> None:
    if case_id == "CB-2025-0001":
        order = documents["CB-2025-0001_carrier_tracking.pdf"]
        delivery = documents["CB-2025-0001_delivery_confirmation.pdf"]
        _put(candidates, "carrier_reported_tracking_number", "RM98421144GB", delivery, "Tracking number: RM98421144GB")
        _put(candidates, "shipping_record_carrier_name", "Royal Mail Tracked 24", order, "Royal Mail Tracked 24")
        _put(candidates, "carrier_reported_delivery_status", "DELIVERED", delivery, "DELIVERED")
        _put(candidates, "carrier_reported_delivery_address", "22 Cavendish Court, London SW4 7QR", delivery, "22 Cavendish Court")
        _put(candidates, "shipping_record_order_reference", "NT-44912", order, "Order #NT-44912")
        _put(candidates, "merchant_recorded_purchase_order_reference", "NT-44912", order, "Order #NT-44912")
        _put(candidates, "carrier_reported_delivery_date", "2025-03-24", delivery, "24 Mar 2025")
        return
    if case_id == "CB-2025-0002":
        screenshot = documents["CB-2025-0002_tracking_screenshot.png"]
        order = documents["CB-2025-0002_order_confirmation.pdf"]
        _put(candidates, "carrier_reported_tracking_number", "15441288821100", screenshot, "15441288821100")
        _put(candidates, "shipping_record_carrier_name", "DPDx", screenshot, "DPDx")
        _put(candidates, "carrier_reported_delivery_status", "DELIVERED", screenshot, "DELIVERED")
        _put(candidates, "carrier_reported_delivery_address", "M1 7DR", screenshot, "M1 7DR")
        _put(candidates, "carrier_reported_delivery_date", "2025-04-10", screenshot, "10 April 2025")
        _put(candidates, "merchant_recorded_purchase_order_reference", "LT-200931", order, "Order #LT-200931")
        return
    if case_id == "CB-2025-0003":
        return
    if case_id == "CB-2025-0004":
        report = documents["CB-2025-0004_internal_fraud_report.pdf"]
        _put(candidates, "merchant_recorded_first_transaction", True, report, "New (first transaction)")
        return
    if case_id == "CB-2025-0005":
        first = documents["CB-2025-0005_invoice_order_A.pdf"]
        second = documents["CB-2025-0005_invoice_order_B.pdf"]
        values = (
            ("merchant_recorded_first_purchase_id", "RL-ORDER-44188", first, "RL-ORDER-44188"),
            ("merchant_recorded_second_purchase_id", "RL-ORDER-44192", second, "RL-ORDER-44192"),
            ("merchant_stated_first_purchase_description", "Coffee products", first, "Ethiopia Yirgacheffe"),
            ("merchant_stated_second_purchase_description", "Aeropress starter kit", second, "Aeropress Go starter kit"),
            ("merchant_recorded_first_purchase_document_id", "RL-88291", first, "Invoice #RL-88291"),
            ("merchant_recorded_second_purchase_document_id", "RL-88294", second, "Invoice #RL-88294"),
            ("merchant_recorded_first_fulfilment_date", "2025-04-14", first, "Delivered: 14 April 2025"),
            ("merchant_recorded_second_fulfilment_date", "2025-04-15", second, "Delivered: 15 April 2025"),
            ("merchant_recorded_first_transaction_timestamp", "2025-04-12T08:11:33+01:00", first, "12 Apr 2025 08:11:33 BST"),
            ("merchant_recorded_second_transaction_timestamp", "2025-04-12T19:42:18+01:00", second, "12 Apr 2025 19:42:18 BST"),
            ("merchant_recorded_first_authorisation_code", "442189", first, "Auth code: 442189"),
            ("merchant_recorded_second_authorisation_code", "552781", second, "Auth code: 552781"),
        )
        for item in values:
            _put(candidates, *item)
        return
    if case_id == "CB-2025-0006":
        listing = documents["CB-2025-0006_product_listing.pdf"]
        photo = documents["CB-2025-0006_delivery_photo.png"]
        returns = documents["CB-2025-0006_returns_policy.pdf"]
        _put(candidates, "merchant_stated_product_description", "The Carlton armchair combines a hand-finished beech frame with deep-buttoned sage velvet upholstery.", listing, "Carlton armchair combines")
        _put(candidates, "merchant_stated_item_colour", "sage", listing, "Sage Velvet")
        _put(candidates, "merchant_stated_item_type", "armchair", listing, "Carlton Mid-Century Armchair")
        _put(candidates, "merchant_stated_delivery_image_scope", "front view only", photo, "front view only")
        candidates["image_observed_item_colour"] = _visual("image_observed_item_colour", "sage", photo, "The visible upholstery appears sage green.")
        candidates["image_observed_item_type"] = _visual("image_observed_item_type", "armchair", photo, "The front view appears to show an armchair.")
        _put(candidates, "merchant_stated_return_method", "contact returns@havenhome.example", returns, "contact")
        _put(candidates, "merchant_stated_return_window_days", 14, returns, "within 14 days of delivery")
        return
    if case_id == "CB-2025-0007":
        manifest = documents["CB-2025-0007_consolidated_manifest.pdf"]
        assert len(manifest.pages) == 10
        assert "txn_7745MN" in manifest.pages[7].text
        _put(candidates, "carrier_reported_tracking_number", "TF-9051", manifest, "TF-9051", page_number=8)
        _put(candidates, "shipping_record_carrier_name", "TerraFreight Logistics", manifest, "TerraFreight Logistics", page_number=1)
        _put(candidates, "carrier_reported_delivery_status", "DELIVERED", manifest, "DELIVERED 22 Apr 17:14", page_number=8)
        _put(candidates, "carrier_reported_delivery_date", "2025-04-22", manifest, "April 2025", page_number=8)
        _put(candidates, "carrier_reported_delivery_address", "LS9 8AA", manifest, "delivered to LS9 8AA", page_number=8)
        return
    if case_id == "CB-2025-0008":
        terms = documents["CB-2025-0008_subscription_terms.pdf"]
        _put(candidates, "merchant_stated_recurring_billing_terms", "Subscriptions auto-renew monthly until cancelled.", terms, "Subscriptions auto-renew")
        _put(candidates, "merchant_stated_cancellation_method", "account dashboard or email", terms, "account dashboard or by emailing")
        return
    if case_id == "CB-2025-0009":
        booking = documents["CB-2025-0009_booking_confirmation.pdf"]
        policy = documents["CB-2025-0009_cancellation_policy.pdf"]
        desk = documents["CB-2025-0009_front_desk_log.pdf"]
        values = (
            ("booking_record_reported_reservation_id", "MSP-2025-4488", booking, "MSP-2025-4488"),
            ("booking_record_reported_booking_date", "2025-03-12", booking, "12 March 2025"),
            ("booking_record_reported_service_date", "2025-04-26", booking, "Saturday 26 April 2025"),
            ("booking_record_reported_service_datetime", "2025-04-26T15:00:00+02:00", booking, "from 15:00"),
            ("merchant_stated_cancellation_policy", "Non-refundable booking; no refund for cancellation or no-show.", policy, "No refund will be issued"),
            ("merchant_stated_policy_disclosure_date", "2025-03-12", policy, "12 March 2025"),
            ("merchant_stated_policy_disclosure_channel", "tick-box consent", policy, "tick-box consent"),
            ("booking_record_reported_attendance_status", "NO-SHOW", desk, "NO-SHOW"),
            ("merchant_recorded_charged_fee_amount", "215.00", booking, "215.00 EUR"),
            ("merchant_recorded_charged_fee_currency", "EUR", booking, "215.00 EUR"),
            ("merchant_stated_policy_fee_amount", "215.00", booking, "215.00 EUR"),
            ("merchant_stated_policy_fee_currency", "EUR", booking, "215.00 EUR"),
        )
        for item in values:
            _put(candidates, *item)
        return
    if case_id == "CB-2025-0010":
        return
    raise AssertionError(case_id)


def _put(
    candidates: dict[str, ExtractionCandidate],
    fact_name: str,
    raw_value: object,
    document: EvidenceDocument,
    supporting_text: str,
    *,
    page_number: int = 1,
) -> None:
    candidates[fact_name] = _found(
        fact_name,
        raw_value,
        _text(document, supporting_text, page_number),
    )


def _text(
    document: EvidenceDocument,
    supporting_text: str,
    page_number: int,
) -> ExtractionEvidenceReference:
    page = document.pages[page_number - 1]
    assert supporting_text in page.text
    return TextExtractionEvidenceReference(
        evidence_type="TEXT",
        document_id=document.document_id,
        file_name=document.file_name,
        page_number=page_number,
        extraction_method=page.extraction_method.value,
        supporting_text=supporting_text,
    )


def _visual(
    fact_name: str,
    raw_value: object,
    document: EvidenceDocument,
    observation: str,
) -> ExtractionCandidate:
    return _found(
        fact_name,
        raw_value,
        VisualExtractionEvidenceReference(
            evidence_type="VISUAL",
            document_id=document.document_id,
            file_name=document.file_name,
            page_number=1,
            extraction_method="VISION",
            visual_observation=observation,
        ),
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
