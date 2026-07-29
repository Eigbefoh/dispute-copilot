"""Generate the reviewed take-home-v2 predicate manifest deterministically."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
RULEBOOK = ROOT / "data" / "rulebooks" / "take-home-v2"


def found(fact_name: str) -> dict[str, object]:
    return {"operator": "fact_is_found", "fact_name": fact_name}


def all_of(*predicates: dict[str, object]) -> dict[str, object]:
    return {"operator": "all_of", "predicates": predicates}


def any_of(*predicates: dict[str, object]) -> dict[str, object]:
    return {"operator": "any_of", "predicates": predicates}


def equals(
    fact_name: str,
    *,
    expected_value: object | None = None,
    other_fact_name: str | None = None,
    value_match: str = "ANY",
) -> dict[str, object]:
    node: dict[str, object] = {
        "operator": "fact_equals",
        "fact_name": fact_name,
        "value_match": value_match,
    }
    if other_fact_name is not None:
        node["other_fact_name"] = other_fact_name
    else:
        node["expected_value"] = expected_value
    return node


def fact_in(
    fact_name: str,
    values: list[object],
    *,
    value_match: str = "ANY",
) -> dict[str, object]:
    return {
        "operator": "fact_in",
        "fact_name": fact_name,
        "expected_values": values,
        "value_match": value_match,
    }


def numeric_at_least(
    fact_name: str,
    bound: int,
) -> dict[str, object]:
    return {
        "operator": "numeric_at_least",
        "fact_name": fact_name,
        "numeric_bound": bound,
    }


def on_or_before(
    fact_name: str,
    other_fact_name: str,
) -> dict[str, object]:
    return {
        "operator": "date_on_or_before",
        "fact_name": fact_name,
        "other_fact_name": other_fact_name,
    }


CUSTOM: dict[tuple[str, str], dict[str, object]] = {
    ("mastercard-4837-take-home-v2", "MC4837-R1"): all_of(
        equals("avs_result", expected_value="Y"),
        equals("cvv_result", expected_value="M"),
    ),
    ("mastercard-4837-take-home-v2", "MC4837-R2"): fact_in(
        "three_ds_status",
        ["authenticated"],
    ),
    ("mastercard-4837-take-home-v2", "MC4837-R3"): all_of(
        numeric_at_least(
            "merchant_recorded_prior_undisputed_transaction_count",
            2,
        ),
        found("merchant_recorded_prior_transaction_date"),
        equals(
            "merchant_recorded_prior_billing_postcode",
            other_fact_name="case_recorded_billing_postcode",
            value_match="ALL",
        ),
    ),
    ("mastercard-4837-take-home-v2", "MC4837-R4"): all_of(
        equals(
            "carrier_reported_delivery_address",
            other_fact_name="case_recorded_billing_postcode",
        ),
        found("carrier_reported_signature_name"),
    ),
    ("mastercard-4855-take-home-v2", "MC4855-R1"): any_of(
        all_of(
            found("carrier_reported_tracking_number"),
            found("shipping_record_carrier_name"),
            fact_in(
                "carrier_reported_delivery_status",
                ["delivered", "DELIVERED"],
            ),
        ),
        fact_in(
            "service_record_reported_completion_status",
            ["completed", "COMPLETED", "rendered"],
        ),
    ),
    ("mastercard-4855-take-home-v2", "MC4855-R2"): any_of(
        on_or_before(
            "carrier_reported_delivery_date",
            "chargeback_date",
        ),
        on_or_before(
            "service_record_reported_completion_date",
            "chargeback_date",
        ),
    ),
    ("mastercard-4855-take-home-v2", "MC4855-R3"): equals(
        "carrier_reported_delivery_address",
        other_fact_name="case_recorded_billing_postcode",
    ),
    ("mastercard-4859-take-home-v2", "MC4859-R3"): any_of(
        fact_in(
            "booking_record_reported_attendance_status",
            ["no-show", "NO_SHOW", "no_show"],
        ),
        all_of(
            found("booking_record_reported_cancellation_date"),
            numeric_at_least(
                "merchant_stated_cancellation_window_hours",
                0,
            ),
        ),
    ),
    ("mastercard-4859-take-home-v2", "MC4859-R4"): all_of(
        equals(
            "merchant_recorded_charged_fee_amount",
            other_fact_name="merchant_stated_policy_fee_amount",
        ),
        equals(
            "merchant_recorded_charged_fee_currency",
            other_fact_name="merchant_stated_policy_fee_currency",
        ),
    ),
    ("visa-13.1-take-home-v2", "V13.1-R2"): all_of(
        fact_in(
            "service_record_reported_completion_status",
            ["completed", "COMPLETED", "rendered"],
        ),
        on_or_before(
            "service_record_reported_completion_date",
            "merchant_stated_expected_service_date",
        ),
    ),
    ("visa-13.1-take-home-v2", "V13.1-R3"): any_of(
        on_or_before(
            "carrier_reported_delivery_date",
            "chargeback_date",
        ),
        on_or_before(
            "service_record_reported_completion_date",
            "chargeback_date",
        ),
    ),
    ("visa-13.2-take-home-v2", "V13.2-R2"): all_of(
        found("merchant_reported_notification_content"),
        on_or_before(
            "merchant_reported_notification_date",
            "transaction_date",
        ),
        found("merchant_reported_notification_channel"),
    ),
    ("visa-13.2-take-home-v2", "V13.2-R3"): all_of(
        found("merchant_reported_cancellation_search_scope"),
        equals(
            "merchant_reported_cancellation_request_count",
            expected_value=0,
        ),
        found("transaction_date"),
    ),
}


def main() -> None:
    policies = []
    for path in sorted((RULEBOOK / "policies").glob("*.json")):
        policy = json.loads(path.read_text())
        requirements = []
        for requirement in policy["requirements"]:
            key = (policy["policy_id"], requirement["requirement_id"])
            predicate = CUSTOM.get(
                key,
                all_of(
                    *(
                        found(fact_name)
                        for fact_name in requirement["required_facts"]
                    )
                ),
            )
            requirements.append(
                {
                    "requirement_id": requirement["requirement_id"],
                    "predicate": predicate,
                }
            )
        policies.append(
            {
                "policy_id": policy["policy_id"],
                "requirements": requirements,
            }
        )
    payload = {
        "rulebook_version": "take-home-v2",
        "predicate_schema_version": "requirement-predicates/0.1.0",
        "policies": policies,
    }
    destination = RULEBOOK / "evaluation" / "predicates.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    )


if __name__ == "__main__":
    main()
