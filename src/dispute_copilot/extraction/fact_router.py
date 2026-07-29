"""Partition registered facts into deterministic and evidence routes."""

from pydantic import BaseModel, ConfigDict

from dispute_copilot.policy.fact_models import FactDefinition


class FactResolutionPlan(BaseModel):
    """Disjoint, ordered routes for selected policy facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_metadata_facts: tuple[FactDefinition, ...]
    evidence_facts: tuple[FactDefinition, ...]


def build_fact_resolution_plan(
    definitions: tuple[FactDefinition, ...],
) -> FactResolutionPlan:
    """Partition definitions without changing their relative order."""
    return FactResolutionPlan(
        case_metadata_facts=tuple(
            definition
            for definition in definitions
            if definition.source_type == "CASE_METADATA"
        ),
        evidence_facts=tuple(
            definition
            for definition in definitions
            if definition.source_type != "CASE_METADATA"
        ),
    )


def build_evidence_fact_request(
    plan: FactResolutionPlan,
) -> tuple[FactDefinition, ...]:
    """Expose only evidence-routed facts to a provider boundary."""
    return plan.evidence_facts
