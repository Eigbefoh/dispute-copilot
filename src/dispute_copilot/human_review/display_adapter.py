"""Read-only, integrity-checked evidence presentation adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from PIL import Image
from pypdf import PdfReader

from dispute_copilot.analyst_review.models import (
    DisplayFactValue,
    SupportType,
)
from dispute_copilot.extraction.bundle_loader import DEFAULT_DATABASE_PATH
from dispute_copilot.extraction.fact_resolution_models import (
    CaseMetadataProvenance,
    EvidenceTextProvenance,
    VisualObservationProvenance,
)
from dispute_copilot.human_review.context import (
    HumanReviewContext,
    load_human_review_context,
)
from dispute_copilot.human_review.display_models import (
    EvidenceDisplayLineage,
    EvidenceDisplayTarget,
    EvidenceType,
    HighlightStatus,
    PageGeometry,
    create_evidence_display_target,
)
from dispute_copilot.human_review.errors import (
    InvalidEvidencePageError,
    SourceIntegrityError,
    UnlinkedEvidenceError,
)
from dispute_copilot.intake.hashing import calculate_sha256

VISUAL_OBSERVATION_CAVEAT = (
    "The displayed pixels support a model observation only; they do not "
    "prove exact product identity, defect-free condition or policy "
    "satisfaction."
)
GAP_MESSAGE = (
    "No qualifying value was admitted from the available evidence. This "
    "does not prove that the value or event does not exist."
)
PAGE_ONLY_MESSAGE = (
    "The exact source page is verified, but no grounded region coordinates "
    "were persisted. No precise highlight is shown."
)


@dataclass(frozen=True)
class ResolvedDisplayTarget:
    """A public target plus its server-only, verified source path."""

    target: EvidenceDisplayTarget
    source_path: Path | None


class EvidencePresentationAdapter:
    """Resolve only sources and gaps linked by an immutable review package."""

    def __init__(
        self,
        *,
        database_path: Path = DEFAULT_DATABASE_PATH,
        project_root: Path = Path("."),
    ) -> None:
        self.database_path = database_path
        self.project_root = project_root.resolve()

    def for_source(
        self,
        *,
        review_package_id: UUID,
        source_ref_id: str,
        requirement_id: str | None = None,
    ) -> ResolvedDisplayTarget:
        context = self._context(review_package_id)
        package = context.package
        source = next(
            (
                item
                for item in package.supporting_sources
                if item.source_ref_id == source_ref_id
            ),
            None,
        )
        if source is None:
            raise UnlinkedEvidenceError(
                "The selected source is not linked to this review package"
            )
        owning_requirements = tuple(
            item
            for item in package.requirement_checklist
            if source_ref_id in item.source_refs
        )
        if not owning_requirements:
            raise UnlinkedEvidenceError(
                "The selected source is not linked to a requirement"
            )
        if requirement_id is None:
            requirement = owning_requirements[0]
        else:
            requirement = next(
                (
                    item
                    for item in owning_requirements
                    if item.requirement_id == requirement_id
                ),
                None,
            )
            if requirement is None:
                raise UnlinkedEvidenceError(
                    "The selected source does not belong to the requested "
                    "requirement"
                )
        display_value = _find_display_value(
            context=context,
            fact_name=source.fact_name,
            fact_value_index=source.fact_value_index,
        )
        fact_id = (
            f"{context.inputs.resolution.resolution_id}:{source.fact_name}"
        )
        common = {
            "review_package_id": package.review_package_id,
            "case_id": package.case_summary.case_id,
            "requirement_id": requirement.requirement_id,
            "fact_id": fact_id,
            "gap_id": None,
            "label": source.fact_name,
            "extracted_or_admitted_value": display_value,
            "inspected_source_summaries": (),
            "lineage": _display_lineage(context),
        }
        if source.support_type is SupportType.STRUCTURED_CASE_DATA:
            target = create_evidence_display_target(
                **common,
                source_document_id=None,
                source_file_name=None,
                source_sha256=None,
                page_number=None,
                page_count=None,
                evidence_type=EvidenceType.CASE_METADATA,
                supporting_excerpt=source.supporting_content,
                highlight_status=HighlightStatus.NOT_APPLICABLE,
                coordinate_space=None,
                page_geometry=None,
                bounding_boxes=(),
                analyst_message=(
                    "This value comes from the immutable case snapshot and "
                    "has no evidence-page location."
                ),
            )
            return ResolvedDisplayTarget(target=target, source_path=None)

        artifact, source_path = self._verified_artifact(
            context=context,
            file_name=source.source_document,
        )
        provenance = _find_exact_provenance(
            context=context,
            fact_name=source.fact_name,
            value_index=source.fact_value_index,
            file_name=source.source_document,
            page_number=source.page_number,
            supporting_content=source.supporting_content,
        )
        page_count, geometry = _source_geometry(
            path=source_path,
            media_type=artifact.mime_type,
            page_number=source.page_number,
        )
        if isinstance(provenance, VisualObservationProvenance):
            evidence_type = EvidenceType.VISUAL_MODEL_OBSERVATION
            highlight_status = HighlightStatus.WHOLE_IMAGE_CONTEXT
            message = VISUAL_OBSERVATION_CAVEAT
        elif isinstance(provenance, EvidenceTextProvenance):
            evidence_type = (
                EvidenceType.NATIVE_TEXT_ASSERTION
                if provenance.extraction_method == "NATIVE_TEXT"
                else EvidenceType.OCR_TEXT_ASSERTION
            )
            highlight_status = HighlightStatus.PAGE_ONLY
            message = PAGE_ONLY_MESSAGE
        else:
            raise UnlinkedEvidenceError(
                "The persisted provenance is incompatible with this source"
            )
        target = create_evidence_display_target(
            **common,
            source_document_id=provenance.document_id,
            source_file_name=source.source_document,
            source_sha256=artifact.sha256,
            page_number=source.page_number,
            page_count=page_count,
            evidence_type=evidence_type,
            supporting_excerpt=source.supporting_content,
            highlight_status=highlight_status,
            coordinate_space=None,
            page_geometry=geometry,
            bounding_boxes=(),
            analyst_message=message,
        )
        return ResolvedDisplayTarget(
            target=target,
            source_path=source_path,
        )

    def for_gap(
        self,
        *,
        review_package_id: UUID,
        gap_id: str,
    ) -> ResolvedDisplayTarget:
        context = self._context(review_package_id)
        package = context.package
        gap = next(
            (item for item in package.evidence_gaps if item.gap_id == gap_id),
            None,
        )
        if gap is None:
            raise UnlinkedEvidenceError(
                "The selected evidence gap is not in this review package"
            )
        target = create_evidence_display_target(
            review_package_id=package.review_package_id,
            case_id=package.case_summary.case_id,
            requirement_id=gap.requirement_id,
            fact_id=(
                f"{context.inputs.resolution.resolution_id}:{gap.fact_name}"
            ),
            gap_id=gap.gap_id,
            source_document_id=None,
            source_file_name=None,
            source_sha256=None,
            page_number=None,
            page_count=None,
            label=gap.display_label,
            evidence_type=EvidenceType.EVIDENCE_GAP,
            extracted_or_admitted_value=None,
            supporting_excerpt=None,
            highlight_status=HighlightStatus.NOT_APPLICABLE,
            coordinate_space=None,
            page_geometry=None,
            bounding_boxes=(),
            inspected_source_summaries=tuple(
                f"{artifact.filename} ({artifact.mime_type})"
                for artifact in context.inputs.case_run.evidence_snapshot
            ),
            analyst_message=GAP_MESSAGE,
            lineage=_display_lineage(context),
        )
        return ResolvedDisplayTarget(target=target, source_path=None)

    def for_requirement(
        self,
        *,
        review_package_id: UUID,
        requirement_id: str,
    ) -> ResolvedDisplayTarget:
        context = self._context(review_package_id)
        package = context.package
        requirement = next(
            (
                item
                for item in package.requirement_checklist
                if item.requirement_id == requirement_id
            ),
            None,
        )
        if requirement is None:
            raise UnlinkedEvidenceError(
                "The selected requirement is not in this review package"
            )
        target = create_evidence_display_target(
            review_package_id=package.review_package_id,
            case_id=package.case_summary.case_id,
            requirement_id=requirement.requirement_id,
            fact_id=None,
            gap_id=None,
            source_document_id=None,
            source_file_name=None,
            source_sha256=None,
            page_number=None,
            page_count=None,
            label=requirement.requirement_text,
            evidence_type=EvidenceType.DETERMINISTIC_CONCLUSION,
            extracted_or_admitted_value=None,
            supporting_excerpt=", ".join(
                requirement.evaluation_basis_codes
            ),
            highlight_status=HighlightStatus.NOT_APPLICABLE,
            coordinate_space=None,
            page_geometry=None,
            bounding_boxes=(),
            inspected_source_summaries=(),
            analyst_message=(
                f"{requirement.requirement_id} is "
                f"{requirement.status.value}. Select a supporting source "
                "or unresolved gap for evidence inspection."
            ),
            lineage=_display_lineage(context),
        )
        return ResolvedDisplayTarget(target=target, source_path=None)

    def for_fact(
        self,
        *,
        review_package_id: UUID,
        requirement_id: str,
        fact_name: str,
    ) -> ResolvedDisplayTarget:
        context = self._context(review_package_id)
        requirement = next(
            (
                item
                for item in context.package.requirement_checklist
                if item.requirement_id == requirement_id
            ),
            None,
        )
        if requirement is None:
            raise UnlinkedEvidenceError(
                "The requested requirement is not in the review package"
            )
        for source_ref in requirement.source_refs:
            source = next(
                item
                for item in context.package.supporting_sources
                if item.source_ref_id == source_ref
            )
            if source.fact_name == fact_name:
                return self.for_source(
                    review_package_id=review_package_id,
                    source_ref_id=source_ref,
                    requirement_id=requirement_id,
                )
        gap = next(
            (
                item
                for item in context.package.evidence_gaps
                if item.requirement_id == requirement_id
                and item.fact_name == fact_name
            ),
            None,
        )
        if gap is not None:
            return self.for_gap(
                review_package_id=review_package_id,
                gap_id=gap.gap_id,
            )
        raise UnlinkedEvidenceError(
            "The requested fact has no display target in this requirement"
        )

    def _verified_artifact(
        self,
        *,
        context: HumanReviewContext,
        file_name: str | None,
    ):
        if file_name is None:
            raise UnlinkedEvidenceError(
                "The linked source does not declare a filename"
            )
        artifacts = tuple(
            artifact
            for artifact in context.inputs.case_run.evidence_snapshot
            if artifact.filename == file_name
        )
        if len(artifacts) != 1:
            raise UnlinkedEvidenceError(
                "The source is not uniquely declared by the original case"
            )
        artifact = artifacts[0]
        raw_path = artifact.path
        source_path = (
            raw_path.resolve()
            if raw_path.is_absolute()
            else (self.project_root / raw_path).resolve()
        )
        if source_path.name != artifact.filename or not source_path.is_file():
            raise SourceIntegrityError(
                "The linked evidence source is missing or is not a file"
            )
        if source_path.stat().st_size != artifact.size_bytes:
            raise SourceIntegrityError(
                "The linked evidence source size has changed"
            )
        if calculate_sha256(source_path) != artifact.sha256:
            raise SourceIntegrityError(
                "The linked evidence source failed SHA-256 verification"
            )
        return artifact, source_path

    def _context(self, review_package_id: UUID) -> HumanReviewContext:
        return load_human_review_context(
            review_package_id=review_package_id,
            database_path=self.database_path,
        )


def _display_lineage(
    context: HumanReviewContext,
) -> EvidenceDisplayLineage:
    lineage = context.package.lineage
    return EvidenceDisplayLineage(
        case_run_id=lineage.run_id,
        policy_selection_id=lineage.selection_id,
        fact_resolution_id=lineage.fact_resolution_id,
        evaluation_id=lineage.evaluation_id,
        recommendation_id=lineage.recommendation_id,
        review_package_id=context.package.review_package_id,
    )


def _find_display_value(
    *,
    context: HumanReviewContext,
    fact_name: str,
    fact_value_index: int,
) -> DisplayFactValue:
    for requirement in context.package.requirement_checklist:
        for fact_ref in requirement.supporting_fact_refs:
            if fact_ref.fact_name != fact_name:
                continue
            if fact_value_index not in fact_ref.value_indexes:
                continue
            position = fact_ref.value_indexes.index(fact_value_index)
            return fact_ref.values[position]
    raise UnlinkedEvidenceError(
        "The source value index is absent from the review package"
    )


def _find_exact_provenance(
    *,
    context: HumanReviewContext,
    fact_name: str,
    value_index: int,
    file_name: str | None,
    page_number: int | None,
    supporting_content: str,
):
    facts = {
        fact.fact_name: fact for fact in context.inputs.resolution.facts
    }
    fact = facts.get(fact_name)
    if fact is None or value_index >= len(fact.resolved_values):
        raise UnlinkedEvidenceError(
            "The source does not resolve to an admitted fact value"
        )
    matches = []
    for provenance in fact.resolved_values[value_index].provenance:
        if isinstance(provenance, EvidenceTextProvenance):
            if (
                provenance.file_name == file_name
                and provenance.page_number == page_number
                and provenance.supporting_text == supporting_content
            ):
                matches.append(provenance)
        elif isinstance(provenance, VisualObservationProvenance):
            if (
                provenance.file_name == file_name
                and provenance.page_number == page_number
                and provenance.observation == supporting_content
            ):
                matches.append(provenance)
        elif isinstance(provenance, CaseMetadataProvenance):
            if provenance.field_path == supporting_content:
                matches.append(provenance)
    if len(matches) != 1:
        raise UnlinkedEvidenceError(
            "The source does not map uniquely to admitted provenance"
        )
    return matches[0]


def _source_geometry(
    *,
    path: Path,
    media_type: str,
    page_number: int | None,
) -> tuple[int, PageGeometry]:
    if page_number is None:
        raise InvalidEvidencePageError(
            "The linked source does not declare a page number"
        )
    if media_type == "application/pdf":
        try:
            reader = PdfReader(path)
        except Exception as error:
            raise SourceIntegrityError(
                "The verified PDF could not be opened for display"
            ) from error
        page_count = len(reader.pages)
        if page_number > page_count:
            raise InvalidEvidencePageError(
                "The linked page exceeds the verified PDF page count"
            )
        page = reader.pages[page_number - 1]
        rotation = int(page.get("/Rotate", 0) or 0) % 360
        if rotation not in {0, 90, 180, 270}:
            raise InvalidEvidencePageError(
                "The PDF declares an unsupported page rotation"
            )
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        if rotation in {90, 270}:
            width, height = height, width
        return page_count, PageGeometry(
            width=width,
            height=height,
            rotation_degrees=rotation,
        )
    if media_type in {"image/png", "image/jpeg"}:
        if page_number != 1:
            raise InvalidEvidencePageError(
                "Image evidence supports page_number=1 only"
            )
        try:
            with Image.open(path) as image:
                width, height = image.size
                image.verify()
        except Exception as error:
            raise SourceIntegrityError(
                "The verified image could not be opened for display"
            ) from error
        return 1, PageGeometry(
            width=float(width),
            height=float(height),
            rotation_degrees=0,
        )
    raise SourceIntegrityError(
        f"Unsupported evidence media type for display: {media_type}"
    )
