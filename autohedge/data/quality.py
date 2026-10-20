"""Data gates operate on supplied UTC data and an explicit point-in-time clock."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

from pydantic import TypeAdapter, AwareDatetime, ValidationError

from autohedge.contracts import Instrument
from autohedge.data.contracts import (
    CandleRequest,
    CandleSeries,
    MarketData,
    QualityAssessment,
    QualityPolicy,
)
from autohedge.errors import DataQualityError


def parse_market_data(payload: object) -> MarketData:
    """Validate wire-shaped data without reflecting its contents into exceptions."""
    try:
        return MarketData.model_validate(payload)
    except ValidationError:
        raise DataQualityError("invalid_schema") from None


def _utc_clock(now):
    try:
        return TypeAdapter(AwareDatetime).validate_python(now).astimezone(timezone.utc)
    except ValidationError:
        raise DataQualityError("invalid_clock") from None


def validate_candle_response(
    series: CandleSeries, request: CandleRequest
) -> CandleSeries:
    """Adapters must validate identity, cadence and requested coverage, without filling gaps."""
    try:
        series = CandleSeries.model_validate(series)
        request = CandleRequest.model_validate(request)
    except ValidationError:
        raise DataQualityError("invalid_schema") from None
    if series.instrument != request.instrument:
        raise DataQualityError("instrument_mismatch")
    if series.interval_seconds != request.interval_seconds:
        raise DataQualityError("interval_mismatch")
    if len(series.candles) < request.count:
        raise DataQualityError("insufficient_candles")
    if series.candles[-1].close_time > request.as_of:
        raise DataQualityError("future_candles")
    if request.as_of - series.candles[-1].close_time >= timedelta(
        seconds=request.interval_seconds
    ):
        raise DataQualityError("incomplete_window")
    return series


def validate_price_time(
    event_time: datetime,
    *,
    now: datetime,
    policy: QualityPolicy,
    received_at: datetime | None = None,
) -> Decimal:
    """Legacy price-only inputs get freshness checks, never an OHLCV assessment."""
    now = _utc_clock(now)
    if event_time > now or (received_at is not None and received_at > now):
        raise DataQualityError("future_data")
    if now - event_time > timedelta(seconds=policy.max_quote_age_seconds):
        raise DataQualityError("stale_quote")
    if received_at is not None and received_at - event_time > timedelta(
        seconds=policy.max_transport_lag_seconds
    ):
        raise DataQualityError("quote_transport_lag")
    age = now - event_time
    with localcontext() as context:
        context.prec = 128
        return Decimal(age.days * 86400 + age.seconds) + Decimal(
            age.microseconds
        ) / Decimal(1_000_000)


def validate_market_data(
    data: MarketData,
    *,
    expected: Instrument,
    now: datetime,
    policy: QualityPolicy | None = None,
) -> QualityAssessment:
    try:
        policy = QualityPolicy.model_validate(
            policy if policy is not None else QualityPolicy()
        )
    except ValidationError:
        raise DataQualityError("invalid_policy") from None
    data = parse_market_data(data)
    now = _utc_clock(now)
    if data.quote.instrument != expected or data.history.instrument != expected:
        raise DataQualityError("instrument_mismatch")
    quote, history = data.quote, data.history
    if history.received_at > now:
        raise DataQualityError("future_data")
    age_seconds = validate_price_time(
        quote.event_time, received_at=quote.received_at, now=now, policy=policy
    )
    if len(history.candles) < policy.min_candles:
        raise DataQualityError("insufficient_candles")
    last = history.candles[-1]
    if last.close_time > now:
        raise DataQualityError("incomplete_candle")
    if now - last.close_time > timedelta(
        seconds=history.interval_seconds * policy.max_candle_lag_intervals
    ):
        raise DataQualityError("stale_candles")
    with localcontext() as context:
        context.prec = 128
        deviation = abs(quote.price - last.close) / last.close * Decimal(10_000)
    if deviation > policy.max_price_deviation_bps:
        raise DataQualityError("price_deviation")
    if policy.require_news and not data.news:
        raise DataQualityError("missing_news")
    for article in data.news:
        if article.published_at > now or article.received_at > now:
            raise DataQualityError("future_news")
        if now - article.published_at > timedelta(seconds=policy.max_news_age_seconds):
            raise DataQualityError("stale_news")
    return QualityAssessment(
        checked_at=now,
        policy=policy,
        quote_age_seconds=age_seconds,
        candle_count=len(history.candles),
        last_candle_close=last.close_time,
        news_count=len(data.news),
        price_deviation_bps=deviation,
    )
