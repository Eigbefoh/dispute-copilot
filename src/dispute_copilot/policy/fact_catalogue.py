"""Semantic and referential validation for policy fact catalogues."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from pydantic import ValidationError

from dispute_copilot.policy.fact_models import (
    FactCatalogue,
    FactDefinition,
    FactReview,
    FactReviewRecord,
)
from dispute_copilot.policy.rule_models import PolicyRecord

REVIEW_COLUMNS = (
    "original_fact_name",
    "definition",
    "value_type",
    "source_type",
    "cardinality",
    "policies_using_it",
    "classification",
    "decision",
    "replacement_fact_names",
    "review_notes",
)
PROHIBITED_FACT_NAMES = {
    "proof_of_delivery",
    "product_matches_listing",
    "description_match_evidence",
    "prior_billing_details_match",
    "issuer_miscoding_evidence",
    "should_represent",
}
PROHIBITED_NAME_FRAGMENTS = (
    "proof_of_",
    "_matches_",
    "_satisfied",
    "_eligible",
    "should_",
    "_recommendation",
)
NON_OBSERVABLE_CLASSIFICATIONS = {
    "COMPARISON",
    "POLICY_CONCLUSION",
    "RECOMMENDATION",
}
TEXT_ASSERTION_PREFIXES = (
    "booking_record_reported_",
    "carrier_reported_",
    "merchant_recorded_",
    "merchant_reported_",
    "merchant_stated_",
    "service_record_reported_",
    "shipping_record_",
)


class FactCatalogueValidationError(ValueError):
    """Raised when reviewed fact semantics are not safe to execute."""


def collect_fact_references(
    policies: tuple[PolicyRecord, ...],
) -> dict[str, tuple[str, ...]]:
    """Return deterministic policy/requirement references by fact name."""
    references: defaultdict[str, list[str]] = defaultdict(list)
    for policy in sorted(policies, key=lambda item: item.policy_id):
        for requirement in policy.requirements:
            for fact_name in requirement.required_facts:
                references[fact_name].append(
                    f"{policy.policy_id}:{requirement.requirement_id}"
                )
    return {
        fact_name: tuple(reference_list)
        for fact_name, reference_list in sorted(references.items())
    }


def load_fact_review(path: Path) -> FactReview:
    """Load and strictly validate the completed CSV review artifact."""
    try:
        with path.open(newline="", encoding="utf-8") as review_file:
            reader = csv.DictReader(review_file)
            if tuple(reader.fieldnames or ()) != REVIEW_COLUMNS:
                raise FactCatalogueValidationError(
                    "fact review columns do not match the required schema"
                )
            records = []
            for row in reader:
                records.append(
                    FactReviewRecord(
                        original_fact_name=row["original_fact_name"],
                        definition=row["definition"],
                        value_type=row["value_type"],
                        source_type=row["source_type"],
                        cardinality=row["cardinality"],
                        policies_using_it=tuple(
                            value
                            for value in row["policies_using_it"].split(";")
                            if value
                        ),
                        classification=row["classification"],
                        decision=row["decision"],
                        replacement_fact_names=tuple(
                            value
                            for value in row[
                                "replacement_fact_names"
                            ].split(";")
                            if value
                        ),
                        review_notes=row["review_notes"],
                    )
                )
        return FactReview(records=tuple(records))
    except (OSError, ValidationError) as error:
        raise FactCatalogueValidationError(
            f"invalid fact review at {path}: {error}"
        ) from error


def prohibited_name_reason(fact_name: str) -> str | None:
    """Return the secondary lint failure for a known unsafe name."""
    if fact_name in PROHIBITED_FACT_NAMES:
        return f"prohibited fact name: {fact_name}"
    for fragment in PROHIBITED_NAME_FRAGMENTS:
        if fragment in fact_name:
            return (
                f"prohibited fact-name fragment {fragment!r} "
                f"in {fact_name}"
            )
    return None


def validate_fact_catalogue(
    *,
    policies: tuple[PolicyRecord, ...],
    catalogue: FactCatalogue,
    review: FactReview,
    metadata_resolver_names: set[str],
) -> None:
    """Validate semantic review, routing, and exact policy references."""
    references = collect_fact_references(policies)
    referenced_names = set(references)
    definitions = {
        definition.fact_name: definition
        for definition in catalogue.facts
    }
    definition_names = set(definitions)

    undefined = referenced_names - definition_names
    unused = definition_names - referenced_names
    if undefined or unused:
        raise FactCatalogueValidationError(
            "policy/catalogue fact mismatch; "
            f"undefined={sorted(undefined)}, unused={sorted(unused)}"
        )

    replacement_names: set[str] = set()
    for record in review.records:
        if (
            record.classification in NON_OBSERVABLE_CLASSIFICATIONS
            and record.decision not in {"REMOVE", "SPLIT"}
        ):
            raise FactCatalogueValidationError(
                f"{record.original_fact_name} is "
                f"{record.classification} and cannot be "
                f"{record.decision}"
            )
        if record.decision == "REMOVE":
            if record.replacement_fact_names:
                raise FactCatalogueValidationError(
                    f"removed fact {record.original_fact_name} "
                    "cannot have replacements"
                )
        elif not record.replacement_fact_names:
            raise FactCatalogueValidationError(
                f"{record.original_fact_name} requires replacement names"
            )
        if (
            record.decision == "KEEP"
            and record.replacement_fact_names
            != (record.original_fact_name,)
        ):
            raise FactCatalogueValidationError(
                f"kept fact {record.original_fact_name} must retain its name"
            )
        replacement_names.update(record.replacement_fact_names)

    missing_review_trace = definition_names - replacement_names
    unknown_replacements = replacement_names - definition_names
    if missing_review_trace or unknown_replacements:
        raise FactCatalogueValidationError(
            "review/catalogue mismatch; "
            f"untraced={sorted(missing_review_trace)}, "
            f"unknown={sorted(unknown_replacements)}"
        )

    for definition in catalogue.facts:
        lint_error = prohibited_name_reason(definition.fact_name)
        if lint_error is not None:
            raise FactCatalogueValidationError(lint_error)
        validate_fact_definition_semantics(definition)

    metadata_names = {
        definition.fact_name
        for definition in catalogue.facts
        if definition.source_type == "CASE_METADATA"
    }
    missing_resolvers = metadata_names - metadata_resolver_names
    if missing_resolvers:
        raise FactCatalogueValidationError(
            "metadata resolver mismatch; "
            f"missing={sorted(missing_resolvers)}"
        )


def validate_fact_definition_semantics(
    definition: FactDefinition,
) -> None:
    """Enforce source-aware meaning beyond the secondary name lint."""
    if (
        definition.source_type == "EVIDENCE_VISUAL_OBSERVATION"
        and not definition.fact_name.startswith("image_observed_")
    ):
        raise FactCatalogueValidationError(
            "visual observation must use image_observed_ semantics: "
            f"{definition.fact_name}"
        )
    if (
        definition.source_type == "EVIDENCE_TEXT_ASSERTION"
        and not definition.fact_name.startswith(TEXT_ASSERTION_PREFIXES)
    ):
        raise FactCatalogueValidationError(
            "text assertion must identify its asserting source: "
            f"{definition.fact_name}"
        )
