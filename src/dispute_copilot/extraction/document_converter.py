"""Convert verified evidence into immutable source-preserving documents."""

from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import version
from uuid import uuid4

from PIL import Image
from pypdf import PdfReader

from dispute_copilot.extraction.errors import (
    EmptyConversionOutputError,
    OCRProviderRequiredError,
    UnsupportedDocumentMediaTypeError,
    VisionProviderRequiredError,
)
from dispute_copilot.extraction.models import (
    EvidenceDocument,
    EvidenceFile,
    EvidencePage,
    ExtractionMethod,
)
from dispute_copilot.extraction.providers import (
    OCRProvider,
    VisionProvider,
)

CONVERTER_VERSION = "0.1.0"


class DocumentConverter:
    """Route each supported file through an explicit conversion method."""

    def __init__(
        self,
        *,
        ocr_provider: OCRProvider | None = None,
        vision_provider: VisionProvider | None = None,
    ) -> None:
        self.ocr_provider = ocr_provider
        self.vision_provider = vision_provider

    def converter_version_for(
        self,
        evidence_file: EvidenceFile,
    ) -> str:
        """Return the complete cache identity for this conversion route."""
        components = [f"document-converter/{CONVERTER_VERSION}"]
        if evidence_file.media_type == "application/pdf":
            components.append(f"pypdf/{version('pypdf')}")
            if self.ocr_provider is not None:
                components.append(
                    f"ocr/{self.ocr_provider.provider_version}"
                )
        elif evidence_file.media_type == "image/png":
            components.append(f"pillow/{version('Pillow')}")
            if self.vision_provider is not None:
                components.append(
                    f"vision/{self.vision_provider.provider_version}"
                )
        else:
            raise UnsupportedDocumentMediaTypeError(
                evidence_file.media_type
            )
        return ";".join(components)

    def convert(self, evidence_file: EvidenceFile) -> EvidenceDocument:
        """Convert one already-verified evidence file."""
        converter_version = self.converter_version_for(evidence_file)
        if evidence_file.media_type == "application/pdf":
            pages = self._convert_pdf(evidence_file)
        elif evidence_file.media_type == "image/png":
            pages = self._convert_png(evidence_file)
        else:
            raise UnsupportedDocumentMediaTypeError(
                evidence_file.media_type
            )

        return EvidenceDocument(
            document_id=uuid4(),
            file_name=evidence_file.file_name,
            source_sha256=evidence_file.sha256,
            media_type=evidence_file.media_type,
            pages=pages,
            converter_version=converter_version,
            created_at=datetime.now(timezone.utc),
        )

    def _convert_pdf(
        self,
        evidence_file: EvidenceFile,
    ) -> tuple[EvidencePage, ...]:
        reader = PdfReader(evidence_file.file_path)
        pages: list[EvidencePage] = []
        for page_number, source_page in enumerate(
            reader.pages,
            start=1,
        ):
            native_text = (source_page.extract_text() or "").strip()
            if native_text:
                pages.append(
                    EvidencePage(
                        page_number=page_number,
                        text=native_text,
                        extraction_method=ExtractionMethod.NATIVE_TEXT,
                    )
                )
                continue

            if self.ocr_provider is None:
                raise OCRProviderRequiredError(
                    evidence_file.file_name,
                    page_number,
                )
            ocr_text = self.ocr_provider.extract_page(
                file_path=evidence_file.file_path,
                page_number=page_number,
            ).strip()
            if not ocr_text:
                raise EmptyConversionOutputError(
                    f"OCR returned no text for "
                    f"{evidence_file.file_name}, page {page_number}"
                )
            pages.append(
                EvidencePage(
                    page_number=page_number,
                    text=ocr_text,
                    extraction_method=ExtractionMethod.OCR,
                )
            )

        if not pages:
            raise EmptyConversionOutputError(
                f"PDF contains no pages: {evidence_file.file_name}"
            )
        return tuple(pages)

    def _convert_png(
        self,
        evidence_file: EvidenceFile,
    ) -> tuple[EvidencePage, ...]:
        if self.vision_provider is None:
            raise VisionProviderRequiredError(
                evidence_file.file_name
            )

        with Image.open(evidence_file.file_path) as image:
            image.load()
            width, height = image.size

        description = self.vision_provider.describe_image(
            file_path=evidence_file.file_path
        ).strip()
        if not description:
            raise EmptyConversionOutputError(
                f"Vision returned no description for "
                f"{evidence_file.file_name}"
            )

        return (
            EvidencePage(
                page_number=1,
                text=description,
                extraction_method=ExtractionMethod.VISION,
                image_width=width,
                image_height=height,
            ),
        )
