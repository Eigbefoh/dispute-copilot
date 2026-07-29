"""Tests for SQLite rulebook source provenance."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from dispute_copilot.domain.rulebook import RulebookSource
from dispute_copilot.repositories.interfaces import (
    RulebookSourceAlreadyExistsError,
)
from dispute_copilot.repositories.sqlite import (
    SQLiteRulebookSourceRepository,
)


def make_source() -> RulebookSource:
    return RulebookSource(
        rulebook_version="take-home-v1",
        document_name="reason-codes.pdf",
        document_path=Path(
            "data/rulebooks/take-home-v1/source/reason-codes.pdf"
        ),
        source_sha256=(
            "32bcc199ee56ecc31b63f5d033d4a36b"
            "08dd01a3f6f365accfba85d51f4f18f8"
        ),
        transcript_path=Path(
            "data/rulebooks/take-home-v1/transcript/"
            "canonical-transcript.json"
        ),
        created_at=datetime.now(timezone.utc),
    )


def test_rulebook_source_round_trip_preserves_record(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    source = make_source()
    SQLiteRulebookSourceRepository(database_path).save(source)

    retrieved = SQLiteRulebookSourceRepository(database_path).get(
        source.rulebook_version
    )

    assert retrieved == source


def test_rulebook_source_paths_are_stored_as_relative(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    source = make_source()
    SQLiteRulebookSourceRepository(database_path).save(source)

    with sqlite3.connect(database_path) as connection:
        paths = connection.execute(
            """
            SELECT document_path, transcript_path
            FROM rulebook_sources
            WHERE rulebook_version = ?
            """,
            (source.rulebook_version,),
        ).fetchone()

    assert paths is not None
    assert not Path(paths[0]).is_absolute()
    assert not Path(paths[1]).is_absolute()


def test_duplicate_rulebook_version_is_rejected(
    tmp_path: Path,
) -> None:
    repository = SQLiteRulebookSourceRepository(tmp_path / "test.db")
    source = make_source()
    repository.save(source)

    with pytest.raises(RulebookSourceAlreadyExistsError):
        repository.save(source)


def test_absolute_rulebook_paths_are_rejected() -> None:
    source_data = make_source().model_dump()
    source_data["document_path"] = Path("/machine-specific/source.pdf")

    with pytest.raises(
        ValidationError,
        match="rulebook paths must be relative",
    ):
        RulebookSource.model_validate(source_data)

