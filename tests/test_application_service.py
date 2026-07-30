"""Application-boundary and failure-isolated batch tests."""

from __future__ import annotations

from hashlib import sha256
import inspect
from pathlib import Path

import pytest

from dispute_copilot.application.errors import (
    CachedExecutionDisabledError,
)
from dispute_copilot.application.demo import (
    build_parser as build_demo_parser,
)
from dispute_copilot.application.models import (
    ExecutionMode,
    ExecutionStatus,
)
from dispute_copilot.application.repository import (
    SQLiteApplicationRepository,
)
from dispute_copilot.application.service import (
    DEFAULT_RULEBOOK_VERSION,
    DisputeCopilotApplication,
)
from dispute_copilot.analyst_review.registry import load_presentation_rules
from dispute_copilot.extraction.evidence_extraction_models import (
    ExtractionCandidate,
)
from dispute_copilot.extraction.provider_models import (
    ModelExecutionIdentity,
)
from dispute_copilot.extraction.provider_runtime import ExtractionRuntime
from dispute_copilot.human_review.decision_models import TrustedAnalystIdentity
from dispute_copilot.human_review.demo_app import (
    _load_case_outputs,
    create_demo_app,
)
from dispute_copilot.policy.fact_models import FactCatalogue
from dispute_copilot.policy.import_rulebook import import_rulebook

PROJECT_ROOT = Path(__file__).parents[1]
RULEBOOK_ROOT = PROJECT_ROOT / "data" / "rulebooks"


class FixtureVisionProvider:
    provider_version = "fixture-vision/application-v1"

    def describe_image(self, *, file_path: Path) -> str:
        return f"Fixture conversion for {file_path.name}."


class NotFoundExtractor:
    execution_identity = ModelExecutionIdentity(
        provider="fixture",
        model="all-not-found",
        adapter="fixture-adapter",
        prompt_version="fixture-prompt/1",
        schema_version="fixture-schema/1",
        parameters={},
    )

    def __init__(self, fail_case: str | None = None) -> None:
        self.fail_case = fail_case

    def extract_evidence_candidates(
        self,
        *,
        requested_facts,
        evidence_bundle,
        evidence_documents,
    ):
        if self.fail_case and any(
            file.file_name.startswith(self.fail_case)
            for file in evidence_bundle.evidence_files
        ):
            raise RuntimeError(
                "provider failed with api_key=super-secret-value"
            )
        return tuple(
            ExtractionCandidate(
                fact_name=fact.fact_name,
                status="NOT_FOUND",
                values=(),
                confidence=None,
            )
            for fact in requested_facts
        )


def _runtime(fail_case: str | None = None):
    return lambda: ExtractionRuntime(
        extractor=NotFoundExtractor(fail_case),
        vision_provider=FixtureVisionProvider(),
    )


@pytest.fixture
def application_database(tmp_path: Path) -> Path:
    path = tmp_path / "application.db"
    import_rulebook(RULEBOOK_ROOT / "take-home-v3", path)
    return path


def _application(
    database_path: Path,
    *,
    mode: ExecutionMode = ExecutionMode.LIVE,
    fail_case: str | None = None,
) -> DisputeCopilotApplication:
    return DisputeCopilotApplication(
        database_path=database_path,
        project_root=PROJECT_ROOT,
        execution_mode=mode,
        rulebook_version="take-home-v3",
        runtime_factory=_runtime(fail_case),
        auto_provision_rulebook=False,
    )


def test_new_application_runs_default_to_take_home_v4(
    tmp_path: Path,
) -> None:
    application = DisputeCopilotApplication(
        database_path=tmp_path / "default-v4.db",
        project_root=PROJECT_ROOT,
        runtime_factory=_runtime(),
    )

    assert DEFAULT_RULEBOOK_VERSION == "take-home-v4"
    assert application.rulebook_version == "take-home-v4"
    assert application.mapping_key == (
        "policy-aware-representment-eligibility-v2"
    )
    assert application.mapping_version == "2.0.0"
    assert application.presentation_rules_version == "1.3.0"

    arguments = build_demo_parser().parse_args(["--mode", "live"])
    assert arguments.rulebook_version == "take-home-v4"
    assert arguments.database == Path("data/v4-live-validation.db")


def test_explicit_take_home_v3_retains_historical_defaults(
    application_database: Path,
) -> None:
    application = _application(application_database)

    assert application.rulebook_version == "take-home-v3"
    assert application.mapping_key == (
        "default-representment-eligibility-v1"
    )
    assert application.mapping_version == "1.0.0"
    assert application.presentation_rules_version == "1.2.0"


def test_lists_all_cases_in_source_order(
    application_database: Path,
) -> None:
    app = _application(application_database)
    cases = app.list_cases()

    assert len(cases) == 10
    assert tuple(item.case_id for item in cases) == tuple(
        f"CB-2025-{index:04d}" for index in range(1, 11)
    )
    assert all(item.status is ExecutionStatus.NOT_RUN for item in cases)


def test_one_case_creates_complete_retrievable_lineage(
    application_database: Path,
) -> None:
    app = _application(application_database)

    result = app.run_case("CB-2025-0001")
    view = app.get_case_view("CB-2025-0001")

    assert result.attempt.status is ExecutionStatus.COMPLETED
    assert result.package is not None
    assert view.package == result.package
    assert view.resolution is not None
    assert view.technical_lineage is not None
    assert view.technical_lineage.provider == "fixture"
    assert view.technical_lineage.token_usage == "NOT RECORDED"
    assert view.evidence_targets


def test_batch_failure_is_isolated_and_secret_safe(
    application_database: Path,
) -> None:
    app = _application(
        application_database,
        fail_case="CB-2025-0003",
    )

    events = tuple(app.run_all_cases())
    terminal = {
        event.case_id: event
        for event in events
        if event.status in {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
        }
    }

    assert len(terminal) == 10
    assert terminal["CB-2025-0003"].status is ExecutionStatus.FAILED
    assert terminal["CB-2025-0004"].status is ExecutionStatus.COMPLETED
    assert "super-secret-value" not in (
        terminal["CB-2025-0003"].error_message or ""
    )
    assert app.get_case_view("CB-2025-0003").package is None
    assert app.get_case_view("CB-2025-0004").package is not None


def test_latest_failed_attempt_does_not_show_stale_completed_result(
    application_database: Path,
) -> None:
    successful = _application(application_database)
    successful.run_case("CB-2025-0001")

    failing = _application(
        application_database,
        fail_case="CB-2025-0001",
    )
    failing.run_case("CB-2025-0001")
    view = failing.get_case_view("CB-2025-0001")

    assert view.status is ExecutionStatus.FAILED
    assert view.package is None
    assert view.resolution is None
    assert view.evidence_targets == ()
    assert failing.get_review_package("CB-2025-0001") is None


def test_switching_from_completed_to_not_run_clears_case_details(
    application_database: Path,
) -> None:
    application = _application(application_database)
    application.run_case("CB-2025-0001")
    identity = TrustedAnalystIdentity(subject="test-analyst")

    completed = _load_case_outputs(
        application,
        identity,
        "CB-2025-0001",
    )
    not_run = _load_case_outputs(
        application,
        identity,
        "CB-2025-0002",
    )

    assert "PERSISTED RESULT" in completed[0]
    assert completed[1]
    assert completed[4]
    assert completed[6]
    assert completed[8] is not None
    assert "CB-2025-0002" in not_run[0]
    assert "NOT_RUN" in not_run[0]
    assert not_run[1:8] == ("", "", "", "", "", "", "")
    assert not_run[8] is None
    assert not_run[10] == {}
    assert not_run[11] is None


def test_completed_result_reopens_after_restart_without_model_runtime(
    application_database: Path,
) -> None:
    first = _application(application_database)
    completed = first.run_case("CB-2025-0001")
    calls = 0

    def forbidden_runtime():
        nonlocal calls
        calls += 1
        raise AssertionError("persisted retrieval must not construct a runtime")

    restarted = DisputeCopilotApplication(
        database_path=application_database,
        project_root=PROJECT_ROOT,
        execution_mode=ExecutionMode.LIVE,
        rulebook_version="take-home-v3",
        runtime_factory=forbidden_runtime,
        auto_provision_rulebook=False,
    )

    view = restarted.get_case_view("CB-2025-0001")

    assert view.status is ExecutionStatus.COMPLETED
    assert view.package == completed.package
    assert view.status_message == "Persisted live result."
    assert calls == 0

    cached = DisputeCopilotApplication(
        database_path=application_database,
        project_root=PROJECT_ROOT,
        execution_mode=ExecutionMode.CACHED_VIEW,
        rulebook_version="take-home-v3",
        runtime_factory=forbidden_runtime,
        auto_provision_rulebook=False,
    )
    cached_view = cached.get_case_view("CB-2025-0001")
    cached_outputs = _load_case_outputs(
        cached,
        TrustedAnalystIdentity(subject="test-analyst"),
        "CB-2025-0001",
    )

    assert cached_view.execution_mode is ExecutionMode.CACHED_VIEW
    assert cached_view.status_message == "Persisted cached result."
    assert "PERSISTED/CACHED RESULT" in cached_outputs[0]
    assert calls == 0


def test_cached_mode_never_creates_runtime_or_attempt(
    application_database: Path,
) -> None:
    calls = 0

    def forbidden_runtime():
        nonlocal calls
        calls += 1
        raise AssertionError("runtime must not be constructed")

    app = DisputeCopilotApplication(
        database_path=application_database,
        project_root=PROJECT_ROOT,
        execution_mode=ExecutionMode.CACHED_VIEW,
        rulebook_version="take-home-v3",
        runtime_factory=forbidden_runtime,
        auto_provision_rulebook=False,
    )

    with pytest.raises(CachedExecutionDisabledError):
        app.run_case("CB-2025-0001")

    assert calls == 0
    assert app.list_cases()[0].status is ExecutionStatus.NOT_RUN


def test_attempt_transition_history_is_append_only(tmp_path: Path) -> None:
    repository = SQLiteApplicationRepository(tmp_path / "events.db")
    batch = repository.create_batch(
        case_count=1,
        execution_mode=ExecutionMode.LIVE,
    )
    queued = repository.queue_attempt(
        batch_id=batch.batch_id,
        case_id="CB-2025-0001",
        execution_mode=ExecutionMode.LIVE,
    )
    running = repository.mark_running(queued.attempt_id)
    repository.mark_failed(
        running.attempt_id,
        error_type="FixtureError",
        error_message="Safe diagnostic.",
    )

    with repository._connect() as connection:
        rows = connection.execute(
            """
            SELECT status, snapshot_json
            FROM demo_case_attempt_events
            WHERE attempt_id = ?
            ORDER BY sequence_number
            """,
            (str(queued.attempt_id),),
        ).fetchall()

    assert tuple(row["status"] for row in rows) == (
        "QUEUED",
        "RUNNING",
        "FAILED",
    )
    assert all("MODEL_API_KEY" not in row["snapshot_json"] for row in rows)


def test_presentation_rules_cover_every_v3_fact() -> None:
    catalogue = FactCatalogue.model_validate_json(
        (
            RULEBOOK_ROOT / "take-home-v3" / "facts" / "catalogue.json"
        ).read_bytes()
    )
    rules = load_presentation_rules(
        presentation_rules_key="analyst-review-package-v1",
        presentation_rules_version="1.2.0",
        presentation_rules_root=(
            PROJECT_ROOT / "data" / "presentation-rules"
        ),
    )

    assert set(rules.evidence_gap_display_labels) == {
        fact.fact_name for fact in catalogue.facts
    }


def test_cached_demo_lists_cases_disables_runs_and_clears_details(
    application_database: Path,
) -> None:
    application = _application(
        application_database,
        mode=ExecutionMode.CACHED_VIEW,
    )
    identity = TrustedAnalystIdentity(subject="test-analyst")
    demo = create_demo_app(
        application=application,
        analyst_identity=identity,
    )
    config = demo.get_config_file()
    run_buttons = {
        component["props"]["value"]: component["props"]["interactive"]
        for component in config["components"]
        if isinstance(component.get("props", {}).get("value"), str)
        and component["props"]["value"]
        in {"Run All Cases", "Run Selected Case"}
    }
    outputs = _load_case_outputs(
        application,
        identity,
        "CB-2025-0001",
    )

    assert run_buttons == {
        "Run Selected Case": False,
        "Run All Cases": False,
    }
    assert "NOT_RUN" in outputs[0]
    assert outputs[1:8] == ("", "", "", "", "", "", "")
    assert outputs[8] is None


def test_demo_ui_does_not_import_pipeline_logic_directly() -> None:
    source = (
        PROJECT_ROOT
        / "src"
        / "dispute_copilot"
        / "human_review"
        / "demo_app.py"
    ).read_text(encoding="utf-8")

    assert "dispute_copilot.policy" not in source
    assert "dispute_copilot.extraction" not in source
    assert "dispute_copilot.evaluation" not in source
    assert "dispute_copilot.recommendation" not in source


def test_demo_run_callbacks_are_streaming_generators(
    application_database: Path,
) -> None:
    application = _application(application_database)
    demo = create_demo_app(
        application=application,
        analyst_identity=TrustedAnalystIdentity(subject="test-analyst"),
    )

    run_selected_dependency = demo.fns[3]
    run_all_dependency = demo.fns[4]

    assert inspect.isgeneratorfunction(run_selected_dependency.fn)
    assert inspect.isgeneratorfunction(run_all_dependency.fn)


def test_cached_mode_discovers_genuine_baseline_without_mutating_it() -> None:
    database_path = (
        PROJECT_ROOT
        / "data"
        / "baselines"
        / "live-extraction-validation-v1"
        / "fixture-demo.db"
    )
    before = sha256(database_path.read_bytes()).hexdigest()
    application = DisputeCopilotApplication(
        database_path=database_path,
        project_root=PROJECT_ROOT,
        execution_mode=ExecutionMode.CACHED_VIEW,
        rulebook_version="take-home-v3",
    )

    queue = application.list_cases()
    view = application.get_case_view("CB-2025-0001")
    after = sha256(database_path.read_bytes()).hexdigest()

    assert tuple(item.status for item in queue[:4]) == (
        ExecutionStatus.COMPLETED,
    ) * 4
    assert all(
        item.status is ExecutionStatus.NOT_RUN for item in queue[4:]
    )
    assert view.execution_mode is ExecutionMode.CACHED_VIEW
    assert view.package is not None
    assert view.technical_lineage is not None
    assert view.technical_lineage.provider == "fixture"
    assert before == after
