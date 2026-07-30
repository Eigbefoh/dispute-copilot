"""In-memory evidence rendering with provenance-bounded highlights."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw
import pypdfium2 as pdfium

from dispute_copilot.human_review.display_models import (
    CoordinateSpace,
    EvidenceDisplayTarget,
    HighlightStatus,
)
from dispute_copilot.human_review.errors import (
    InvalidEvidencePageError,
    SourceIntegrityError,
)
from dispute_copilot.intake.hashing import calculate_sha256

RENDERER_VERSION = "evidence-renderer/1.0.0"


@dataclass(frozen=True)
class RenderedEvidence:
    image: Image.Image
    displayed_page: int
    page_count: int
    highlight_drawn: bool
    renderer_version: str = RENDERER_VERSION


class EvidenceRenderer:
    """Render verified PDF pages and images without modifying originals."""

    def __init__(self, *, scale: float = 1.5) -> None:
        if scale <= 0:
            raise ValueError("render scale must be positive")
        self.scale = scale
        self._cache: dict[tuple[str, int, float], Image.Image] = {}

    def render(
        self,
        *,
        target: EvidenceDisplayTarget,
        source_path: Path,
        page_number: int | None = None,
    ) -> RenderedEvidence:
        if target.source_sha256 is None or target.page_count is None:
            raise SourceIntegrityError(
                "This display target has no renderable source"
            )
        if not source_path.is_file():
            raise SourceIntegrityError("The verified display source is missing")
        if calculate_sha256(source_path) != target.source_sha256:
            raise SourceIntegrityError(
                "The display source changed after target validation"
            )
        selected_page = page_number or target.page_number
        if (
            selected_page is None
            or selected_page < 1
            or selected_page > target.page_count
        ):
            raise InvalidEvidencePageError(
                "Requested viewer page is outside the linked document"
            )
        key = (target.source_sha256, selected_page, self.scale)
        cached = self._cache.get(key)
        if cached is None:
            if source_path.suffix.lower() == ".pdf":
                rendered = self._render_pdf(source_path, selected_page)
            else:
                rendered = self._render_image(source_path, selected_page)
            self._cache[key] = rendered.copy()
        else:
            rendered = cached.copy()

        highlight_drawn = False
        if (
            selected_page == target.page_number
            and target.highlight_status
            in {
                HighlightStatus.EXACT_REGION_AVAILABLE,
                HighlightStatus.DERIVED_NATIVE_TEXT_REGION,
            }
        ):
            rendered = self._draw_regions(rendered, target)
            highlight_drawn = bool(target.bounding_boxes)
        return RenderedEvidence(
            image=rendered,
            displayed_page=selected_page,
            page_count=target.page_count,
            highlight_drawn=highlight_drawn,
        )

    def _render_pdf(self, path: Path, page_number: int) -> Image.Image:
        try:
            document = pdfium.PdfDocument(str(path))
            if page_number > len(document):
                raise InvalidEvidencePageError(
                    "Requested page exceeds rendered PDF page count"
                )
            page = document[page_number - 1]
            bitmap = page.render(scale=self.scale)
            image = bitmap.to_pil().convert("RGB")
            bitmap.close()
            page.close()
            document.close()
            return image
        except InvalidEvidencePageError:
            raise
        except Exception as error:
            raise SourceIntegrityError(
                "The verified PDF page could not be rendered"
            ) from error

    @staticmethod
    def _render_image(path: Path, page_number: int) -> Image.Image:
        if page_number != 1:
            raise InvalidEvidencePageError(
                "Image evidence supports page 1 only"
            )
        try:
            with Image.open(path) as image:
                return image.convert("RGB").copy()
        except Exception as error:
            raise SourceIntegrityError(
                "The verified image could not be rendered"
            ) from error

    @staticmethod
    def _draw_regions(
        image: Image.Image,
        target: EvidenceDisplayTarget,
    ) -> Image.Image:
        if target.page_geometry is None or target.coordinate_space is None:
            raise SourceIntegrityError(
                "Precise highlight lacks validated page geometry"
            )
        output = image.copy()
        draw = ImageDraw.Draw(output)
        if target.coordinate_space is CoordinateSpace.NORMALIZED_0_TO_1:
            x_scale = output.width
            y_scale = output.height
        else:
            x_scale = output.width / target.page_geometry.width
            y_scale = output.height / target.page_geometry.height
        for box in target.bounding_boxes:
            coordinates = (
                round(box.x_min * x_scale),
                round(box.y_min * y_scale),
                round(box.x_max * x_scale),
                round(box.y_max * y_scale),
            )
            draw.rectangle(
                coordinates,
                outline=(255, 230, 0),
                width=max(4, round(min(output.size) / 150)),
            )
        return output
