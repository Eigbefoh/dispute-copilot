"""Domain models for dispute cases."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DomainModel(BaseModel):
    """Base configuration shared by domain models."""

    model_config = ConfigDict(extra="forbid")


class Money(DomainModel):
    """A monetary amount in an ISO-style currency."""

    value: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")


class Transaction(DomainModel):
    """The payment transaction associated with a dispute."""

    transaction_id: str = Field(min_length=1)
    merchant_name: str = Field(min_length=1)
    merchant_mcc: str = Field(pattern=r"^\d{4}$")
    transaction_date: datetime
    amount: Money
    card_bin_country: str = Field(
        min_length=2,
        max_length=2,
        pattern=r"^[A-Z]{2}$",
    )
    avs_result: str = Field(min_length=1)
    cvv_result: str = Field(min_length=1)
    three_ds_status: str = Field(min_length=1)
    ip_address: str | None
    device_fingerprint: str | None
    billing_address_postcode: str = Field(min_length=1)
    shipping_address_postcode: str | None


class Case(DomainModel):
    """A payment dispute case and its supplied evidence references."""

    case_id: str = Field(min_length=1)
    scheme: Literal["visa", "mastercard"]
    reason_code: str = Field(min_length=1)
    reason_code_label: str = Field(min_length=1)
    chargeback_date: date
    chargeback_amount: Money
    transaction: Transaction
    issuer_narrative: str = Field(min_length=1)
    merchant_evidence_documents: list[str]

