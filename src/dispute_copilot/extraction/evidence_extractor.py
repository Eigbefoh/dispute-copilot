"""Provider-neutral evidence-extraction protocol."""

from typing import Protocol

from dispute_copilot.extraction.evidence_extraction_models import (
    ExtractionCandidate,
)
from dispute_copilot.extraction.models import (
    EvidenceBundle,
    EvidenceDocument,
)
from dispute_copilot.extraction.provider_models import (
    ModelExecutionIdentity,
)
from dispute_copilot.policy.fact_models import FactDefinition


class EvidenceCandidateExtractor(Protocol):
    """Minimal provider-neutral boundary used by orchestration."""

    @property
    def execution_identity(self) -> ModelExecutionIdentity:
        """Return non-secret reproducibility metadata."""
        ...

    def extract_evidence_candidates(
        self,
        *,
        requested_facts: tuple[FactDefinition, ...],
        evidence_bundle: EvidenceBundle,
        evidence_documents: tuple[EvidenceDocument, ...],
    ) -> tuple[ExtractionCandidate, ...]:
        """Return one untrusted candidate per requested evidence fact."""
        ...
