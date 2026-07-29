"""Human-in-the-loop evidence display and decision acceptance tests."""

from __future__ import annotations

import json
from math import nan
from pathlib import Path
import shutil
import sqlite3
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter, ValidationError
from PIL import Image

from dispute_copilot.analyst_review.repository import (
    SQLiteAnalystReviewPackageRepository,
)
from dispute_copilot.human_review.app import create_app
from dispute_copilot.human_review.app import _close_callback
from dispute_copilot.human_review.controller import (
    DisplaySelection,
    HumanReviewController,
)
from dispute_copilot.human_review.decision_models import (
    AnalystAction,
    AnalystDecisionCommand,
    AnalystOutcome,
    ConfirmDecisionCommand,
    OverrideDecisionCommand,
    RequestEvidenceDecisionCommand,
    TrustedAnalystIdentity,
)
from dispute_copilot.human_review.decision_repository import (
    SQLiteAnalystDecisionRepository,
)
from dispute_copilot.human_review.decision_rules import (
    DEFAULT_DECISION_RULES_KEY,
    DEFAULT_DECISION_RULES_VERSION,
    load_decision_rules,
)
from dispute_copilot.human_review.decision_service import (
    preview_analyst_decision,
    record_analyst_decision,
)
from dispute_copilot.human_review.display_adapter import (
    EvidencePresentationAdapter,
    VISUAL_OBSERVATION_CAVEAT,
)
from dispute_copilot.human_review.display_models import (
    EvidenceDisplayLineage,
    EvidenceType,
    BoundingBox,
    CoordinateSpace,
    HighlightStatus,
    PageGeometry,
    RegionPrecision,
    RegionSource,
    create_evidence_display_target,
)
from dispute_copilot.human_review.errors import (
    DecisionPersistenceConflictError,
    DecisionValidationError,
    SourceIntegrityError,
    StaleReviewPackageError,
    UnlinkedEvidenceError,
)
from dispute_copilot.human_review.renderer import EvidenceRenderer
from dispute_copilot.intake.hashing import calculate_sha256

PROJECT_ROOT = Path(__file__).parents[1]
SOURCE_DATABASE = PROJECT_ROOT / "data" / "dispute_copilot.db"
SOURCE_EVIDENCE = PROJECT_ROOT / "data" / "evidence"
PACKAGE_ID = UUID("ff9d317b-ca4e-4047-b081-4dbd3ea204f5")
SELECTION_ID = UUID("1cb6b917-62c5-41b1-b9ca-0c0659e5299a")
EXPECTED_PACKAGE_HASH = (
    "9ab60106d732ca7b370cb218d6d39c0a2cc8b2962930d86a74e783cb441f472d"
)
EVIDENCE_FILES = (
    "CB-2025-0006_product_listing.pdf",
    "CB-2025-0006_delivery_photo.png",
    "CB-2025-0006_returns_policy.pdf",
)


@pytest.fixture
def review_environment(tmp_path: Path) -> tuple[Path, Path]:
    database_path = tmp_path / "review.db"
    shutil.copy2(SOURCE_DATABASE, database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE IF EXISTS analyst_decisions")
        connection.execute(
            "DELETE FROM workflow_events WHERE source_type = 'ANALYST_DECISION'"
        )
    project_root = tmp_path / "project"
    evidence_directory = project_root / "data" / "evidence"
    evidence_directory.mkdir(parents=True)
    for file_name in EVIDENCE_FILES:
        shutil.copy2(
            SOURCE_EVIDENCE / file_name,
            evidence_directory / file_name,
        )
    return database_path, project_root


@pytest.fixture
def package(review_environment):
    database_path, _ = review_environment
    result = SQLiteAnalystReviewPackageRepository(database_path).get(
        PACKAGE_ID
    )
    assert result is not None
    assert result.content_hash == EXPECTED_PACKAGE_HASH
    return result


@pytest.fixture
def controller(review_environment) -> HumanReviewController:
    database_path, project_root = review_environment
    return HumanReviewController(
        review_package_id=PACKAGE_ID,
        analyst_identity=TrustedAnalystIdentity(subject="analyst-007"),
        database_path=database_path,
        project_root=project_root,
    )


def _confirm_command(package, *, key: str = "test:confirm:0001"):
    return ConfirmDecisionCommand(
        action=AnalystAction.CONFIRM,
        review_package_id=package.review_package_id,
        expected_review_package_hash=package.content_hash,
        idempotency_key=key,
    )


def _request_command(
    package,
    gap_ids: tuple[str, ...],
    *,
    key: str = "test:request:0001",
):
    return RequestEvidenceDecisionCommand(
        action=AnalystAction.REQUEST_MORE_EVIDENCE,
        review_package_id=package.review_package_id,
        expected_review_package_hash=package.content_hash,
        selected_gap_ids=gap_ids,
        idempotency_key=key,
    )


def test_all_six_sources_have_honest_region_capability(
    review_environment,
    package,
) -> None:
    database_path, project_root = review_environment
    adapter = EvidencePresentationAdapter(
        database_path=database_path,
        project_root=project_root,
    )
    assert len(package.supporting_sources) == 6

    for source in package.supporting_sources:
        resolved = adapter.for_source(
            review_package_id=PACKAGE_ID,
            source_ref_id=source.source_ref_id,
        )
        target = resolved.target
        assert resolved.source_path is not None
        assert not target.bounding_boxes
        if source.support_type.value == "VISUAL_MODEL_OBSERVATION":
            assert target.evidence_type is (
                EvidenceType.VISUAL_MODEL_OBSERVATION
            )
            assert target.highlight_status is (
                HighlightStatus.WHOLE_IMAGE_CONTEXT
            )
            assert target.analyst_message == VISUAL_OBSERVATION_CAVEAT
        else:
            assert target.evidence_type is (
                EvidenceType.NATIVE_TEXT_ASSERTION
            )
            assert target.highlight_status is HighlightStatus.PAGE_ONLY


def test_visual_photo_renders_without_fabricated_highlight(
    controller,
    package,
) -> None:
    source = next(
        item
        for item in package.supporting_sources
        if item.fact_name == "image_observed_item_colour"
    )
    panel = controller.inspect(
        DisplaySelection(kind="source", item_id=source.source_ref_id)
    )

    assert panel.image is not None
    assert panel.image.size == (800, 600)
    assert "WHOLE_IMAGE_CONTEXT" in panel.metadata_markdown
    assert "VISUAL_MODEL_OBSERVATION" in panel.metadata_markdown
    assert VISUAL_OBSERVATION_CAVEAT in panel.metadata_markdown


def test_gap_has_no_location_and_lists_only_declared_sources(
    controller,
    package,
) -> None:
    gap = package.evidence_gaps[0]
    panel = controller.inspect(
        DisplaySelection(kind="gap", item_id=gap.gap_id)
    )

    assert panel.image is None
    assert "NOT_APPLICABLE" in panel.metadata_markdown
    assert "does not prove" in panel.metadata_markdown
    for file_name in EVIDENCE_FILES:
        assert file_name in panel.metadata_markdown


def test_pdf_opens_exact_linked_page_and_unlinked_id_fails_closed(
    controller,
    package,
) -> None:
    source = next(
        item
        for item in package.supporting_sources
        if item.source_document == EVIDENCE_FILES[0]
    )
    panel = controller.inspect(
        DisplaySelection(kind="source", item_id=source.source_ref_id)
    )
    assert panel.image is not None
    assert "**Page:** 1 of 1" in panel.metadata_markdown
    assert "PAGE_ONLY" in panel.metadata_markdown

    with pytest.raises(UnlinkedEvidenceError, match="not linked"):
        controller.inspect(
            DisplaySelection(kind="source", item_id="source-not-linked")
        )


def test_changed_evidence_bytes_block_display(
    review_environment,
    package,
) -> None:
    database_path, project_root = review_environment
    photo = project_root / "data" / "evidence" / EVIDENCE_FILES[1]
    photo.write_bytes(photo.read_bytes() + b"changed")
    source = next(
        item
        for item in package.supporting_sources
        if item.source_document == EVIDENCE_FILES[1]
    )

    with pytest.raises(SourceIntegrityError, match="size has changed"):
        EvidencePresentationAdapter(
            database_path=database_path,
            project_root=project_root,
        ).for_source(
            review_package_id=PACKAGE_ID,
            source_ref_id=source.source_ref_id,
        )


def test_visual_observation_without_coordinates_cannot_claim_exact_region(
) -> None:
    lineage = EvidenceDisplayLineage(
        case_run_id=uuid4(),
        policy_selection_id=uuid4(),
        fact_resolution_id=uuid4(),
        evaluation_id=uuid4(),
        recommendation_id=uuid4(),
        review_package_id=uuid4(),
    )
    with pytest.raises(ValidationError, match="bounding box"):
        create_evidence_display_target(
            review_package_id=lineage.review_package_id,
            case_id="case",
            requirement_id="R1",
            fact_id="fact",
            gap_id=None,
            source_document_id=uuid4(),
            source_file_name="photo.png",
            source_sha256="a" * 64,
            page_number=1,
            page_count=1,
            label="visual fact",
            evidence_type=EvidenceType.VISUAL_MODEL_OBSERVATION,
            extracted_or_admitted_value=None,
            supporting_excerpt="an observation",
            highlight_status=HighlightStatus.EXACT_REGION_AVAILABLE,
            coordinate_space=None,
            page_geometry=PageGeometry(width=800.0, height=600.0),
            bounding_boxes=(),
            inspected_source_summaries=(),
            analyst_message="No region",
            lineage=lineage,
        )


def test_validated_region_is_transformed_and_drawn_in_render_space(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (100, 100), (255, 255, 255)).save(source)
    lineage = EvidenceDisplayLineage(
        case_run_id=uuid4(),
        policy_selection_id=uuid4(),
        fact_resolution_id=uuid4(),
        evaluation_id=uuid4(),
        recommendation_id=uuid4(),
        review_package_id=uuid4(),
    )
    target = create_evidence_display_target(
        review_package_id=lineage.review_package_id,
        case_id="case",
        requirement_id="R1",
        fact_id="fact",
        gap_id=None,
        source_document_id=uuid4(),
        source_file_name=source.name,
        source_sha256=calculate_sha256(source),
        page_number=1,
        page_count=1,
        label="grounded visual fact",
        evidence_type=EvidenceType.VISUAL_MODEL_OBSERVATION,
        extracted_or_admitted_value=None,
        supporting_excerpt="grounded observation",
        highlight_status=HighlightStatus.EXACT_REGION_AVAILABLE,
        coordinate_space=CoordinateSpace.NORMALIZED_0_TO_1,
        page_geometry=PageGeometry(width=100.0, height=100.0),
        bounding_boxes=(
            BoundingBox(
                x_min=0.2,
                y_min=0.2,
                x_max=0.6,
                y_max=0.6,
                region_source=RegionSource.CAPTURED_DURING_EXTRACTION,
                region_precision=RegionPrecision.OBJECT,
            ),
        ),
        inspected_source_summaries=(),
        analyst_message="Exact persisted region.",
        lineage=lineage,
    )

    rendered = EvidenceRenderer().render(
        target=target,
        source_path=source,
    )
    assert rendered.highlight_drawn is True
    assert rendered.image.getpixel((20, 20)) == (255, 230, 0)
    assert rendered.image.getpixel((10, 10)) == (255, 255, 255)


def test_invalid_coordinates_and_page_rotation_fail_validation() -> None:
    with pytest.raises(ValidationError, match="finite"):
        BoundingBox(
            x_min=nan,
            y_min=0.0,
            x_max=1.0,
            y_max=1.0,
            region_source=RegionSource.CAPTURED_DURING_EXTRACTION,
            region_precision=RegionPrecision.OBJECT,
        )
    with pytest.raises(ValidationError, match="greater"):
        BoundingBox(
            x_min=1.0,
            y_min=0.0,
            x_max=0.0,
            y_max=1.0,
            region_source=RegionSource.CAPTURED_DURING_EXTRACTION,
            region_precision=RegionPrecision.OBJECT,
        )
    with pytest.raises(ValidationError, match="rotation"):
        PageGeometry(
            width=100.0,
            height=100.0,
            rotation_degrees=45,
        )


def test_decision_rules_are_versioned_hashed_and_complete() -> None:
    rules = load_decision_rules(
        decision_rules_key=DEFAULT_DECISION_RULES_KEY,
        decision_rules_version=DEFAULT_DECISION_RULES_VERSION,
    )
    assert len(rules.integrity_hash) == 64
    assert {
        (rule.action.value, rule.outcome.value)
        for rule in rules.workflow_transitions
    } == {
        ("CONFIRM", "REPRESENT"),
        ("CONFIRM", "ACCEPT_LIABILITY"),
        ("CONFIRM", "REQUEST_MORE_EVIDENCE"),
        ("OVERRIDE", "REPRESENT"),
        ("OVERRIDE", "ACCEPT_LIABILITY"),
        ("OVERRIDE", "REQUEST_MORE_EVIDENCE"),
        ("REQUEST_MORE_EVIDENCE", "REQUEST_MORE_EVIDENCE"),
    }


def test_tampered_decision_rules_fail_closed(tmp_path: Path) -> None:
    source = (
        PROJECT_ROOT
        / "data"
        / "human-review-rules"
        / DEFAULT_DECISION_RULES_KEY
        / DEFAULT_DECISION_RULES_VERSION
        / "manifest.json"
    )
    payload = json.loads(source.read_text())
    payload["override_reasons"][0]["display_label"] = "Tampered"
    target = (
        tmp_path
        / DEFAULT_DECISION_RULES_KEY
        / DEFAULT_DECISION_RULES_VERSION
        / "manifest.json"
    )
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(payload))

    with pytest.raises(
        DecisionValidationError,
        match="failed validation",
    ):
        load_decision_rules(
            decision_rules_key=DEFAULT_DECISION_RULES_KEY,
            decision_rules_version=DEFAULT_DECISION_RULES_VERSION,
            decision_rules_root=tmp_path,
        )


def test_confirm_preview_derives_outcome_without_persistence(
    review_environment,
    package,
) -> None:
    database_path, _ = review_environment
    preview = preview_analyst_decision(
        command=_confirm_command(package),
        database_path=database_path,
    )

    assert preview.selected_outcome is (
        AnalystOutcome.REQUEST_MORE_EVIDENCE
    )
    assert preview.next_workflow_status == "MORE_EVIDENCE_REQUESTED"
    with sqlite3.connect(database_path) as connection:
        table = connection.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type = 'table' AND name = 'analyst_decisions'
            """
        ).fetchone()[0]
    assert table == 0


def test_request_more_evidence_persists_decision_and_event_atomically(
    review_environment,
    package,
) -> None:
    database_path, _ = review_environment
    selected_gaps = tuple(gap.gap_id for gap in package.evidence_gaps)
    upstream_before = _upstream_hashes(database_path)
    decision = record_analyst_decision(
        command=_request_command(package, selected_gaps),
        analyst_identity=TrustedAnalystIdentity(subject="analyst-007"),
        database_path=database_path,
    )

    assert decision.selected_outcome is (
        AnalystOutcome.REQUEST_MORE_EVIDENCE
    )
    assert decision.selected_gap_ids == selected_gaps
    assert decision.next_workflow_status.value == "MORE_EVIDENCE_REQUESTED"
    assert (
        SQLiteAnalystDecisionRepository(database_path)
        .get(decision.analyst_decision_id)
        == decision
    )
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM analyst_decisions"
        ).fetchone()[0] == 1
        event = connection.execute(
            """
            SELECT from_status, to_status
            FROM workflow_events
            WHERE source_type = 'ANALYST_DECISION'
            """
        ).fetchone()
    assert event == (
        "READY_FOR_ANALYST_REVIEW",
        "MORE_EVIDENCE_REQUESTED",
    )
    assert _upstream_hashes(database_path) == upstream_before


def test_identical_idempotent_replay_returns_existing_decision(
    review_environment,
    package,
) -> None:
    database_path, _ = review_environment
    command = _confirm_command(package)
    identity = TrustedAnalystIdentity(subject="analyst-007")

    first = record_analyst_decision(
        command=command,
        analyst_identity=identity,
        database_path=database_path,
    )
    second = record_analyst_decision(
        command=command,
        analyst_identity=identity,
        database_path=database_path,
    )

    assert second == first
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM analyst_decisions"
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*) FROM workflow_events
            WHERE source_type = 'ANALYST_DECISION'
            """
        ).fetchone()[0] == 1


def test_conflicting_replay_and_second_decision_fail_closed(
    review_environment,
    package,
) -> None:
    database_path, _ = review_environment
    gaps = tuple(gap.gap_id for gap in package.evidence_gaps)
    identity = TrustedAnalystIdentity(subject="analyst-007")
    record_analyst_decision(
        command=_request_command(package, gaps[:1]),
        analyst_identity=identity,
        database_path=database_path,
    )

    with pytest.raises(
        DecisionPersistenceConflictError,
        match="idempotency key",
    ):
        record_analyst_decision(
            command=_request_command(package, gaps[1:2]),
            analyst_identity=identity,
            database_path=database_path,
        )
    with pytest.raises(
        DecisionPersistenceConflictError,
        match="final analyst decision",
    ):
        record_analyst_decision(
            command=_request_command(
                package,
                gaps[1:2],
                key="test:request:0002",
            ),
            analyst_identity=identity,
            database_path=database_path,
        )


def test_override_stale_hash_and_unknown_gap_are_rejected(
    review_environment,
    package,
) -> None:
    database_path, _ = review_environment
    identity = TrustedAnalystIdentity(subject="analyst-007")
    same_as_system = OverrideDecisionCommand(
        action=AnalystAction.OVERRIDE,
        review_package_id=PACKAGE_ID,
        expected_review_package_hash=package.content_hash,
        selected_outcome=AnalystOutcome.REQUEST_MORE_EVIDENCE,
        override_reason_code="RISK_JUDGEMENT",
        analyst_comment="This is a sufficiently long explanation.",
        idempotency_key="test:override:0001",
    )
    with pytest.raises(DecisionValidationError, match="must differ"):
        record_analyst_decision(
            command=same_as_system,
            analyst_identity=identity,
            database_path=database_path,
        )

    stale = _confirm_command(package).model_copy(
        update={"expected_review_package_hash": "0" * 64}
    )
    with pytest.raises(StaleReviewPackageError, match="stale"):
        record_analyst_decision(
            command=stale,
            analyst_identity=identity,
            database_path=database_path,
        )

    with pytest.raises(DecisionValidationError, match="not unresolved"):
        record_analyst_decision(
            command=_request_command(package, ("gap-not-linked",)),
            analyst_identity=identity,
            database_path=database_path,
        )


def test_valid_override_is_immutable_and_uses_allowed_taxonomy(
    review_environment,
    package,
) -> None:
    database_path, _ = review_environment
    command = OverrideDecisionCommand(
        action=AnalystAction.OVERRIDE,
        review_package_id=PACKAGE_ID,
        expected_review_package_hash=package.content_hash,
        selected_outcome=AnalystOutcome.REPRESENT,
        override_reason_code="EVIDENCE_INTERPRETATION",
        analyst_comment="  The admitted evidence supports representment.  ",
        idempotency_key="test:override:0002",
    )
    decision = record_analyst_decision(
        command=command,
        analyst_identity=TrustedAnalystIdentity(subject="analyst-007"),
        database_path=database_path,
    )

    assert decision.action is AnalystAction.OVERRIDE
    assert decision.selected_outcome is AnalystOutcome.REPRESENT
    assert decision.reason_code == "EVIDENCE_INTERPRETATION"
    assert decision.analyst_comment == (
        "The admitted evidence supports representment."
    )
    assert decision.next_workflow_status.value == (
        "ANALYST_OVERRIDDEN_TO_REPRESENT"
    )
    with pytest.raises(ValidationError):
        decision.selected_outcome = AnalystOutcome.ACCEPT_LIABILITY


def test_close_without_deciding_performs_no_write(
    review_environment,
) -> None:
    database_path, _ = review_environment
    response = _close_callback()

    assert "No AnalystDecision" in response[0]
    assert (
        SQLiteAnalystDecisionRepository(database_path)
        .get_for_review_package(PACKAGE_ID)
        is None
    )
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM workflow_events
            WHERE source_type = 'ANALYST_DECISION'
            """
        ).fetchone()[0] == 0


def test_identity_is_not_accepted_from_browser_command(package) -> None:
    payload = {
        **_confirm_command(package).model_dump(mode="json"),
        "decided_by": "browser-supplied",
    }
    with pytest.raises(ValidationError, match="decided_by"):
        TypeAdapter(AnalystDecisionCommand).validate_json(
            json.dumps(payload)
        )


def test_forced_event_failure_rolls_back_decision(
    review_environment,
    package,
) -> None:
    database_path, _ = review_environment
    repository = SQLiteAnalystDecisionRepository(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_analyst_event
            BEFORE INSERT ON workflow_events
            WHEN NEW.source_type = 'ANALYST_DECISION'
            BEGIN
                SELECT RAISE(ABORT, 'forced event failure');
            END
            """
        )
    command = _confirm_command(package)
    preview = preview_analyst_decision(
        command=command,
        database_path=database_path,
    )
    assert preview.next_workflow_status == "MORE_EVIDENCE_REQUESTED"

    with pytest.raises(DecisionPersistenceConflictError):
        record_analyst_decision(
            command=command,
            analyst_identity=TrustedAnalystIdentity(
                subject="analyst-007"
            ),
            database_path=database_path,
        )
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM analyst_decisions"
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*) FROM workflow_events
            WHERE source_type = 'ANALYST_DECISION'
            """
        ).fetchone()[0] == 0
    assert repository.effective_status(SELECTION_ID) == (
        "READY_FOR_ANALYST_REVIEW"
    )


def test_gradio_app_has_no_preselected_action_and_builds_offline(
    review_environment,
) -> None:
    database_path, project_root = review_environment
    app = create_app(
        review_package_id=PACKAGE_ID,
        database_path=database_path,
        project_root=project_root,
        analyst_identity=TrustedAnalystIdentity(subject="analyst-007"),
    )
    config = app.get_config_file()
    action_components = [
        component
        for component in config["components"]
        if component["props"].get("label") == "Action"
    ]
    assert len(action_components) == 1
    assert action_components[0]["props"].get("value") is None
    assert any(
        component["props"].get("value") == "Submit final decision"
        and component["props"]["interactive"] is False
        for component in config["components"]
    )


def _upstream_hashes(database_path: Path) -> tuple[str, ...]:
    with sqlite3.connect(database_path) as connection:
        package_hash = connection.execute(
            """
            SELECT content_hash FROM analyst_review_packages
            WHERE review_package_id = ?
            """,
            (str(PACKAGE_ID),),
        ).fetchone()[0]
        recommendation_hash = connection.execute(
            """
            SELECT content_hash FROM recommendation_results
            WHERE selection_id = ?
            """,
            (str(SELECTION_ID),),
        ).fetchone()[0]
        evaluation_hash = connection.execute(
            """
            SELECT content_hash FROM requirement_evaluations
            WHERE selection_id = ?
            """,
            (str(SELECTION_ID),),
        ).fetchone()[0]
        resolution_hash = connection.execute(
            """
            SELECT content_hash FROM fact_resolution_results
            WHERE selection_id = ?
            """,
            (str(SELECTION_ID),),
        ).fetchone()[0]
    return (
        package_hash,
        recommendation_hash,
        evaluation_hash,
        resolution_hash,
    )
