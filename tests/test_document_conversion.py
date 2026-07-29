"""Tests for source-preserving evidence document conversion."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError
from pypdf import PdfWriter

from dispute_copilot.extraction.bundle_loader import load_evidence_bundle
from dispute_copilot.extraction.document_converter import DocumentConverter
from dispute_copilot.extraction.errors import (
    EvidenceDocumentAlreadyExistsError,
    OCRProviderRequiredError,
    UnsupportedDocumentMediaTypeError,
    VisionProviderRequiredError,
)
from dispute_copilot.extraction.models import (
    EvidenceBundle,
    EvidenceDocument,
    EvidenceFile,
    ExtractionMethod,
)
from dispute_copilot.extraction.repository import (
    SQLiteEvidenceDocumentRepository,
)
from dispute_copilot.extraction.service import (
    convert_evidence_bundle,
    convert_selection_documents,
)
from dispute_copilot.intake.hashing import calculate_sha256
from dispute_copilot.orchestration.service import create_case_run
from dispute_copilot.policy.import_rulebook import import_rulebook
from dispute_copilot.policy.selection_service import select_policy_for_run
from dispute_copilot.repositories.sqlite import SQLiteCaseRunRepository

PROJECT_ROOT = Path(__file__).parents[1]
CASES_PATH = PROJECT_ROOT / "data" / "cases.json"
SOURCE_EVIDENCE_DIRECTORY = PROJECT_ROOT / "data" / "evidence"
RULEBOOK_DIRECTORY = (
    PROJECT_ROOT / "data" / "rulebooks" / "take-home-v1"
)
CASE_ID = "CB-2025-0006"
EXPECTED_FILENAMES = [
    "CB-2025-0006_product_listing.pdf",
    "CB-2025-0006_delivery_photo.png",
    "CB-2025-0006_returns_policy.pdf",
]
VISION_DESCRIPTION = """Visible text:
HavenHome — Delivery Photo
Carlton Armchair (Sage) — delivered 18 Apr 2025
Photo taken by driver at delivery (front view only)

Visual content:
Front view of a sage-coloured armchair"""


class RecordingVisionProvider:
    """Source-grounded test double at the provider boundary."""

    def __init__(self, provider_version: str = "fixture-vision/1") -> None:
        self._provider_version = provider_version
        self.calls: list[Path] = []

    @property
    def provider_version(self) -> str:
        return self._provider_version

    def describe_image(self, *, file_path: Path) -> str:
        self.calls.append(file_path)
        return VISION_DESCRIPTION


class RecordingOCRProvider:
    """Test double proving non-native PDF pages are explicitly routed."""

    provider_version = "fixture-ocr/1"

    def __init__(self) -> None:
        self.calls: list[tuple[Path, int]] = []

    def extract_page(
        self,
        *,
        file_path: Path,
        page_number: int,
    ) -> str:
        self.calls.append((file_path, page_number))
        return f"Observed scan text on page {page_number}"


def prepare_visa_13_3_bundle(
    tmp_path: Path,
) -> tuple[Path, EvidenceBundle]:
    evidence_directory = tmp_path / "evidence"
    evidence_directory.mkdir()
    for filename in EXPECTED_FILENAMES:
        shutil.copy2(
            SOURCE_EVIDENCE_DIRECTORY / filename,
            evidence_directory / filename,
        )

    database_path = tmp_path / "test.db"
    run = create_case_run(
        CASE_ID,
        CASES_PATH,
        evidence_directory,
    )
    SQLiteCaseRunRepository(database_path).save(run)
    import_rulebook(RULEBOOK_DIRECTORY, database_path)
    selection = select_policy_for_run(
        run.run_id,
        "take-home-v1",
        database_path,
    )
    bundle = load_evidence_bundle(
        selection_id=selection.selection_id,
        database_path=database_path,
    )
    return database_path, bundle


def make_evidence_file(
    path: Path,
    media_type: str = "application/pdf",
) -> EvidenceFile:
    return EvidenceFile(
        file_name=path.name,
        file_path=path,
        media_type=media_type,
        sha256=calculate_sha256(path),
        size_bytes=path.stat().st_size,
    )


def test_complete_cb_2025_0006_bundle_becomes_ordered_documents(
    tmp_path: Path,
) -> None:
    database_path, bundle = prepare_visa_13_3_bundle(tmp_path)
    vision = RecordingVisionProvider()

    documents = convert_selection_documents(
        selection_id=bundle.selection_id,
        database_path=database_path,
        vision_provider=vision,
    )

    assert [document.file_name for document in documents] == (
        EXPECTED_FILENAMES
    )
    assert [document.source_sha256 for document in documents] == [
        evidence_file.sha256
        for evidence_file in bundle.evidence_files
    ]
    assert len(documents) == len(bundle.evidence_files) == 3
    assert all(isinstance(document, EvidenceDocument) for document in documents)
    assert vision.calls == [bundle.evidence_files[1].file_path]

    repository = SQLiteEvidenceDocumentRepository(database_path)
    assert all(
        repository.get(document.document_id) == document
        for document in documents
    )


def test_native_pdf_text_preserves_page_numbers_and_avoids_ocr(
    tmp_path: Path,
) -> None:
    _, bundle = prepare_visa_13_3_bundle(tmp_path)
    ocr = RecordingOCRProvider()
    converter = DocumentConverter(ocr_provider=ocr)

    product_listing = converter.convert(bundle.evidence_files[0])
    returns_policy = converter.convert(bundle.evidence_files[2])

    assert [page.page_number for page in product_listing.pages] == [1]
    assert [page.page_number for page in returns_policy.pages] == [1]
    assert product_listing.pages[0].extraction_method == (
        ExtractionMethod.NATIVE_TEXT
    )
    assert returns_policy.pages[0].extraction_method == (
        ExtractionMethod.NATIVE_TEXT
    )
    assert "Carlton Mid-Century Armchair" in (
        product_listing.pages[0].text
    )
    assert "return it within 14 days" in returns_policy.pages[0].text
    assert ocr.calls == []


def test_png_is_one_vision_page_with_source_dimensions(
    tmp_path: Path,
) -> None:
    _, bundle = prepare_visa_13_3_bundle(tmp_path)
    vision = RecordingVisionProvider()

    document = DocumentConverter(
        vision_provider=vision
    ).convert(bundle.evidence_files[1])

    assert len(document.pages) == 1
    page = document.pages[0]
    assert page.page_number == 1
    assert page.extraction_method == ExtractionMethod.VISION
    assert (page.image_width, page.image_height) == (800, 600)
    assert page.text == VISION_DESCRIPTION
    for visible_caption in (
        "HavenHome — Delivery Photo",
        "Carlton Armchair (Sage) — delivered 18 Apr 2025",
        "Photo taken by driver at delivery (front view only)",
    ):
        assert visible_caption in page.text
    assert "vision/fixture-vision/1" in document.converter_version


def test_conversion_produces_no_policy_fact_or_conclusion_fields(
    tmp_path: Path,
) -> None:
    _, bundle = prepare_visa_13_3_bundle(tmp_path)

    documents = convert_evidence_bundle(
        bundle=bundle,
        converter=DocumentConverter(
            vision_provider=RecordingVisionProvider()
        ),
        repository=SQLiteEvidenceDocumentRepository(
            tmp_path / "documents.db"
        ),
    )

    serialized = " ".join(
        document.model_dump_json() for document in documents
    ).lower()
    for forbidden_conclusion in (
        "product matches listing",
        "delivery proven",
        "item is not defective",
        "visa 13.3 satisfied",
    ):
        assert forbidden_conclusion not in serialized
    for document in documents:
        assert not hasattr(document, "facts")
        assert not hasattr(document, "requirements")
        assert not hasattr(document, "decision")


def test_cache_reuses_same_document_ids_and_provider_output(
    tmp_path: Path,
) -> None:
    database_path, bundle = prepare_visa_13_3_bundle(tmp_path)
    vision = RecordingVisionProvider()

    first = convert_selection_documents(
        selection_id=bundle.selection_id,
        database_path=database_path,
        vision_provider=vision,
    )
    second = convert_selection_documents(
        selection_id=bundle.selection_id,
        database_path=database_path,
        vision_provider=vision,
    )

    assert second == first
    assert [document.document_id for document in second] == [
        document.document_id for document in first
    ]
    assert len(vision.calls) == 1


def test_provider_version_is_part_of_visual_cache_key(
    tmp_path: Path,
) -> None:
    database_path, bundle = prepare_visa_13_3_bundle(tmp_path)

    first = convert_selection_documents(
        selection_id=bundle.selection_id,
        database_path=database_path,
        vision_provider=RecordingVisionProvider("fixture-vision/1"),
    )
    second = convert_selection_documents(
        selection_id=bundle.selection_id,
        database_path=database_path,
        vision_provider=RecordingVisionProvider("fixture-vision/2"),
    )

    assert second[1].document_id != first[1].document_id
    assert second[1].converter_version != first[1].converter_version
    assert second[0] == first[0]
    assert second[2] == first[2]


def test_scanned_pdf_page_is_explicitly_routed_to_ocr(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=200)
    with path.open("wb") as output:
        writer.write(output)
    ocr = RecordingOCRProvider()

    document = DocumentConverter(ocr_provider=ocr).convert(
        make_evidence_file(path)
    )

    assert document.pages[0].extraction_method == ExtractionMethod.OCR
    assert document.pages[0].text == "Observed scan text on page 1"
    assert ocr.calls == [(path, 1)]
    assert "ocr/fixture-ocr/1" in document.converter_version


def test_scanned_pdf_fails_closed_without_ocr_provider(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=200)
    with path.open("wb") as output:
        writer.write(output)

    with pytest.raises(
        OCRProviderRequiredError,
        match="page 1",
    ):
        DocumentConverter().convert(make_evidence_file(path))


def test_png_fails_closed_without_vision_provider(
    tmp_path: Path,
) -> None:
    _, bundle = prepare_visa_13_3_bundle(tmp_path)

    with pytest.raises(
        VisionProviderRequiredError,
        match=EXPECTED_FILENAMES[1],
    ):
        DocumentConverter().convert(bundle.evidence_files[1])


def test_unsupported_media_type_has_no_implicit_route(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence.txt"
    path.write_text("source text")

    with pytest.raises(
        UnsupportedDocumentMediaTypeError,
        match="text/plain",
    ):
        DocumentConverter().convert(
            make_evidence_file(path, "text/plain")
        )


def test_repository_round_trip_survives_new_instance(
    tmp_path: Path,
) -> None:
    database_path, bundle = prepare_visa_13_3_bundle(tmp_path)
    first_repository = SQLiteEvidenceDocumentRepository(database_path)
    document = DocumentConverter().convert(bundle.evidence_files[0])
    first_repository.save(document)

    second_repository = SQLiteEvidenceDocumentRepository(database_path)
    retrieved = second_repository.get(document.document_id)

    assert retrieved == document
    assert retrieved is not None
    assert retrieved.source_sha256 == bundle.evidence_files[0].sha256
    assert retrieved.pages == document.pages


def test_duplicate_source_and_converter_version_is_rejected(
    tmp_path: Path,
) -> None:
    _, bundle = prepare_visa_13_3_bundle(tmp_path)
    repository = SQLiteEvidenceDocumentRepository(
        tmp_path / "documents.db"
    )
    converter = DocumentConverter()
    first = converter.convert(bundle.evidence_files[0])
    duplicate = converter.convert(bundle.evidence_files[0])
    repository.save(first)

    with pytest.raises(EvidenceDocumentAlreadyExistsError):
        repository.save(duplicate)


def test_documents_and_pages_are_immutable(tmp_path: Path) -> None:
    _, bundle = prepare_visa_13_3_bundle(tmp_path)
    document = DocumentConverter().convert(bundle.evidence_files[0])

    with pytest.raises(ValidationError, match="Instance is frozen"):
        document.file_name = "changed.pdf"
    with pytest.raises(ValidationError, match="Instance is frozen"):
        document.pages[0].text = "changed"


def test_each_converted_document_is_stored_in_its_own_row(
    tmp_path: Path,
) -> None:
    database_path, bundle = prepare_visa_13_3_bundle(tmp_path)

    documents = convert_selection_documents(
        selection_id=bundle.selection_id,
        database_path=database_path,
        vision_provider=RecordingVisionProvider(),
    )

    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT document_id, file_name
            FROM evidence_documents
            ORDER BY rowid
            """
        ).fetchall()
    assert rows == [
        (str(document.document_id), document.file_name)
        for document in documents
    ]
