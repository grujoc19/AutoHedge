"""Validated contracts; a research report never authorizes order execution."""

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field
from pydantic import field_validator, model_validator


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
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @field_validator("*", mode="after")
    @classmethod
    def utc_timestamps(cls, value):
        return value.astimezone(timezone.utc) if isinstance(value, datetime) else value


class Instrument(Contract):
    asset_id: Text
    symbol: Text
    venue: Text
    quote_currency: Text


class MarketSnapshot(Contract):
    instrument: Instrument
    observed_at: AwareDatetime
    source: Text
    price: PositiveAmount
    volume: Amount | None = None
    volatility: Amount | None = None


class Position(Contract):
    instrument: Instrument
    quantity: Amount


class PortfolioSnapshot(Contract):
    observed_at: AwareDatetime
    cash: Amount
    equity: Amount
    positions: tuple[Position, ...] = ()

    @model_validator(mode="after")
    def unique_positions(self):
        keys = [(p.instrument.venue, p.instrument.asset_id) for p in self.positions]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate portfolio positions")
        return self


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Signal(Contract):
    """A proposal only: no quantity or execution authority can come from an agent."""

    action: Action
    instrument: Instrument
    created_at: AwareDatetime
    reasoning: Text
    confidence: Annotated[
        float, BeforeValidator(reject_bool), Field(ge=0, le=1, allow_inf_nan=False)
    ]


class Rejection(Contract):
    created_at: AwareDatetime
    reason: Text


class ResearchRequest(Contract):
    task: Annotated[str, Field(min_length=1, max_length=10_000)]
    market: MarketSnapshot | None = None
    portfolio: PortfolioSnapshot | None = None


class Stage(str, Enum):
    THESIS = "thesis"
    QUANT = "quant"
    SENTIMENT = "sentiment"
    RISK = "risk"
    SYNTHESIS = "synthesis"


class StageResult(Contract):
    stage: Stage
    content: Text
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def ordered_times(self):
        if self.completed_at < self.started_at:
            raise ValueError("Stage completed before it started")
        return self


class ResearchReport(Contract):
    run_id: UUID
    request: ResearchRequest
    started_at: AwareDatetime
    completed_at: AwareDatetime
    stages: tuple[StageResult, ...]
    execution_enabled: Literal[False] = False
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def complete_pipeline(self):
        if tuple(stage.stage for stage in self.stages) != tuple(Stage):
            raise ValueError("Research report requires all stages in order")
        if self.completed_at < self.started_at:
            raise ValueError("Research completed before it started")
        previous = self.started_at
        for stage in self.stages:
            if stage.started_at < previous or stage.completed_at > self.completed_at:
                raise ValueError("Stage is outside the ordered research timeline")
            previous = stage.completed_at
        return self
