"""Offline acceptance and integrity tests for analyst-review assembly."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from dispute_copilot.analyst_review.assembler import (
    assemble_analyst_review_package,
)
from dispute_copilot.analyst_review.errors import (
    AnalystReviewInputError,
    PresentationRulesError,
)
from dispute_copilot.analyst_review.guards import (
    validate_analyst_review_inputs,
)
from dispute_copilot.analyst_review.models import (
    AnalystReviewPackage,
    SupportType,
)
from dispute_copilot.analyst_review.presentation_models import (
    PresentationRulesManifest,
)
from dispute_copilot.analyst_review.registry import (
    load_presentation_rules,
)
from dispute_copilot.analyst_review.repository import (
    SQLiteAnalystReviewPackageRepository,
)
from dispute_copilot.analyst_review.review import main as review_main
from dispute_copilot.analyst_review.service import (
    create_analyst_review_package,
)
from dispute_copilot.evaluation.repository import (
    SQLiteEvaluationRepository,
)
from dispute_copilot.extraction.fact_resolution_repository import (
    SQLiteFactResolutionRepository,
)
from dispute_copilot.policy.repository import (
    SQLitePolicySelectionRepository,
)
from dispute_copilot.recommendation.mapping_models import Recommendation
from dispute_copilot.recommendation.repository import (
    SQLiteRecommendationRepository,
)
from dispute_copilot.repositories.sqlite import SQLiteCaseRunRepository

PROJECT_ROOT = Path(__file__).parents[1]
SOURCE_DATABASE = PROJECT_ROOT / "data" / "dispute_copilot.db"
PRESENTATION_ROOT = PROJECT_ROOT / "data" / "presentation-rules"
PRESENTATION_KEY = "analyst-review-package-v1"
PRESENTATION_VERSION = "1.0.0"
RUN_ID = UUID("cf8687c3-a58d-4fd4-9c07-9d4c7df5b3e9")
SELECTION_ID = UUID("1cb6b917-62c5-41b1-b9ca-0c0659e5299a")
RESOLUTION_ID = UUID("6a58ecc4-2782-4136-a7af-e21571ffdb50")
EVALUATION_ID = UUID("bed922b8-2aa6-48c8-bb09-7e174465683d")
RECOMMENDATION_ID = UUID("c7ade87f-6845-4be8-be26-ce914b7e14dc")


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    destination = tmp_path / "review.db"
    shutil.copy2(SOURCE_DATABASE, destination)
    with sqlite3.connect(destination) as connection:
        connection.execute(
            "DELETE FROM analyst_review_packages"
        )
        # The checked-in/demo database can legitimately advance after a
        # human review. This fixture needs the pre-decision package-assembly
        # state, so isolate only the temporary copy without rewriting the
        # append-only decision in the real database.
        connection.execute("DELETE FROM analyst_decisions")
        connection.execute(
            """
            DELETE FROM workflow_events
            WHERE source_type = 'ANALYST_DECISION'
            """
        )
    return destination


def _load_guarded_inputs(database_path: Path):
    recommendation_repository = SQLiteRecommendationRepository(
        database_path
    )
    recommendation = recommendation_repository.get(RECOMMENDATION_ID)
    evaluation = SQLiteEvaluationRepository(database_path).get(EVALUATION_ID)
    resolution = SQLiteFactResolutionRepository(database_path).get(
        RESOLUTION_ID
    )
    selection = SQLitePolicySelectionRepository(database_path).get(
        SELECTION_ID
    )
    case_run = SQLiteCaseRunRepository(database_path).get(RUN_ID)
    assert recommendation is not None
    assert evaluation is not None
    assert resolution is not None
    assert selection is not None
    assert case_run is not None
    return validate_analyst_review_inputs(
        recommendation=recommendation,
        evaluation=evaluation,
        resolution=resolution,
        selection=selection,
        case_run=case_run,
        workflow_status=recommendation_repository.effective_status(
            SELECTION_ID
        ),
    )


def test_real_cb_2025_0006_package_is_complete_and_offline(
    database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with sqlite3.connect(database_path) as connection:
        event_count_before = connection.execute(
            "SELECT COUNT(*) FROM workflow_events"
        ).fetchone()[0]

    original_read_bytes = Path.read_bytes
    file_reads: list[Path] = []

    def allow_only_presentation_manifest(path: Path) -> bytes:
        file_reads.append(path)
        assert path.name == "manifest.json"
        assert "presentation-rules" in path.parts
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", allow_only_presentation_manifest)

    package = create_analyst_review_package(
        recommendation_id=RECOMMENDATION_ID,
        presentation_rules_key=PRESENTATION_KEY,
        presentation_rules_version=PRESENTATION_VERSION,
        database_path=database_path,
        presentation_rules_root=PRESENTATION_ROOT,
    )

    assert package.case_summary.case_id == "CB-2025-0006"
    assert package.case_summary.current_workflow_status == (
        "READY_FOR_ANALYST_REVIEW"
    )
    assert package.recommendation.decision is (
        Recommendation.INSUFFICIENT_EVIDENCE_TO_REPRESENT
    )
    assert package.recommendation.advisory_only is True
    assert package.recommendation.causal_requirement_ids == (
        "V13.3-R2",
        "V13.3-R3",
    )
    assert tuple(
        (item.requirement_id, item.status.value)
        for item in package.requirement_checklist
    ) == (
        ("V13.3-R1", "SATISFIED"),
        ("V13.3-R2", "NOT_ESTABLISHED"),
        ("V13.3-R3", "NOT_ESTABLISHED"),
    )
    assert tuple(
        (gap.requirement_id, gap.fact_name, gap.resolution_status)
        for gap in package.evidence_gaps
    ) == (
        (
            "V13.3-R2",
            "image_observed_visible_damage",
            "NOT_FOUND",
        ),
        (
            "V13.3-R2",
            "image_observed_serial_number",
            "NOT_FOUND",
        ),
        (
            "V13.3-R3",
            "merchant_recorded_return_request_date",
            "NOT_FOUND",
        ),
        (
            "V13.3-R3",
            "merchant_recorded_post_complaint_usage_date",
            "NOT_FOUND",
        ),
        (
            "V13.3-R3",
            "merchant_recorded_merchandise_retention_status",
            "NOT_FOUND",
        ),
    )
    assert len(package.rationale.sentences) == 3
    assert package.rationale.sentences[1].startswith(
        "However, requirements V13.3-R2 and V13.3-R3"
    )
    assert {
        (source.fact_name, source.source_document, source.page_number)
        for source in package.supporting_sources
    } >= {
        (
            "merchant_stated_product_description",
            "CB-2025-0006_product_listing.pdf",
            1,
        ),
        (
            "image_observed_item_colour",
            "CB-2025-0006_delivery_photo.png",
            1,
        ),
        (
            "image_observed_item_type",
            "CB-2025-0006_delivery_photo.png",
            1,
        ),
        (
            "merchant_stated_return_method",
            "CB-2025-0006_returns_policy.pdf",
            1,
        ),
        (
            "merchant_stated_return_window_days",
            "CB-2025-0006_returns_policy.pdf",
            1,
        ),
    }
    visual_sources = tuple(
        source
        for source in package.supporting_sources
        if source.fact_name
        in {"image_observed_item_colour", "image_observed_item_type"}
    )
    assert visual_sources
    assert all(
        source.support_type is SupportType.VISUAL_MODEL_OBSERVATION
        and source.extraction_method == "VISION"
        for source in visual_sources
    )
    assert file_reads == [
        PRESENTATION_ROOT
        / PRESENTATION_KEY
        / PRESENTATION_VERSION
        / "manifest.json"
    ]

    with sqlite3.connect(database_path) as connection:
        package_count = connection.execute(
            "SELECT COUNT(*) FROM analyst_review_packages"
        ).fetchone()[0]
        event_count_after = connection.execute(
            "SELECT COUNT(*) FROM workflow_events"
        ).fetchone()[0]
        review_event_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM workflow_events
            WHERE source_type = 'ANALYST_REVIEW_PACKAGE'
            """
        ).fetchone()[0]
    assert package_count == 1
    assert event_count_after == event_count_before
    assert review_event_count == 0


def test_identical_replay_returns_same_persisted_package(
    database_path: Path,
) -> None:
    first = create_analyst_review_package(
        selection_id=SELECTION_ID,
        presentation_rules_key=PRESENTATION_KEY,
        presentation_rules_version=PRESENTATION_VERSION,
        database_path=database_path,
        presentation_rules_root=PRESENTATION_ROOT,
    )
    second = create_analyst_review_package(
        recommendation_id=RECOMMENDATION_ID,
        presentation_rules_key=PRESENTATION_KEY,
        presentation_rules_version=PRESENTATION_VERSION,
        database_path=database_path,
        presentation_rules_root=PRESENTATION_ROOT,
    )

    assert second == first
    assert SQLiteAnalystReviewPackageRepository(database_path).get(
        first.review_package_id
    ) == first
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM analyst_review_packages"
        ).fetchone()[0] == 1


def test_semantic_package_hash_ignores_generated_identity_and_time(
    database_path: Path,
) -> None:
    inputs = _load_guarded_inputs(database_path)
    rules = load_presentation_rules(
        presentation_rules_key=PRESENTATION_KEY,
        presentation_rules_version=PRESENTATION_VERSION,
        presentation_rules_root=PRESENTATION_ROOT,
    )
    first = assemble_analyst_review_package(
        inputs=inputs,
        presentation_rules=rules,
        review_package_id=uuid4(),
        created_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    second = assemble_analyst_review_package(
        inputs=inputs,
        presentation_rules=rules,
        review_package_id=uuid4(),
        created_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )

    assert first.review_package_id != second.review_package_id
    assert first.created_at != second.created_at
    assert first.content_hash == second.content_hash
    assert AnalystReviewPackage.model_validate_json(
        first.model_dump_json()
    ) == first
    with pytest.raises(ValidationError, match="content_hash"):
        AnalystReviewPackage.model_validate(
            {
                **first.model_dump(),
                "content_hash": "0" * 64,
            }
        )


def test_lineage_guard_rejects_tampering_and_wrong_workflow(
    database_path: Path,
) -> None:
    inputs = _load_guarded_inputs(database_path)
    recommendation = inputs.recommendation
    evaluation = inputs.evaluation
    resolution = inputs.resolution
    selection = inputs.selection
    case_run = inputs.case_run

    with pytest.raises(AnalystReviewInputError, match="workflow"):
        validate_analyst_review_inputs(
            recommendation=recommendation,
            evaluation=evaluation,
            resolution=resolution,
            selection=selection,
            case_run=case_run,
            workflow_status="READY_FOR_RECOMMENDATION",
        )
    with pytest.raises(AnalystReviewInputError, match="content hash"):
        validate_analyst_review_inputs(
            recommendation=recommendation.model_copy(
                update={"content_hash": "0" * 64}
            ),
            evaluation=evaluation,
            resolution=resolution,
            selection=selection,
            case_run=case_run,
            workflow_status="READY_FOR_ANALYST_REVIEW",
        )
    with pytest.raises(AnalystReviewInputError, match="lineage"):
        validate_analyst_review_inputs(
            recommendation=recommendation,
            evaluation=evaluation,
            resolution=resolution,
            selection=selection.model_copy(update={"run_id": uuid4()}),
            case_run=case_run,
            workflow_status="READY_FOR_ANALYST_REVIEW",
        )
    with pytest.raises(AnalystReviewInputError, match="Policy identity"):
        validate_analyst_review_inputs(
            recommendation=recommendation,
            evaluation=evaluation,
            resolution=resolution,
            selection=selection.model_copy(
                update={"policy_sha256": "0" * 64}
            ),
            case_run=case_run,
            workflow_status="READY_FOR_ANALYST_REVIEW",
        )


def test_presentation_manifest_is_complete_hashed_and_fail_closed(
    tmp_path: Path,
) -> None:
    manifest = load_presentation_rules(
        presentation_rules_key=PRESENTATION_KEY,
        presentation_rules_version=PRESENTATION_VERSION,
        presentation_rules_root=PRESENTATION_ROOT,
    )
    assert len(manifest.integrity_hash) == 64
    assert {
        template.recommendation
        for template in manifest.rationale_templates
    } == set(Recommendation)
    assert all(
        len(template.sentences) <= 3
        for template in manifest.rationale_templates
    )

    source = (
        PRESENTATION_ROOT
        / PRESENTATION_KEY
        / PRESENTATION_VERSION
        / "manifest.json"
    )
    payload = json.loads(source.read_text())
    payload["limitations"][0] = "Tampered limitation"
    destination = (
        tmp_path
        / PRESENTATION_KEY
        / PRESENTATION_VERSION
        / "manifest.json"
    )
    destination.parent.mkdir(parents=True)
    destination.write_text(json.dumps(payload))
    with pytest.raises(
        PresentationRulesError,
        match="integrity_hash|failed validation",
    ):
        load_presentation_rules(
            presentation_rules_key=PRESENTATION_KEY,
            presentation_rules_version=PRESENTATION_VERSION,
            presentation_rules_root=tmp_path,
        )


def test_missing_gap_label_produces_no_partial_package(
    database_path: Path,
) -> None:
    inputs = _load_guarded_inputs(database_path)
    rules = load_presentation_rules(
        presentation_rules_key=PRESENTATION_KEY,
        presentation_rules_version=PRESENTATION_VERSION,
        presentation_rules_root=PRESENTATION_ROOT,
    )
    invalid_rules = rules.model_copy(
        update={"evidence_gap_display_labels": {}}
    )

    with pytest.raises(PresentationRulesError, match="display label"):
        assemble_analyst_review_package(
            inputs=inputs,
            presentation_rules=invalid_rules,
        )
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM analyst_review_packages"
        ).fetchone()[0] == 0


def test_package_is_deeply_immutable_and_retains_fixed_limitations(
    database_path: Path,
) -> None:
    package = create_analyst_review_package(
        recommendation_id=RECOMMENDATION_ID,
        presentation_rules_key=PRESENTATION_KEY,
        presentation_rules_version=PRESENTATION_VERSION,
        database_path=database_path,
        presentation_rules_root=PRESENTATION_ROOT,
    )

    with pytest.raises(ValidationError):
        package.case_summary.current_workflow_status = "ANALYST_CONFIRMED"
    assert package.limitations == (
        "The recommendation is advisory and has not been reviewed by an "
        "analyst.",
        "Visual-model observations are not independently verified visual "
        "truths.",
        "NOT_ESTABLISHED means the current evidence did not establish the "
        "requirement; it does not prove the opposite.",
        "The package does not submit, accept liability or contact any "
        "external party.",
    )


def test_offline_cli_emits_structured_json(
    database_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = review_main(
        [
            "--recommendation-id",
            str(RECOMMENDATION_ID),
            "--database",
            str(database_path),
            "--presentation-rules-root",
            str(PRESENTATION_ROOT),
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["case_summary"]["case_id"] == "CB-2025-0006"
    assert payload["recommendation"]["decision"] == (
        "INSUFFICIENT_EVIDENCE_TO_REPRESENT"
    )
    assert payload["case_summary"]["current_workflow_status"] == (
        "READY_FOR_ANALYST_REVIEW"
    )
