"""SQLite persistence for validated executable policies."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from dispute_copilot.domain.policy_selection import PolicySelection
from dispute_copilot.policy.fact_models import (
    FactCatalogue,
    FactDefinition,
)
from dispute_copilot.policy.rule_models import PolicyRecord


class DuplicatePolicyError(Exception):
    """Raised when an identical versioned policy key already exists."""


class ConflictingPolicyKeyError(Exception):
    """Raised when a policy ID or versioned key conflicts."""


class DuplicateFactDefinitionError(Exception):
    """Raised when a versioned fact definition already exists."""


class ConflictingFactDefinitionError(Exception):
    """Raised when a versioned fact name has different semantics."""


class DuplicatePolicySelectionError(Exception):
    """Raised when a run already has a policy selection."""

    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        super().__init__(
            f"Policy selection already exists for run '{run_id}'"
        )


@dataclass(frozen=True)
class ImportedPolicy:
    """A persisted policy together with its verified integrity hashes."""

    policy: PolicyRecord
    policy_sha256: str
    transcript_sha256: str
    fact_catalogue_sha256: str | None = None


class SQLitePolicyRepository:
    """Append-only storage for versioned executable policies."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS policies (
                    rulebook_version TEXT NOT NULL,
                    scheme TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    policy_id TEXT NOT NULL UNIQUE,
                    policy_sha256 TEXT NOT NULL,
                    transcript_sha256 TEXT NOT NULL,
                    fact_catalogue_sha256 TEXT,
                    policy_json TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    PRIMARY KEY (
                        rulebook_version,
                        scheme,
                        reason_code
                    )
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(policies)"
                ).fetchall()
            }
            if "fact_catalogue_sha256" not in columns:
                connection.execute(
                    "ALTER TABLE policies "
                    "ADD COLUMN fact_catalogue_sha256 TEXT"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS fact_definitions (
                    rulebook_version TEXT NOT NULL,
                    fact_name TEXT NOT NULL,
                    definition_sha256 TEXT NOT NULL,
                    definition_json TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    PRIMARY KEY (rulebook_version, fact_name)
                )
                """
            )

    def save_many(
        self,
        policies: tuple[PolicyRecord, ...],
        policy_hashes: dict[str, str],
        transcript_sha256: str,
        imported_at: datetime,
        fact_catalogue: FactCatalogue | None = None,
        fact_catalogue_sha256: str | None = None,
    ) -> None:
        """Insert a validated policy batch in one atomic transaction."""
        self._validate_batch_keys(policies)
        if (fact_catalogue is None) != (
            fact_catalogue_sha256 is None
        ):
            raise ValueError(
                "fact catalogue and hash must be provided together"
            )
        if fact_catalogue is not None and any(
            policy.rulebook_version != fact_catalogue.rulebook_version
            for policy in policies
        ):
            raise ValueError(
                "policy and fact-catalogue versions must match"
            )

        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                for policy in policies:
                    self._reject_existing_key(connection, policy)
                    connection.execute(
                        """
                        INSERT INTO policies (
                            rulebook_version,
                            scheme,
                            reason_code,
                            policy_id,
                            policy_sha256,
                            transcript_sha256,
                            fact_catalogue_sha256,
                            policy_json,
                            imported_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            policy.rulebook_version,
                            policy.scheme,
                            policy.reason_code,
                            policy.policy_id,
                            policy_hashes[policy.policy_id],
                            transcript_sha256,
                            fact_catalogue_sha256,
                            policy.model_dump_json(),
                            imported_at.isoformat(),
                        ),
                    )
                if fact_catalogue is not None:
                    self._save_fact_definitions(
                        connection=connection,
                        catalogue=fact_catalogue,
                        imported_at=imported_at,
                    )
        except sqlite3.IntegrityError as error:
            raise ConflictingPolicyKeyError(
                "policy key conflict during import"
            ) from error

    def get(
        self,
        rulebook_version: str,
        scheme: str,
        reason_code: str,
    ) -> PolicyRecord | None:
        """Retrieve a validated policy by its versioned key."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT policy_json
                FROM policies
                WHERE rulebook_version = ?
                  AND scheme = ?
                  AND reason_code = ?
                """,
                (rulebook_version, scheme, reason_code),
            ).fetchone()

        if row is None:
            return None
        return PolicyRecord.model_validate_json(row[0])

    def get_imported(
        self,
        rulebook_version: str,
        scheme: str,
        reason_code: str,
    ) -> ImportedPolicy | None:
        """Retrieve one exact policy and its imported integrity metadata."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    policy_json,
                    policy_sha256,
                    transcript_sha256,
                    fact_catalogue_sha256
                FROM policies
                WHERE rulebook_version = ?
                  AND scheme = ?
                  AND reason_code = ?
                """,
                (rulebook_version, scheme, reason_code),
            ).fetchone()

        if row is None:
            return None
        return ImportedPolicy(
            policy=PolicyRecord.model_validate_json(row[0]),
            policy_sha256=row[1],
            transcript_sha256=row[2],
            fact_catalogue_sha256=row[3],
        )

    def get_fact_definition(
        self,
        rulebook_version: str,
        fact_name: str,
    ) -> FactDefinition | None:
        """Retrieve one exact immutable fact definition."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT definition_json
                FROM fact_definitions
                WHERE rulebook_version = ?
                  AND fact_name = ?
                """,
                (rulebook_version, fact_name),
            ).fetchone()
        if row is None:
            return None
        return FactDefinition.model_validate_json(row[0])

    def list_fact_definitions(
        self,
        rulebook_version: str,
    ) -> tuple[FactDefinition, ...]:
        """Retrieve a version's complete ordered fact catalogue."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT definition_json
                FROM fact_definitions
                WHERE rulebook_version = ?
                ORDER BY fact_name
                """,
                (rulebook_version,),
            ).fetchall()
        return tuple(
            FactDefinition.model_validate_json(row[0])
            for row in rows
        )

    @staticmethod
    def _validate_batch_keys(
        policies: tuple[PolicyRecord, ...],
    ) -> None:
        seen_keys: set[tuple[str, str, str]] = set()
        seen_ids: set[str] = set()
        for policy in policies:
            key = (
                policy.rulebook_version,
                policy.scheme,
                policy.reason_code,
            )
            if key in seen_keys:
                raise DuplicatePolicyError(
                    f"duplicate policy key in import batch: {key}"
                )
            if policy.policy_id in seen_ids:
                raise ConflictingPolicyKeyError(
                    "duplicate policy_id in import batch: "
                    f"{policy.policy_id}"
                )
            seen_keys.add(key)
            seen_ids.add(policy.policy_id)

    @staticmethod
    def _reject_existing_key(
        connection: sqlite3.Connection,
        policy: PolicyRecord,
    ) -> None:
        key_row = connection.execute(
            """
            SELECT policy_id
            FROM policies
            WHERE rulebook_version = ?
              AND scheme = ?
              AND reason_code = ?
            """,
            (
                policy.rulebook_version,
                policy.scheme,
                policy.reason_code,
            ),
        ).fetchone()
        if key_row is not None:
            if key_row[0] == policy.policy_id:
                raise DuplicatePolicyError(
                    "policy already imported: "
                    f"{policy.rulebook_version}/"
                    f"{policy.scheme}/{policy.reason_code}"
                )
            raise ConflictingPolicyKeyError(
                "versioned policy key already belongs to "
                f"{key_row[0]}"
            )

        id_row = connection.execute(
            """
            SELECT rulebook_version, scheme, reason_code
            FROM policies
            WHERE policy_id = ?
            """,
            (policy.policy_id,),
        ).fetchone()
        if id_row is not None:
            raise ConflictingPolicyKeyError(
                f"policy_id {policy.policy_id} already belongs to "
                f"{id_row[0]}/{id_row[1]}/{id_row[2]}"
            )

    @staticmethod
    def _save_fact_definitions(
        *,
        connection: sqlite3.Connection,
        catalogue: FactCatalogue,
        imported_at: datetime,
    ) -> None:
        for definition in catalogue.facts:
            definition_json = definition.model_dump_json()
            definition_sha256 = sha256(
                definition_json.encode("utf-8")
            ).hexdigest()
            existing = connection.execute(
                """
                SELECT definition_sha256
                FROM fact_definitions
                WHERE rulebook_version = ?
                  AND fact_name = ?
                """,
                (
                    catalogue.rulebook_version,
                    definition.fact_name,
                ),
            ).fetchone()
            if existing is not None:
                if existing[0] == definition_sha256:
                    raise DuplicateFactDefinitionError(
                        "fact definition already imported: "
                        f"{catalogue.rulebook_version}/"
                        f"{definition.fact_name}"
                    )
                raise ConflictingFactDefinitionError(
                    "fact definition conflict: "
                    f"{catalogue.rulebook_version}/"
                    f"{definition.fact_name}"
                )
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
                    catalogue.rulebook_version,
                    definition.fact_name,
                    definition_sha256,
                    definition_json,
                    imported_at.isoformat(),
                ),
            )


class SQLitePolicySelectionRepository:
    """Append-only storage for immutable policy selections."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS policy_selections (
                    selection_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    selected_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    rulebook_version TEXT NOT NULL,
                    scheme TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    policy_sha256 TEXT NOT NULL,
                    policy_snapshot_json TEXT NOT NULL
                )
                """
            )

    def save(self, selection: PolicySelection) -> None:
        """Insert one selection without updating an existing run."""
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO policy_selections (
                        selection_id,
                        run_id,
                        selected_at,
                        status,
                        rulebook_version,
                        scheme,
                        reason_code,
                        policy_id,
                        policy_sha256,
                        policy_snapshot_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(selection.selection_id),
                        str(selection.run_id),
                        selection.selected_at.isoformat(),
                        selection.status.value,
                        selection.rulebook_version,
                        selection.scheme,
                        selection.reason_code,
                        selection.policy_id,
                        selection.policy_sha256,
                        selection.policy_snapshot.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise DuplicatePolicySelectionError(
                selection.run_id
            ) from error

    def get(self, selection_id: UUID) -> PolicySelection | None:
        """Retrieve a selection by its own ID."""
        return self._get_by("selection_id", str(selection_id))

    def get_for_run(self, run_id: UUID) -> PolicySelection | None:
        """Retrieve the single selection associated with a run."""
        return self._get_by("run_id", str(run_id))

    def _get_by(
        self,
        column_name: str,
        value: str,
    ) -> PolicySelection | None:
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT
                    selection_id,
                    run_id,
                    selected_at,
                    status,
                    rulebook_version,
                    scheme,
                    reason_code,
                    policy_id,
                    policy_sha256,
                    policy_snapshot_json
                FROM policy_selections
                WHERE {column_name} = ?
                """,
                (value,),
            ).fetchone()

        if row is None:
            return None

        return PolicySelection(
            selection_id=row[0],
            run_id=row[1],
            selected_at=row[2],
            status=row[3],
            rulebook_version=row[4],
            scheme=row[5],
            reason_code=row[6],
            policy_id=row[7],
            policy_sha256=row[8],
            policy_snapshot=PolicyRecord.model_validate_json(row[9]),
        )
