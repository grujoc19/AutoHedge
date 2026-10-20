"""Shared immutable value types for research and provider contracts."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator


def reject_bool(value):
    if isinstance(value, bool):
        raise ValueError("Boolean values are not numeric quantities")
    return value


PositiveAmount = Annotated[
    Decimal, BeforeValidator(reject_bool), Field(gt=0, allow_inf_nan=False)
]
Amount = Annotated[
    Decimal, BeforeValidator(reject_bool), Field(ge=0, allow_inf_nan=False)
]
Text = Annotated[str, Field(min_length=1, max_length=20_000)]


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        revalidate_instances="always",
    )

    @field_validator("*", mode="after")
    @classmethod
    def utc_timestamps(cls, value):
        return value.astimezone(timezone.utc) if isinstance(value, datetime) else value


class Instrument(Contract):
    asset_id: Text
    symbol: Text
    venue: Text
    quote_currency: Text
