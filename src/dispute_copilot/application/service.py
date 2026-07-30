"""Stable case execution and retrieval API used by CLI and Gradio."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import json
from pathlib import Path
import sqlite3
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from dispute_copilot.analyst_review.models import AnalystReviewPackage
from dispute_copilot.analyst_review.action_repository import (
    SQLiteSuggestedActionRepository,
)
from dispute_copilot.analyst_review.action_service import (
    create_suggested_analyst_action,
)
from dispute_copilot.analyst_review.registry import (
    DEFAULT_PRESENTATION_RULES_KEY,
    DEFAULT_PRESENTATION_RULES_ROOT,
    DEFAULT_PRESENTATION_RULES_VERSION,
)
from dispute_copilot.analyst_review.service import (
    create_analyst_review_package,
)
from dispute_copilot.application.errors import (
    CachedExecutionDisabledError,
    CaseCatalogueError,
    sanitize_execution_error,
)
from dispute_copilot.application.models import (
    AnalystCaseView,
    CaseExecutionAttempt,
    CaseExecutionResult,
    CaseProgressEvent,
    CaseQueueItem,
    ExecutionMode,
    ExecutionStatus,
    TechnicalLineage,
)
from dispute_copilot.application.repository import (
    SQLiteApplicationRepository,
)
from dispute_copilot.config import load_model_provider_settings
from dispute_copilot.domain.case import Case
from dispute_copilot.evaluation.service import (
    evaluate_selection_requirements,
)
from dispute_copilot.extraction.fact_resolution_repository import (
    SQLiteFactResolutionRepository,
)
from dispute_copilot.extraction.fact_resolution_service import (
    resolve_selection_facts,
)
from dispute_copilot.extraction.provider_models import (
    ModelExecutionIdentity,
)
from dispute_copilot.extraction.provider_runtime import (
    ExtractionRuntime,
    create_extraction_runtime,
)
from dispute_copilot.human_review.context import load_human_review_context
from dispute_copilot.human_review.display_adapter import (
    EvidencePresentationAdapter,
)
from dispute_copilot.intake.case_loader import load_case
from dispute_copilot.orchestration.service import create_case_run
from dispute_copilot.policy.import_rulebook import import_rulebook
from dispute_copilot.policy.constants import DEFAULT_RULEBOOK_VERSION
from dispute_copilot.policy.repository import SQLitePolicyRepository
from dispute_copilot.policy.selection_service import select_policy_for_run
from dispute_copilot.recommendation.service import create_recommendation
from dispute_copilot.repositories.sqlite import SQLiteCaseRunRepository

_CASE_LIST_ADAPTER = TypeAdapter(list[Case])

DEFAULT_MAPPING_KEY = "default-representment-eligibility-v1"
DEFAULT_MAPPING_VERSION = "1.0.0"
V4_MAPPING_KEY = "policy-aware-representment-eligibility-v2"
V4_MAPPING_VERSION = "2.0.0"
V4_PRESENTATION_RULES_VERSION = "1.3.0"

RuntimeFactory = Callable[[], ExtractionRuntime]


class DisputeCopilotApplication:
    """Compose the existing immutable pipeline behind one stable boundary."""

    def __init__(
        self,
        *,
        database_path: Path,
        project_root: Path = Path("."),
        execution_mode: ExecutionMode = ExecutionMode.LIVE,
        cases_path: Path | None = None,
        evidence_directory: Path | None = None,
        rulebook_root: Path | None = None,
        rulebook_version: str = DEFAULT_RULEBOOK_VERSION,
        mapping_root: Path | None = None,
        mapping_key: str | None = None,
        mapping_version: str | None = None,
        presentation_rules_root: Path | None = None,
        presentation_rules_key: str = DEFAULT_PRESENTATION_RULES_KEY,
        presentation_rules_version: str | None = None,
        runtime_factory: RuntimeFactory | None = None,
        auto_provision_rulebook: bool = True,
    ) -> None:
        self.database_path = database_path
        self.project_root = project_root.resolve()
        self.execution_mode = execution_mode
        self.cases_path = (
            cases_path or self.project_root / "data" / "cases.json"
        )
        self.evidence_directory = (
            evidence_directory
            or self.project_root / "data" / "evidence"
        )
        self.rulebook_root = (
            rulebook_root or self.project_root / "data" / "rulebooks"
        )
        self.rulebook_version = rulebook_version
        self.mapping_root = (
            mapping_root
            or self.project_root / "data" / "recommendation-mappings"
        )
        default_mapping = (
            (V4_MAPPING_KEY, V4_MAPPING_VERSION)
            if rulebook_version == "take-home-v4"
            else (DEFAULT_MAPPING_KEY, DEFAULT_MAPPING_VERSION)
        )
        self.mapping_key = mapping_key or default_mapping[0]
        self.mapping_version = mapping_version or default_mapping[1]
        self.presentation_rules_root = (
            presentation_rules_root
            or self.project_root / "data" / "presentation-rules"
        )
        self.presentation_rules_key = presentation_rules_key
        self.presentation_rules_version = (
            presentation_rules_version
            or (
                V4_PRESENTATION_RULES_VERSION
                if rulebook_version == "take-home-v4"
                else DEFAULT_PRESENTATION_RULES_VERSION
            )
        )
        self.runtime_factory = (
            runtime_factory or self._create_configured_runtime
        )
        self.auto_provision_rulebook = auto_provision_rulebook
        self.repository = SQLiteApplicationRepository(
            database_path,
            initialize_schema=execution_mode is ExecutionMode.LIVE,
        )

    def list_cases(self) -> tuple[CaseQueueItem, ...]:
        """Return every supplied case in source order with latest state."""
        return tuple(
            self._queue_item(case)
            for case in self._load_cases()
        )

    def run_case(self, case_id: str) -> CaseExecutionResult:
        """Run one fresh isolated live attempt and return its outcome."""
        terminal = None
        for event in self.run_case_progress(case_id):
            terminal = event
        assert terminal is not None
        attempt = self.repository.get_attempt(terminal.attempt_id)
        assert attempt is not None
        package = (
            self.get_review_package(case_id)
            if attempt.status is ExecutionStatus.COMPLETED
            else None
        )
        return CaseExecutionResult(attempt=attempt, package=package)

    def run_case_progress(
        self,
        case_id: str,
    ) -> Iterator[CaseProgressEvent]:
        """Stream queued, running and terminal state for one fresh attempt."""
        self._require_live_execution()
        self._require_case(case_id)
        batch = self.repository.create_batch(
            case_count=1,
            execution_mode=ExecutionMode.LIVE,
        )
        attempt = self.repository.queue_attempt(
            batch_id=batch.batch_id,
            case_id=case_id,
            execution_mode=ExecutionMode.LIVE,
        )
        yield self._event(attempt)
        running = self.repository.mark_running(attempt.attempt_id)
        yield self._event(running)
        result = self._run_started_attempt(running)
        yield self._event(result.attempt)
        self.repository.finalize_batch(batch.batch_id)

    def run_all_cases(self) -> Iterator[CaseProgressEvent]:
        """Run all cases sequentially while isolating every failure."""
        self._require_live_execution()
        cases = self._load_cases()
        batch = self.repository.create_batch(
            case_count=len(cases),
            execution_mode=ExecutionMode.LIVE,
        )
        attempts = []
        for case in cases:
            attempt = self.repository.queue_attempt(
                batch_id=batch.batch_id,
                case_id=case.case_id,
                execution_mode=ExecutionMode.LIVE,
            )
            attempts.append(attempt)
            yield self._event(attempt)

        for attempt in attempts:
            running = self.repository.mark_running(attempt.attempt_id)
            yield self._event(running)
            result = self._run_started_attempt(running)
            yield self._event(result.attempt)
        self.repository.finalize_batch(batch.batch_id)

    def get_case_progress(self, case_id: str) -> CaseQueueItem:
        return self._queue_item(self._require_case(case_id))

    def get_review_package(
        self,
        case_id: str,
    ) -> AnalystReviewPackage | None:
        """Load the latest package only when the latest attempt completed."""
        self._require_case(case_id)
        attempt = self.repository.latest_for_case(case_id)
        if (
            attempt is None
            and self.execution_mode is ExecutionMode.CACHED_VIEW
        ):
            package = self._latest_cached_package(case_id)
            if package is None:
                return None
            return load_human_review_context(
                review_package_id=package.review_package_id,
                database_path=self.database_path,
            ).package
        if (
            attempt is None
            or attempt.status is not ExecutionStatus.COMPLETED
            or attempt.review_package_id is None
        ):
            return None
        context = load_human_review_context(
            review_package_id=attempt.review_package_id,
            database_path=self.database_path,
        )
        return context.package

    def locate_evidence(
        self,
        case_id: str,
    ) -> tuple:
        """Return integrity-checked targets from the latest completed package."""
        package = self.get_review_package(case_id)
        if package is None:
            return ()
        adapter = EvidencePresentationAdapter(
            database_path=self.database_path,
            project_root=self.project_root,
        )
        targets = []
        for source in package.supporting_sources:
            if source.source_document is None:
                continue
            targets.append(
                adapter.for_source(
                    review_package_id=package.review_package_id,
                    source_ref_id=source.source_ref_id,
                ).target
            )
        for gap in package.evidence_gaps:
            targets.append(
                adapter.for_gap(
                    review_package_id=package.review_package_id,
                    gap_id=gap.gap_id,
                ).target
            )
        return tuple(targets)

    def get_case_view(self, case_id: str) -> AnalystCaseView:
        """Return a presentation DTO without executing any pipeline stage."""
        case = self._require_case(case_id)
        attempt = self.repository.latest_for_case(case_id)
        if attempt is None:
            if self.execution_mode is ExecutionMode.CACHED_VIEW:
                package = self._latest_cached_package(case_id)
                if package is not None:
                    return self._completed_view(
                        case=case,
                        package=package,
                        attempt=None,
                        execution_mode=ExecutionMode.CACHED_VIEW,
                    )
            return AnalystCaseView(
                case=case,
                status=ExecutionStatus.NOT_RUN,
                execution_mode=None,
                status_message="This case has not been run.",
            )
        if attempt.status is not ExecutionStatus.COMPLETED:
            return AnalystCaseView(
                case=case,
                status=attempt.status,
                execution_mode=attempt.execution_mode,
                attempt=attempt,
                status_message=(
                    attempt.sanitized_error_message
                    or f"This case is {attempt.status.value}."
                ),
            )
        assert attempt.review_package_id is not None
        context = load_human_review_context(
            review_package_id=attempt.review_package_id,
            database_path=self.database_path,
        )
        return self._completed_view(
            case=case,
            package=context.package,
            attempt=attempt,
            execution_mode=(
                ExecutionMode.CACHED_VIEW
                if self.execution_mode is ExecutionMode.CACHED_VIEW
                else attempt.execution_mode
            ),
        )

    def _completed_view(
        self,
        *,
        case: Case,
        package: AnalystReviewPackage,
        attempt: CaseExecutionAttempt | None,
        execution_mode: ExecutionMode,
    ) -> AnalystCaseView:
        context = load_human_review_context(
            review_package_id=package.review_package_id,
            database_path=self.database_path,
        )
        package = context.package
        resolution = SQLiteFactResolutionRepository(
            self.database_path
        ).get(package.lineage.fact_resolution_id)
        if resolution is None:
            raise CaseCatalogueError(
                "Completed attempt references a missing fact resolution"
            )
        identity = _model_identity(resolution.resolver_version)
        suggested_action = SQLiteSuggestedActionRepository(
            self.database_path,
            initialize_schema=(
                self.execution_mode is ExecutionMode.LIVE
            ),
        ).get_for_package(package.review_package_id)
        lineage = package.lineage
        return AnalystCaseView(
            case=case,
            status=ExecutionStatus.COMPLETED,
            execution_mode=execution_mode,
            attempt=attempt,
            package=package,
            suggested_action=suggested_action,
            resolution=resolution,
            evidence_targets=self.locate_evidence(case.case_id),
            technical_lineage=TechnicalLineage(
                run_id=lineage.run_id,
                selection_id=lineage.selection_id,
                resolution_id=lineage.fact_resolution_id,
                evaluation_id=lineage.evaluation_id,
                recommendation_id=lineage.recommendation_id,
                review_package_id=package.review_package_id,
                rulebook_version=resolution.rulebook_version,
                policy_id=resolution.policy_id,
                provider=identity.provider,
                model=identity.model,
                adapter=identity.adapter,
                prompt_version=identity.prompt_version,
                schema_version=identity.schema_version,
                parameters_json=json.dumps(
                    identity.model_dump(mode="json")["parameters"],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
            status_message=(
                "Persisted live result."
                if execution_mode is ExecutionMode.LIVE
                else "Persisted cached result."
            ),
        )

    def _run_started_attempt(
        self,
        attempt: CaseExecutionAttempt,
    ) -> CaseExecutionResult:
        stage = "RULEBOOK_PROVISIONING"
        run = None
        selection = None
        resolution = None
        evaluation = None
        recommendation = None
        try:
            self._ensure_policy_available(attempt.case_id)
            stage = "PROVIDER_CONFIGURATION"
            runtime = self.runtime_factory()
            stage = "INTAKE"
            run = create_case_run(
                case_id=attempt.case_id,
                cases_path=self.cases_path,
                evidence_directory=self.evidence_directory,
            )
            SQLiteCaseRunRepository(self.database_path).save(run)
            stage = "POLICY_SELECTION"
            selection = select_policy_for_run(
                run_id=run.run_id,
                rulebook_version=self.rulebook_version,
                database_path=self.database_path,
            )
            stage = "FACT_RESOLUTION"
            resolution = resolve_selection_facts(
                selection_id=selection.selection_id,
                extractor=runtime.extractor,
                vision_provider=runtime.vision_provider,
                database_path=self.database_path,
            )
            stage = "REQUIREMENT_EVALUATION"
            evaluation = evaluate_selection_requirements(
                selection_id=selection.selection_id,
                database_path=self.database_path,
                rulebook_root=self.rulebook_root,
            )
            stage = "RECOMMENDATION"
            recommendation = create_recommendation(
                selection_id=selection.selection_id,
                mapping_key=self.mapping_key,
                mapping_version=self.mapping_version,
                database_path=self.database_path,
                mapping_root=self.mapping_root,
            )
            stage = "ANALYST_REVIEW_PACKAGE"
            package = create_analyst_review_package(
                selection_id=selection.selection_id,
                presentation_rules_key=self.presentation_rules_key,
                presentation_rules_version=(
                    self.presentation_rules_version
                ),
                database_path=self.database_path,
                presentation_rules_root=self.presentation_rules_root,
            )
            stage = "SUGGESTED_ANALYST_ACTION"
            create_suggested_analyst_action(
                package=package,
                database_path=self.database_path,
            )
            completed = self.repository.mark_completed(
                attempt.attempt_id,
                run_id=run.run_id,
                selection_id=selection.selection_id,
                resolution_id=resolution.resolution_id,
                evaluation_id=evaluation.evaluation_id,
                recommendation_id=recommendation.recommendation_id,
                review_package_id=package.review_package_id,
            )
            return CaseExecutionResult(
                attempt=completed,
                package=package,
            )
        except Exception as error:
            sanitized = sanitize_execution_error(error, stage=stage)
            failed = self.repository.mark_failed(
                attempt.attempt_id,
                error_type=sanitized.error_type,
                error_message=sanitized.message,
                run_id=None if run is None else run.run_id,
                selection_id=(
                    None if selection is None else selection.selection_id
                ),
                resolution_id=(
                    None if resolution is None else resolution.resolution_id
                ),
                evaluation_id=(
                    None if evaluation is None else evaluation.evaluation_id
                ),
                recommendation_id=(
                    None
                    if recommendation is None
                    else recommendation.recommendation_id
                ),
            )
            return CaseExecutionResult(attempt=failed)

    def _ensure_policy_available(self, case_id: str) -> None:
        case = self._require_case(case_id)
        policy_repository = SQLitePolicyRepository(self.database_path)
        existing = policy_repository.get_imported(
            self.rulebook_version,
            case.scheme,
            case.reason_code,
        )
        if existing is not None:
            return
        if not self.auto_provision_rulebook:
            raise CaseCatalogueError(
                "The exact configured policy has not been provisioned"
            )
        import_rulebook(
            self.rulebook_root / self.rulebook_version,
            self.database_path,
        )

    def _load_cases(self) -> tuple[Case, ...]:
        try:
            content = self.cases_path.read_text(encoding="utf-8")
            cases = _CASE_LIST_ADAPTER.validate_json(content)
        except (OSError, ValidationError, ValueError) as error:
            raise CaseCatalogueError(
                f"Case catalogue is unavailable or invalid: {self.cases_path}"
            ) from error
        ids = tuple(case.case_id for case in cases)
        if len(ids) != len(set(ids)):
            raise CaseCatalogueError("Case catalogue contains duplicate IDs")
        if not cases:
            raise CaseCatalogueError("Case catalogue is empty")
        return tuple(cases)

    def _require_case(self, case_id: str) -> Case:
        try:
            return load_case(case_id, self.cases_path)
        except (OSError, ValueError, LookupError) as error:
            raise CaseCatalogueError(
                f"Case '{case_id}' is not available"
            ) from error

    def _queue_item(self, case: Case) -> CaseQueueItem:
        attempt = self.repository.latest_for_case(case.case_id)
        if attempt is None:
            if self.execution_mode is ExecutionMode.CACHED_VIEW:
                package = self._latest_cached_package(case.case_id)
                if package is not None:
                    return CaseQueueItem(
                        case_id=case.case_id,
                        card_scheme=case.scheme,
                        reason_code=case.reason_code,
                        reason_code_label=case.reason_code_label,
                        declared_evidence_count=len(
                            case.merchant_evidence_documents
                        ),
                        status=ExecutionStatus.COMPLETED,
                        execution_mode=ExecutionMode.CACHED_VIEW,
                        review_package_id=package.review_package_id,
                    )
            return CaseQueueItem(
                case_id=case.case_id,
                card_scheme=case.scheme,
                reason_code=case.reason_code,
                reason_code_label=case.reason_code_label,
                declared_evidence_count=len(
                    case.merchant_evidence_documents
                ),
                status=ExecutionStatus.NOT_RUN,
            )
        return CaseQueueItem(
            case_id=case.case_id,
            card_scheme=case.scheme,
            reason_code=case.reason_code,
            reason_code_label=case.reason_code_label,
            declared_evidence_count=len(
                case.merchant_evidence_documents
            ),
            status=attempt.status,
            execution_mode=attempt.execution_mode,
            latest_attempt_id=attempt.attempt_id,
            review_package_id=attempt.review_package_id,
            error_message=attempt.sanitized_error_message,
        )

    def _latest_cached_package(
        self,
        case_id: str,
    ) -> AnalystReviewPackage | None:
        """Discover only genuine persisted packages without writing tables."""
        if not self.database_path.is_file():
            return None
        try:
            uri = f"file:{self.database_path.resolve()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                rows = connection.execute(
                    """
                    SELECT package_json
                    FROM analyst_review_packages
                    ORDER BY created_at DESC
                    """
                ).fetchall()
        except sqlite3.Error:
            return None
        for (payload,) in rows:
            try:
                package = AnalystReviewPackage.model_validate_json(payload)
            except ValidationError:
                continue
            if package.case_summary.case_id == case_id:
                return package
        return None

    @staticmethod
    def _event(attempt: CaseExecutionAttempt) -> CaseProgressEvent:
        return CaseProgressEvent(
            batch_id=attempt.batch_id,
            attempt_id=attempt.attempt_id,
            case_id=attempt.case_id,
            status=attempt.status,
            occurred_at=(
                attempt.completed_at
                or attempt.started_at
                or attempt.queued_at
            ),
            review_package_id=attempt.review_package_id,
            error_message=attempt.sanitized_error_message,
        )

    def _require_live_execution(self) -> None:
        if self.execution_mode is not ExecutionMode.LIVE:
            raise CachedExecutionDisabledError(
                "Live execution is disabled in CACHED_VIEW mode."
            )

    @staticmethod
    def _create_configured_runtime() -> ExtractionRuntime:
        return create_extraction_runtime(
            load_model_provider_settings()
        )


def _model_identity(resolver_version: str) -> ModelExecutionIdentity:
    try:
        return ModelExecutionIdentity.model_validate_json(resolver_version)
    except (ValidationError, ValueError):
        return ModelExecutionIdentity(
            provider="NOT RECORDED",
            model="NOT RECORDED",
            adapter="NOT RECORDED",
            prompt_version="NOT RECORDED",
            schema_version="NOT RECORDED",
            parameters={},
        )
