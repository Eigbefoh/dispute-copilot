"""SQLite persistence for demo batches and isolated case attempts."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from dispute_copilot.application.errors import ApplicationPersistenceError
from dispute_copilot.application.models import (
    BatchExecution,
    BatchStatus,
    CaseExecutionAttempt,
    ExecutionMode,
    ExecutionStatus,
    calculate_attempt_content_hash,
)


class SQLiteApplicationRepository:
    """Persist application progress without mutating pipeline lineage."""

    def __init__(
        self,
        database_path: Path,
        *,
        initialize_schema: bool = True,
    ) -> None:
        self.database_path = database_path
        self.initialize_schema = initialize_schema
        if initialize_schema:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        if self.initialize_schema:
            connection = sqlite3.connect(self.database_path)
        else:
            connection = sqlite3.connect(
                f"file:{self.database_path.resolve()}?mode=ro",
                uri=True,
            )
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS demo_batches (
                    batch_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    execution_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    case_count INTEGER NOT NULL,
                    completed_count INTEGER NOT NULL,
                    failed_count INTEGER NOT NULL,
                    CHECK (execution_mode IN ('LIVE', 'CACHED_VIEW')),
                    CHECK (status IN (
                        'RUNNING',
                        'COMPLETED',
                        'COMPLETED_WITH_FAILURES'
                    ))
                );

                CREATE TABLE IF NOT EXISTS demo_case_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    execution_mode TEXT NOT NULL,
                    queued_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    run_id TEXT,
                    selection_id TEXT,
                    resolution_id TEXT,
                    evaluation_id TEXT,
                    recommendation_id TEXT,
                    review_package_id TEXT,
                    sanitized_error_type TEXT,
                    sanitized_error_message TEXT,
                    content_hash TEXT NOT NULL,
                    UNIQUE (case_id, attempt_number),
                    FOREIGN KEY (batch_id) REFERENCES demo_batches(batch_id),
                    CHECK (status IN (
                        'QUEUED',
                        'RUNNING',
                        'COMPLETED',
                        'FAILED'
                    )),
                    CHECK (execution_mode IN ('LIVE', 'CACHED_VIEW'))
                );

                CREATE TABLE IF NOT EXISTS demo_case_attempt_events (
                    event_id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL,
                    sequence_number INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    UNIQUE (attempt_id, sequence_number),
                    FOREIGN KEY (attempt_id)
                        REFERENCES demo_case_attempts(attempt_id)
                );

                CREATE INDEX IF NOT EXISTS idx_demo_attempts_case
                    ON demo_case_attempts(case_id, attempt_number DESC);
                CREATE INDEX IF NOT EXISTS idx_demo_attempts_batch
                    ON demo_case_attempts(batch_id, queued_at);
                """
            )

    def create_batch(
        self,
        *,
        case_count: int,
        execution_mode: ExecutionMode,
        batch_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> BatchExecution:
        now = created_at or datetime.now(timezone.utc)
        batch = BatchExecution(
            batch_id=batch_id or uuid4(),
            execution_mode=execution_mode,
            status=BatchStatus.RUNNING,
            case_count=case_count,
            completed_count=0,
            failed_count=0,
            created_at=now,
            started_at=now,
            completed_at=None,
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO demo_batches (
                        batch_id, created_at, started_at, completed_at,
                        execution_mode, status, case_count,
                        completed_count, failed_count
                    ) VALUES (?, ?, ?, NULL, ?, ?, ?, 0, 0)
                    """,
                    (
                        str(batch.batch_id),
                        batch.created_at.isoformat(),
                        batch.started_at.isoformat(),
                        batch.execution_mode.value,
                        batch.status.value,
                        batch.case_count,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ApplicationPersistenceError(
                f"Batch '{batch.batch_id}' already exists"
            ) from error
        return batch

    def queue_attempt(
        self,
        *,
        batch_id: UUID,
        case_id: str,
        execution_mode: ExecutionMode,
        queued_at: datetime | None = None,
    ) -> CaseExecutionAttempt:
        now = queued_at or datetime.now(timezone.utc)
        with self._connect() as connection:
            batch = connection.execute(
                "SELECT status FROM demo_batches WHERE batch_id = ?",
                (str(batch_id),),
            ).fetchone()
            if batch is None:
                raise ApplicationPersistenceError(
                    f"Batch '{batch_id}' was not found"
                )
            if batch["status"] != BatchStatus.RUNNING.value:
                raise ApplicationPersistenceError(
                    "Cannot queue an attempt in a completed batch"
                )
            row = connection.execute(
                """
                SELECT COALESCE(MAX(attempt_number), 0) + 1
                FROM demo_case_attempts
                WHERE case_id = ?
                """,
                (case_id,),
            ).fetchone()
            attempt = _build_attempt(
                attempt_id=uuid4(),
                batch_id=batch_id,
                case_id=case_id,
                attempt_number=int(row[0]),
                status=ExecutionStatus.QUEUED,
                execution_mode=execution_mode,
                queued_at=now,
            )
            connection.execute("BEGIN IMMEDIATE")
            self._insert_attempt(connection, attempt)
            self._append_event(connection, attempt)
        return attempt

    def mark_running(
        self,
        attempt_id: UUID,
        *,
        started_at: datetime | None = None,
    ) -> CaseExecutionAttempt:
        current = self._require_attempt(attempt_id)
        if current.status is not ExecutionStatus.QUEUED:
            raise ApplicationPersistenceError(
                "Only a queued attempt can start"
            )
        updated = _build_attempt(
            **current.model_dump(
                exclude={"content_hash", "status", "started_at"}
            ),
            status=ExecutionStatus.RUNNING,
            started_at=started_at or datetime.now(timezone.utc),
        )
        return self._replace_attempt(current, updated)

    def mark_completed(
        self,
        attempt_id: UUID,
        *,
        run_id: UUID,
        selection_id: UUID,
        resolution_id: UUID,
        evaluation_id: UUID,
        recommendation_id: UUID,
        review_package_id: UUID,
        completed_at: datetime | None = None,
    ) -> CaseExecutionAttempt:
        current = self._require_attempt(attempt_id)
        if current.status is not ExecutionStatus.RUNNING:
            raise ApplicationPersistenceError(
                "Only a running attempt can complete"
            )
        updated = _build_attempt(
            **current.model_dump(
                exclude={
                    "content_hash",
                    "status",
                    "run_id",
                    "selection_id",
                    "resolution_id",
                    "evaluation_id",
                    "recommendation_id",
                    "review_package_id",
                    "completed_at",
                }
            ),
            status=ExecutionStatus.COMPLETED,
            completed_at=completed_at or datetime.now(timezone.utc),
            run_id=run_id,
            selection_id=selection_id,
            resolution_id=resolution_id,
            evaluation_id=evaluation_id,
            recommendation_id=recommendation_id,
            review_package_id=review_package_id,
        )
        return self._replace_attempt(current, updated)

    def mark_failed(
        self,
        attempt_id: UUID,
        *,
        error_type: str,
        error_message: str,
        run_id: UUID | None = None,
        selection_id: UUID | None = None,
        resolution_id: UUID | None = None,
        evaluation_id: UUID | None = None,
        recommendation_id: UUID | None = None,
        completed_at: datetime | None = None,
    ) -> CaseExecutionAttempt:
        current = self._require_attempt(attempt_id)
        if current.status not in {
            ExecutionStatus.QUEUED,
            ExecutionStatus.RUNNING,
        }:
            raise ApplicationPersistenceError(
                "Only a queued or running attempt can fail"
            )
        started_at = current.started_at or datetime.now(timezone.utc)
        updated = _build_attempt(
            **current.model_dump(
                exclude={
                    "content_hash",
                    "status",
                    "started_at",
                    "completed_at",
                    "run_id",
                    "selection_id",
                    "resolution_id",
                    "evaluation_id",
                    "recommendation_id",
                    "sanitized_error_type",
                    "sanitized_error_message",
                }
            ),
            status=ExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at or datetime.now(timezone.utc),
            run_id=run_id,
            selection_id=selection_id,
            resolution_id=resolution_id,
            evaluation_id=evaluation_id,
            recommendation_id=recommendation_id,
            sanitized_error_type=error_type,
            sanitized_error_message=error_message,
        )
        return self._replace_attempt(current, updated)

    def finalize_batch(
        self,
        batch_id: UUID,
        *,
        completed_at: datetime | None = None,
    ) -> BatchExecution:
        attempts = self.list_batch_attempts(batch_id)
        if not attempts:
            raise ApplicationPersistenceError(
                "Cannot finalize a batch without attempts"
            )
        if any(
            item.status
            not in {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED}
            for item in attempts
        ):
            raise ApplicationPersistenceError(
                "Cannot finalize a batch with active attempts"
            )
        completed_count = sum(
            item.status is ExecutionStatus.COMPLETED for item in attempts
        )
        failed_count = len(attempts) - completed_count
        status = (
            BatchStatus.COMPLETED
            if failed_count == 0
            else BatchStatus.COMPLETED_WITH_FAILURES
        )
        now = completed_at or datetime.now(timezone.utc)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE demo_batches
                SET status = ?, completed_at = ?,
                    completed_count = ?, failed_count = ?
                WHERE batch_id = ? AND status = 'RUNNING'
                """,
                (
                    status.value,
                    now.isoformat(),
                    completed_count,
                    failed_count,
                    str(batch_id),
                ),
            )
            if cursor.rowcount != 1:
                raise ApplicationPersistenceError(
                    "Batch is missing or already finalized"
                )
        batch = self.get_batch(batch_id)
        assert batch is not None
        return batch

    def get_batch(self, batch_id: UUID) -> BatchExecution | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM demo_batches WHERE batch_id = ?",
                (str(batch_id),),
            ).fetchone()
        return None if row is None else _batch_from_row(row)

    def get_attempt(
        self,
        attempt_id: UUID,
    ) -> CaseExecutionAttempt | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM demo_case_attempts WHERE attempt_id = ?",
                    (str(attempt_id),),
                ).fetchone()
        except sqlite3.OperationalError:
            return None
        return None if row is None else _attempt_from_row(row)

    def latest_for_case(
        self,
        case_id: str,
    ) -> CaseExecutionAttempt | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT *
                    FROM demo_case_attempts
                    WHERE case_id = ?
                    ORDER BY attempt_number DESC
                    LIMIT 1
                    """,
                    (case_id,),
                ).fetchone()
        except sqlite3.OperationalError:
            return None
        return None if row is None else _attempt_from_row(row)

    def latest_completed_for_case(
        self,
        case_id: str,
    ) -> CaseExecutionAttempt | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT *
                    FROM demo_case_attempts
                    WHERE case_id = ? AND status = 'COMPLETED'
                    ORDER BY attempt_number DESC
                    LIMIT 1
                    """,
                    (case_id,),
                ).fetchone()
        except sqlite3.OperationalError:
            return None
        return None if row is None else _attempt_from_row(row)

    def list_batch_attempts(
        self,
        batch_id: UUID,
    ) -> tuple[CaseExecutionAttempt, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM demo_case_attempts
                WHERE batch_id = ?
                ORDER BY queued_at, attempt_number
                """,
                (str(batch_id),),
            ).fetchall()
        return tuple(_attempt_from_row(row) for row in rows)

    def _require_attempt(self, attempt_id: UUID) -> CaseExecutionAttempt:
        attempt = self.get_attempt(attempt_id)
        if attempt is None:
            raise ApplicationPersistenceError(
                f"Attempt '{attempt_id}' was not found"
            )
        return attempt

    def _replace_attempt(
        self,
        current: CaseExecutionAttempt,
        updated: CaseExecutionAttempt,
    ) -> CaseExecutionAttempt:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE demo_case_attempts
                SET status = ?, started_at = ?, completed_at = ?,
                    run_id = ?, selection_id = ?, resolution_id = ?,
                    evaluation_id = ?, recommendation_id = ?,
                    review_package_id = ?, sanitized_error_type = ?,
                    sanitized_error_message = ?, content_hash = ?
                WHERE attempt_id = ? AND status = ? AND content_hash = ?
                """,
                (
                    updated.status.value,
                    _iso(updated.started_at),
                    _iso(updated.completed_at),
                    _uuid(updated.run_id),
                    _uuid(updated.selection_id),
                    _uuid(updated.resolution_id),
                    _uuid(updated.evaluation_id),
                    _uuid(updated.recommendation_id),
                    _uuid(updated.review_package_id),
                    updated.sanitized_error_type,
                    updated.sanitized_error_message,
                    updated.content_hash,
                    str(updated.attempt_id),
                    current.status.value,
                    current.content_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise ApplicationPersistenceError(
                    "Attempt changed concurrently or transition is invalid"
                )
            self._append_event(connection, updated)
        return updated

    @staticmethod
    def _insert_attempt(
        connection: sqlite3.Connection,
        attempt: CaseExecutionAttempt,
    ) -> None:
        connection.execute(
            """
            INSERT INTO demo_case_attempts (
                attempt_id, batch_id, case_id, attempt_number, status,
                execution_mode, queued_at, started_at, completed_at,
                run_id, selection_id, resolution_id, evaluation_id,
                recommendation_id, review_package_id,
                sanitized_error_type, sanitized_error_message, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(attempt.attempt_id),
                str(attempt.batch_id),
                attempt.case_id,
                attempt.attempt_number,
                attempt.status.value,
                attempt.execution_mode.value,
                attempt.queued_at.isoformat(),
                _iso(attempt.started_at),
                _iso(attempt.completed_at),
                _uuid(attempt.run_id),
                _uuid(attempt.selection_id),
                _uuid(attempt.resolution_id),
                _uuid(attempt.evaluation_id),
                _uuid(attempt.recommendation_id),
                _uuid(attempt.review_package_id),
                attempt.sanitized_error_type,
                attempt.sanitized_error_message,
                attempt.content_hash,
            ),
        )

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        attempt: CaseExecutionAttempt,
    ) -> None:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(sequence_number), 0) + 1
            FROM demo_case_attempt_events
            WHERE attempt_id = ?
            """,
            (str(attempt.attempt_id),),
        ).fetchone()
        occurred_at = (
            attempt.completed_at
            or attempt.started_at
            or attempt.queued_at
        )
        connection.execute(
            """
            INSERT INTO demo_case_attempt_events (
                event_id, attempt_id, sequence_number, status,
                occurred_at, snapshot_json, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                str(attempt.attempt_id),
                int(row[0]),
                attempt.status.value,
                occurred_at.isoformat(),
                attempt.model_dump_json(),
                attempt.content_hash,
            ),
        )


def _build_attempt(**values: object) -> CaseExecutionAttempt:
    draft = CaseExecutionAttempt.model_construct(
        **values,
        content_hash="0" * 64,
    )
    return CaseExecutionAttempt(
        **values,
        content_hash=calculate_attempt_content_hash(draft),
    )


def _attempt_from_row(row: sqlite3.Row) -> CaseExecutionAttempt:
    return CaseExecutionAttempt(
        attempt_id=UUID(row["attempt_id"]),
        batch_id=UUID(row["batch_id"]),
        case_id=row["case_id"],
        attempt_number=row["attempt_number"],
        status=ExecutionStatus(row["status"]),
        execution_mode=ExecutionMode(row["execution_mode"]),
        queued_at=datetime.fromisoformat(row["queued_at"]),
        started_at=_datetime(row["started_at"]),
        completed_at=_datetime(row["completed_at"]),
        run_id=_optional_uuid(row["run_id"]),
        selection_id=_optional_uuid(row["selection_id"]),
        resolution_id=_optional_uuid(row["resolution_id"]),
        evaluation_id=_optional_uuid(row["evaluation_id"]),
        recommendation_id=_optional_uuid(row["recommendation_id"]),
        review_package_id=_optional_uuid(row["review_package_id"]),
        sanitized_error_type=row["sanitized_error_type"],
        sanitized_error_message=row["sanitized_error_message"],
        content_hash=row["content_hash"],
    )


def _batch_from_row(row: sqlite3.Row) -> BatchExecution:
    return BatchExecution(
        batch_id=UUID(row["batch_id"]),
        execution_mode=ExecutionMode(row["execution_mode"]),
        status=BatchStatus(row["status"]),
        case_count=row["case_count"],
        completed_count=row["completed_count"],
        failed_count=row["failed_count"],
        created_at=datetime.fromisoformat(row["created_at"]),
        started_at=datetime.fromisoformat(row["started_at"]),
        completed_at=_datetime(row["completed_at"]),
    )


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _uuid(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def _optional_uuid(value: str | None) -> UUID | None:
    return None if value is None else UUID(value)


def _datetime(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)
