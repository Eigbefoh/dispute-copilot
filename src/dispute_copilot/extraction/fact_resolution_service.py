"""Orchestrate deterministic and evidence-backed fact resolution."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from dispute_copilot.domain.case_run import CaseRun
from dispute_copilot.domain.policy_selection import PolicySelection
from dispute_copilot.extraction.bundle_loader import (
    DEFAULT_DATABASE_PATH,
    load_evidence_bundle,
)
from dispute_copilot.extraction.document_converter import DocumentConverter
from dispute_copilot.extraction.errors import FactResolutionServiceError
from dispute_copilot.extraction.evidence_candidate_adapter import (
    to_resolved_facts,
)
from dispute_copilot.extraction.evidence_extractor import (
    EvidenceCandidateExtractor,
)
from dispute_copilot.extraction.fact_resolution_models import (
    CaseMetadataProvenance,
    FactResolutionResult,
    ResolvedFact,
    ResolvedValue,
)
from dispute_copilot.extraction.fact_resolution_repository import (
    SQLiteFactResolutionRepository,
)
from dispute_copilot.extraction.fact_resolution_validation import (
    create_fact_resolution_result,
    selected_policy_fact_names,
)
from dispute_copilot.extraction.fact_router import (
    build_evidence_fact_request,
    build_fact_resolution_plan,
)
from dispute_copilot.extraction.metadata_resolver import (
    get_metadata_binding,
    resolve_case_metadata,
)
from dispute_copilot.extraction.models import (
    EvidenceBundle,
    EvidenceDocument,
)
from dispute_copilot.extraction.providers import VisionProvider
from dispute_copilot.extraction.repository import (
    SQLiteEvidenceDocumentRepository,
)
from dispute_copilot.extraction.service import convert_evidence_bundle
from dispute_copilot.policy.fact_models import (
    FactCatalogue,
    FactDefinition,
)
from dispute_copilot.policy.repository import (
    SQLitePolicyRepository,
    SQLitePolicySelectionRepository,
)
from dispute_copilot.repositories.sqlite import SQLiteCaseRunRepository


def resolve_case_facts(
    *,
    case_run: CaseRun,
    selection: PolicySelection,
    catalogue: FactCatalogue,
    evidence_bundle: EvidenceBundle,
    evidence_documents: tuple[EvidenceDocument, ...],
    extractor: EvidenceCandidateExtractor,
) -> FactResolutionResult:
    """Resolve all frozen-policy facts and admit the complete result."""
    requested_names = selected_policy_fact_names(selection)
    definitions_by_name = {
        definition.fact_name: definition
        for definition in catalogue.facts
    }
    missing_definitions = (
        set(requested_names) - set(definitions_by_name)
    )
    if missing_definitions:
        raise FactResolutionServiceError(
            "selected policy references undefined facts: "
            f"{sorted(missing_definitions)}"
        )
    requested_definitions = tuple(
        definitions_by_name[fact_name]
        for fact_name in requested_names
    )

    plan = build_fact_resolution_plan(requested_definitions)
    metadata_facts = _resolve_metadata_facts(
        case_run=case_run,
        definitions=plan.case_metadata_facts,
    )
    evidence_request = build_evidence_fact_request(plan)
    provider_candidates = (
        extractor.extract_evidence_candidates(
            requested_facts=evidence_request,
            evidence_bundle=evidence_bundle,
            evidence_documents=evidence_documents,
        )
        if evidence_request
        else ()
    )
    evidence_facts = to_resolved_facts(
        provider_candidates=provider_candidates,
        requested_facts=evidence_request,
        evidence_documents=evidence_documents,
    )

    facts_by_name = {
        fact.fact_name: fact
        for fact in (*metadata_facts, *evidence_facts)
    }
    facts = tuple(facts_by_name[fact_name] for fact_name in requested_names)
    resolver_version = (
        extractor.execution_identity.canonical_json()
        if evidence_request
        else "deterministic-metadata/0.1.0"
    )
    return create_fact_resolution_result(
        selection=selection,
        case_run=case_run,
        catalogue=catalogue,
        evidence_bundle=evidence_bundle,
        evidence_documents=evidence_documents,
        facts=facts,
        resolver_version=resolver_version,
    )


def resolve_selection_facts(
    *,
    selection_id: UUID,
    extractor: EvidenceCandidateExtractor,
    vision_provider: VisionProvider,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> FactResolutionResult:
    """Load trusted workflow state, convert evidence, and resolve facts."""
    resolution_repository = SQLiteFactResolutionRepository(database_path)
    existing = resolution_repository.get_for_selection(selection_id)
    if existing is not None:
        return existing

    selection_repository = SQLitePolicySelectionRepository(database_path)
    selection = selection_repository.get(selection_id)
    if selection is None:
        raise FactResolutionServiceError(
            f"Policy selection '{selection_id}' was not found"
        )

    case_run_repository = SQLiteCaseRunRepository(database_path)
    case_run = case_run_repository.get(selection.run_id)
    if case_run is None:
        raise FactResolutionServiceError(
            f"Case run '{selection.run_id}' was not found"
        )

    evidence_bundle = load_evidence_bundle(
        selection_id=selection_id,
        database_path=database_path,
    )
    definitions = SQLitePolicyRepository(
        database_path
    ).list_fact_definitions(selection.rulebook_version)
    if not definitions:
        raise FactResolutionServiceError(
            "No fact catalogue is available for rulebook "
            f"{selection.rulebook_version}"
        )
    catalogue = FactCatalogue(
        rulebook_version=selection.rulebook_version,
        facts=definitions,
    )
    evidence_documents = convert_evidence_bundle(
        bundle=evidence_bundle,
        converter=DocumentConverter(
            vision_provider=vision_provider,
        ),
        repository=SQLiteEvidenceDocumentRepository(database_path),
    )
    result = resolve_case_facts(
        case_run=case_run,
        selection=selection,
        catalogue=catalogue,
        evidence_bundle=evidence_bundle,
        evidence_documents=evidence_documents,
        extractor=extractor,
    )
    resolution_repository.save(result)
    return result


def _resolve_metadata_facts(
    *,
    case_run: CaseRun,
    definitions: tuple[FactDefinition, ...],
) -> tuple[ResolvedFact, ...]:
    raw_facts = resolve_case_metadata(
        run=case_run,
        definitions=definitions,
    )
    resolved: list[ResolvedFact] = []
    for definition, raw_fact in zip(
        definitions,
        raw_facts,
        strict=True,
    ):
        if raw_fact.value is None:
            resolved.append(
                ResolvedFact(
                    fact_name=definition.fact_name,
                    status="NOT_FOUND",
                    resolved_values=(),
                    resolver_type="CASE_METADATA",
                    confidence=None,
                )
            )
            continue

        binding = get_metadata_binding(definition.fact_name)
        if binding is None:
            raise FactResolutionServiceError(
                "No deterministic metadata binding for "
                f"{definition.fact_name}"
            )
        resolved.append(
            ResolvedFact(
                fact_name=definition.fact_name,
                status="FOUND",
                resolved_values=(
                    ResolvedValue(
                        value=raw_fact.value,
                        provenance=(
                            CaseMetadataProvenance(
                                source_type="CASE_METADATA",
                                case_run_id=case_run.run_id,
                                field_path=binding.field_path,
                            ),
                        ),
                    ),
                ),
                resolver_type="CASE_METADATA",
                confidence=None,
            )
        )
    return tuple(resolved)
