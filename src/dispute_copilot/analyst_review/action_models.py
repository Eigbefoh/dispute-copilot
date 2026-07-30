"""Immutable deterministic suggestions shown beside analyst recommendations."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SuggestedActionType(StrEnum):
    NO_ADDITIONAL_ACTION = "NO_ADDITIONAL_ACTION"
    REQUEST_SPECIFIC_EVIDENCE = "REQUEST_SPECIFIC_EVIDENCE"
    VERIFY_CONFLICT = "VERIFY_CONFLICT"
    MANUAL_POLICY_REVIEW = "MANUAL_POLICY_REVIEW"


class SuggestedAnalystAction(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )

    action_id: UUID
    review_package_id: UUID
    action: SuggestedActionType
    requested_items: tuple[str, ...]
    causal_requirement_ids: tuple[str, ...]
    generated_at: datetime
    rules_version: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def action_shape_is_valid(self) -> "SuggestedAnalystAction":
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        if len(self.requested_items) != len(set(self.requested_items)):
            raise ValueError("requested evidence items must be unique")
        if len(self.causal_requirement_ids) != len(
            set(self.causal_requirement_ids)
        ):
            raise ValueError("causal requirement IDs must be unique")
        requires_items = self.action in {
            SuggestedActionType.REQUEST_SPECIFIC_EVIDENCE,
            SuggestedActionType.VERIFY_CONFLICT,
        }
        if requires_items != bool(self.requested_items):
            raise ValueError(
                "requested items are required exactly for evidence or "
                "conflict actions"
            )
        if self.content_hash != calculate_suggested_action_hash(self):
            raise ValueError("content_hash does not match suggested action")
        return self


def calculate_suggested_action_hash(
    action: SuggestedAnalystAction,
) -> str:
    payload = action.model_dump(
        mode="json",
        exclude={"action_id", "generated_at", "content_hash"},
    )
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
