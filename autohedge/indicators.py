"""Causal Decimal indicators over validated, completed and already received candles."""

from datetime import datetime, timedelta
import hashlib
import json
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BeforeValidator,
    Field,
    StrictInt,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from autohedge.contracts import Amount, Contract, Instrument, Text, reject_bool
from autohedge.data.contracts import CandleSeries, Interval
from autohedge.data.history import HistorySnapshot
from autohedge.errors import DataQualityError

# Independent of the embedding application's precision, rounding and traps.
_CONTEXT = Context(prec=128, rounding=ROUND_HALF_EVEN)
_YEAR_SECONDS = 365 * 24 * 60 * 60
Period = Annotated[StrictInt, Field(ge=2, le=1000)]
Number = Annotated[Decimal, BeforeValidator(reject_bool), Field(allow_inf_nan=False)]
RsiValue = Annotated[Number, Field(ge=0, le=100)]


class IndicatorConfig(Contract):
    rsi_period: Period = 14
    macd_fast: Period = 12
    macd_slow: Period = 26
    macd_signal: Period = 9
    volatility_window: Period = 20

    @model_validator(mode="after")
    def ordered_macd_periods(self):
        if self.macd_fast >= self.macd_slow:
            raise ValueError("MACD fast period must be less than its slow period")
        return self

    @property
    def minimum_candles(self) -> int:
        return max(
            self.rsi_period + 1,
            self.macd_slow + self.macd_signal - 1,
            self.volatility_window + 1,
        )


class IndicatorPoint(Contract):
    close_time: AwareDatetime
    rsi: RsiValue | None = None
    macd: Number | None = None
    macd_signal: Number | None = None
    macd_histogram: Number | None = None
    volatility: Amount | None = None
    annualized_volatility: Amount | None = None

    @model_validator(mode="after")
    def consistent_values(self):
        if (self.macd_signal is None) != (self.macd_histogram is None):
            raise ValueError("MACD signal and histogram must be available together")
        if self.macd_signal is not None:
            with localcontext(_CONTEXT):
                if (
                    self.macd is None
                    or self.macd_histogram != self.macd - self.macd_signal
                ):
                    raise ValueError("Inconsistent MACD histogram")
        if (self.volatility is None) != (self.annualized_volatility is None):
            raise ValueError("Volatility units must be available together")
        return self

    @property
    def ready(self) -> bool:
        return all(
            value is not None for value in (self.rsi, self.macd_signal, self.volatility)
        )


class IndicatorSeries(Contract):
    implementation: Literal["autohedge.indicators.v1"] = "autohedge.indicators.v1"
    history_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    instrument: Instrument
    source: Text
    interval_seconds: Interval
    received_at: AwareDatetime
    as_of: AwareDatetime
    config: IndicatorConfig
    annualization_seconds: Literal[31536000] = _YEAR_SECONDS
    points: Annotated[tuple[IndicatorPoint, ...], Field(min_length=1, max_length=2000)]

    @model_validator(mode="after")
    def causal_and_warmed(self):
        if self.received_at > self.as_of:
            raise ValueError(
                "History was not available at the requested knowledge time"
            )
        previous = None
        with localcontext(_CONTEXT):
            scale = (Decimal(self.annualization_seconds) / self.interval_seconds).sqrt()
            for i, point in enumerate(self.points):
                if point.close_time > self.received_at:
                    raise ValueError("Indicator uses an incomplete or future candle")
                if previous is not None and point.close_time - previous != timedelta(
                    seconds=self.interval_seconds
                ):
                    raise ValueError(
                        "Indicator points must be ordered, unique and gap-free"
                    )
                previous = point.close_time
                availability = (
                    (point.rsi, i >= self.config.rsi_period),
                    (point.macd, i >= self.config.macd_slow - 1),
                    (
                        point.macd_signal,
                        i >= self.config.macd_slow + self.config.macd_signal - 2,
                    ),
                    (point.volatility, i >= self.config.volatility_window),
                )
                if any(
                    (value is not None) != expected for value, expected in availability
                ):
                    raise ValueError(
                        "Indicator availability does not match its warm-up"
                    )
                if (
                    point.volatility is not None
                    and point.annualized_volatility != point.volatility * scale
                ):
                    raise ValueError("Inconsistent annualized volatility")
        return self

    @property
    def latest(self) -> IndicatorPoint:
        return self.points[-1]


def _ema(values: tuple[Decimal, ...], period: int) -> list[Decimal | None]:
    result: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return result
    value = sum(values[:period], Decimal(0)) / period
    result[period - 1] = value
    alpha = Decimal(2) / (period + 1)
    for i in range(period, len(values)):
        value += alpha * (values[i] - value)
        result[i] = value
    return result


def _rsi(values: tuple[Decimal, ...], period: int) -> list[Decimal | None]:
    result: list[Decimal | None] = [None] * len(values)
    if len(values) <= period:
        return result
    changes = tuple(b - a for a, b in zip(values, values[1:]))
    gain = (
        sum((max(change, Decimal(0)) for change in changes[:period]), Decimal(0))
        / period
    )
    loss = (
        sum((max(-change, Decimal(0)) for change in changes[:period]), Decimal(0))
        / period
    )
    for i in range(period, len(values)):
        if i > period:
            change = changes[i - 1]
            gain = (gain * (period - 1) + max(change, Decimal(0))) / period
            loss = (loss * (period - 1) + max(-change, Decimal(0))) / period
        # Flat history is neutral. This form also avoids subtractive cancellation.
        result[i] = (
            Decimal(50) if gain == loss == 0 else Decimal(100) * gain / (gain + loss)
        )
    return result


def _volatility(values: tuple[Decimal, ...], window: int) -> list[Decimal | None]:
    result: list[Decimal | None] = [None] * len(values)
    returns = tuple((b / a).ln() for a, b in zip(values, values[1:]))
    for i in range(window, len(values)):
        sample = returns[i - window : i]
        if min(sample) == max(sample):
            result[i] = Decimal(0)
            continue
        mean = sum(sample, Decimal(0)) / window
        variance = sum(((value - mean) ** 2 for value in sample), Decimal(0)) / (
            window - 1
        )
        result[i] = variance.sqrt()
    return result


def calculate_indicators(
    history: CandleSeries,
    *,
    as_of: datetime,
    config: IndicatorConfig | None = None,
) -> IndicatorSeries:
    """Return an aligned series; insufficient warm-up yields None, never invented data.

    as_of is knowledge time: even historical bars must have been received by it.
    Historical freshness/expected instrument checks still belong to the data gate.
    """
    with localcontext(_CONTEXT):
        try:
            history = CandleSeries.model_validate(history)
        except ValidationError:
            raise DataQualityError("invalid_indicator_history") from None
        try:
            config = IndicatorConfig.model_validate(
                config if config is not None else IndicatorConfig()
            )
        except ValidationError:
            raise DataQualityError("invalid_indicator_config") from None
        try:
            as_of = TypeAdapter(AwareDatetime).validate_python(as_of)
        except ValidationError:
            raise DataQualityError("invalid_clock") from None
        if history.received_at > as_of:
            raise DataQualityError("indicator_history_not_yet_available")
        payload = json.dumps(
            history.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        closes = tuple(candle.close for candle in history.candles)
        rsi = _rsi(closes, config.rsi_period)
        fast, slow = _ema(closes, config.macd_fast), _ema(closes, config.macd_slow)
        macd = [
            a - b if a is not None and b is not None else None
            for a, b in zip(fast, slow)
        ]
        first_macd = config.macd_slow - 1
        signal = [None] * min(first_macd, len(closes)) + _ema(
            tuple(macd[first_macd:]), config.macd_signal
        )
        volatility = _volatility(closes, config.volatility_window)
        scale = (Decimal(_YEAR_SECONDS) / history.interval_seconds).sqrt()
        return IndicatorSeries(
            history_digest=digest,
            instrument=history.instrument,
            source=history.source,
            interval_seconds=history.interval_seconds,
            received_at=history.received_at,
            as_of=as_of,
            config=config,
            points=tuple(
                IndicatorPoint(
                    close_time=candle.close_time,
                    rsi=rsi[i],
                    macd=macd[i],
                    macd_signal=signal[i],
                    macd_histogram=macd[i] - signal[i]
                    if signal[i] is not None
                    else None,
                    volatility=volatility[i],
                    annualized_volatility=volatility[i] * scale
                    if volatility[i] is not None
                    else None,
                )
                for i, candle in enumerate(history.candles)
            ),
        )


def indicators_from_snapshot(
    snapshot: HistorySnapshot, *, as_of: datetime, config: IndicatorConfig | None = None
) -> IndicatorSeries:
    """Validate recorded page provenance before calculating from an acquisition."""
    try:
        snapshot = HistorySnapshot.model_validate(snapshot)
    except ValidationError:
        raise DataQualityError("invalid_history") from None
    return calculate_indicators(snapshot.series, as_of=as_of, config=config)
