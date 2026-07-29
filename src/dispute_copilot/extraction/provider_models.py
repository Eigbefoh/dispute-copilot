"""Immutable provider runtime and reproducibility contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from types import MappingProxyType

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_serializer,
    model_validator,
)


class ModelExecutionIdentity(BaseModel):
    """Non-secret identity required to reproduce one model execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    parameters: Mapping[str, JsonValue]

    @model_validator(mode="after")
    def parameters_are_immutable(self) -> "ModelExecutionIdentity":
        ordered = dict(sorted(self.parameters.items()))
        object.__setattr__(
            self,
            "parameters",
            MappingProxyType(ordered),
        )
        return self

    @field_serializer("parameters")
    def serialize_parameters(
        self,
        value: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return dict(value)

    def canonical_json(self) -> str:
        """Return stable provider-neutral identity for resolver_version."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
