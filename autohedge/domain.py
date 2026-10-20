"""Validated contracts; a research report never authorizes order execution."""

from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BeforeValidator, Field, model_validator

from autohedge.contracts import Amount as Amount, PositiveAmount as PositiveAmount
from autohedge.contracts import Contract as Contract, Instrument as Instrument
from autohedge.contracts import Text as Text, reject_bool as reject_bool
from autohedge.data.contracts import MarketData, QualityAssessment


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
    instrument: Instrument | None = None
    market_data: MarketData | None = None

    @model_validator(mode="after")
    def unambiguous_market_input(self):
        if self.market_data is not None:
            if self.instrument is None:
                raise ValueError("An expected instrument is required with market_data")
            if self.market is not None:
                raise ValueError(
                    "Supply market_data or a legacy market snapshot, not both"
                )
        if (
            self.market is not None
            and self.instrument is not None
            and self.market.instrument != self.instrument
        ):
            raise ValueError("Legacy snapshot does not match the expected instrument")
        return self


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
    market_quality: QualityAssessment | None = None

    @model_validator(mode="after")
    def complete_pipeline(self):
        if (self.request.market_data is None) != (self.market_quality is None):
            raise ValueError("Market-data reports require a quality assessment")
        if (
            self.market_quality is not None
            and not self.started_at
            <= self.market_quality.checked_at
            <= self.completed_at
        ):
            raise ValueError("Market quality was checked outside this research cycle")
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
