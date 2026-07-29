"""Deterministically resolve registered facts from an immutable case run."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from dispute_copilot.domain.case_run import CaseRun
from dispute_copilot.extraction.fact_resolution_models import FactValue
from dispute_copilot.policy.fact_models import FactDefinition

MetadataResolver = Callable[[CaseRun], FactValue | None]


@dataclass(frozen=True)
class CaseMetadataBinding:
    """Approved immutable field path and deterministic resolver."""

    field_path: str
    resolver: MetadataResolver


CASE_METADATA_BINDINGS: dict[str, CaseMetadataBinding] = {
    "avs_result": CaseMetadataBinding(
        field_path="case_snapshot.transaction.avs_result",
        resolver=lambda run: run.case_snapshot.transaction.avs_result,
    ),
    "case_recorded_billing_postcode": CaseMetadataBinding(
        field_path=(
            "case_snapshot.transaction.billing_address_postcode"
        ),
        resolver=lambda run: (
            run.case_snapshot.transaction.billing_address_postcode
        ),
    ),
    "case_recorded_reason_code": CaseMetadataBinding(
        field_path="case_snapshot.reason_code",
        resolver=lambda run: run.case_snapshot.reason_code,
    ),
    "case_recorded_reason_code_label": CaseMetadataBinding(
        field_path="case_snapshot.reason_code_label",
        resolver=lambda run: run.case_snapshot.reason_code_label,
    ),
    "case_recorded_shipping_postcode": CaseMetadataBinding(
        field_path=(
            "case_snapshot.transaction.shipping_address_postcode"
        ),
        resolver=lambda run: (
            run.case_snapshot.transaction.shipping_address_postcode
        ),
    ),
    "chargeback_date": CaseMetadataBinding(
        field_path="case_snapshot.chargeback_date",
        resolver=lambda run: run.case_snapshot.chargeback_date,
    ),
    "cvv_result": CaseMetadataBinding(
        field_path="case_snapshot.transaction.cvv_result",
        resolver=lambda run: run.case_snapshot.transaction.cvv_result,
    ),
    "three_ds_status": CaseMetadataBinding(
        field_path="case_snapshot.transaction.three_ds_status",
        resolver=lambda run: (
            run.case_snapshot.transaction.three_ds_status
        ),
    ),
    "transaction_date": CaseMetadataBinding(
        field_path="case_snapshot.transaction.transaction_date",
        resolver=lambda run: (
            run.case_snapshot.transaction.transaction_date
        ),
    ),
}
# Compatibility view for callers that need only resolver functions.
CASE_METADATA_RESOLVERS = {
    fact_name: binding.resolver
    for fact_name, binding in CASE_METADATA_BINDINGS.items()
}


class ResolvedMetadataFact(BaseModel):
    """One value copied directly from the immutable case snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_name: str
    value: FactValue | None


def metadata_resolver_names() -> set[str]:
    """Return the exact registered names supported deterministically."""
    return set(CASE_METADATA_BINDINGS)


def get_metadata_binding(
    fact_name: str,
) -> CaseMetadataBinding | None:
    """Return the approved path and resolver for a metadata fact."""
    return CASE_METADATA_BINDINGS.get(fact_name)


def resolve_case_metadata(
    *,
    run: CaseRun,
    definitions: tuple[FactDefinition, ...],
) -> tuple[ResolvedMetadataFact, ...]:
    """Resolve only definitions explicitly routed to case metadata."""
    resolved = []
    for definition in definitions:
        if definition.source_type != "CASE_METADATA":
            raise ValueError(
                "evidence fact cannot enter metadata resolver: "
                f"{definition.fact_name}"
            )
        binding = get_metadata_binding(definition.fact_name)
        if binding is None:
            raise KeyError(
                f"No metadata resolver for {definition.fact_name}"
            )
        resolved.append(
            ResolvedMetadataFact(
                fact_name=definition.fact_name,
                value=binding.resolver(run),
            )
        )
    return tuple(resolved)
