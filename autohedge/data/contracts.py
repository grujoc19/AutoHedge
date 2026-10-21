"""Provider-neutral spot prices, fixed-interval OHLCV and news provenance."""

from datetime import timedelta
from typing import Annotated

from pydantic import AwareDatetime, Field, HttpUrl, StrictInt, model_validator

from autohedge.contracts import Amount, Contract, Instrument, PositiveAmount, Text

Interval = Annotated[StrictInt, Field(ge=1, le=2_678_400)]
Count = Annotated[StrictInt, Field(ge=1, le=2000)]
Decimals = Annotated[StrictInt, Field(ge=0, le=30)]
DataPrice = Annotated[PositiveAmount, Field(max_digits=80, decimal_places=40)]
DataAmount = Annotated[Amount, Field(max_digits=80, decimal_places=40)]


class Quote(Contract):
    """Price is quote-currency units per base token; volume units are explicit."""

    instrument: Instrument
    source: Text
    event_time: AwareDatetime
    received_at: AwareDatetime
    price: DataPrice
    base_decimals: Decimals
    quote_decimals: Decimals
    volume_base_24h: DataAmount | None = None
    volume_quote_24h: DataAmount | None = None

    @model_validator(mode="after")
    def received_after_event(self):
        if self.received_at < self.event_time:
            raise ValueError("Quote received before its event time")
        return self


class Candle(Contract):
    """UTC half-open interval; prices in quote units, volume units are explicit."""

    open_time: AwareDatetime
    close_time: AwareDatetime
    open: DataPrice
    high: DataPrice
    low: DataPrice
    close: DataPrice
    volume_base: DataAmount | None = None
    volume_quote: DataAmount | None = None

    @model_validator(mode="after")
    def valid_bar(self):
        if self.volume_base is None and self.volume_quote is None:
            raise ValueError("At least one explicitly denominated volume is required")
        if self.close_time <= self.open_time:
            raise ValueError("Candle close must follow open")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("Candle high is inconsistent")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("Candle low is inconsistent")
        return self


class CandleSeries(Contract):
    instrument: Instrument
    source: Text
    received_at: AwareDatetime
    interval_seconds: Interval
    candles: Annotated[tuple[Candle, ...], Field(min_length=1, max_length=2000)]

    @model_validator(mode="after")
    def regular_closed_bars(self):
        interval = timedelta(seconds=self.interval_seconds)
        previous = None
        for candle in self.candles:
            if candle.close_time - candle.open_time != interval:
                raise ValueError("Candle duration does not match declared interval")
            if previous is not None and candle.open_time != previous.close_time:
                raise ValueError("Candles must be ordered, unique and gap-free")
            if candle.close_time > self.received_at:
                raise ValueError("Only completed candles may be supplied")
            previous = candle
        return self


class NewsArticle(Contract):
    article_id: Text
    source: Text
    url: HttpUrl
    published_at: AwareDatetime
    received_at: AwareDatetime
    title: Text
    summary: Annotated[str, Field(max_length=4000)] = ""

    @model_validator(mode="after")
    def valid_provenance(self):
        if self.url.username or self.url.password:
            raise ValueError("News URLs must not contain credentials")
        if self.published_at > self.received_at:
            raise ValueError("News received before publication")
        return self


class MarketData(Contract):
    quote: Quote
    history: CandleSeries
    news: Annotated[tuple[NewsArticle, ...], Field(max_length=100)] = ()

    @model_validator(mode="after")
    def matching_identity_and_unique_news(self):
        if self.quote.instrument != self.history.instrument:
            raise ValueError("Quote and history instruments do not match")
        identities = [(article.source, article.article_id) for article in self.news]
        urls = [str(article.url) for article in self.news]
        if len(set(identities)) != len(identities) or len(set(urls)) != len(urls):
            raise ValueError("Duplicate news articles")
        return self


class CandleRequest(Contract):
    instrument: Instrument
    interval_seconds: Interval
    count: Count
    as_of: AwareDatetime


class QualityPolicy(Contract):
    """Conservative 24/7 spot-data defaults; not portfolio/trading risk limits."""

    max_quote_age_seconds: Annotated[StrictInt, Field(ge=1, le=3600)] = 60
    max_transport_lag_seconds: Annotated[StrictInt, Field(ge=1, le=3600)] = 30
    min_candles: Count = 30
    max_candle_lag_intervals: Annotated[StrictInt, Field(ge=1, le=10)] = 2
    max_price_deviation_bps: Annotated[StrictInt, Field(ge=0, le=10_000)] = 1000
    max_news_age_seconds: Annotated[StrictInt, Field(ge=1, le=604800)] = 86400
    require_news: bool = False


class QualityAssessment(Contract):
    checked_at: AwareDatetime
    policy: QualityPolicy
    quote_age_seconds: Amount
    candle_count: Count
    last_candle_close: AwareDatetime
    news_count: Annotated[StrictInt, Field(ge=0, le=100)]
    price_deviation_bps: Amount
