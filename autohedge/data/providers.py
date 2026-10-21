"""Contracts for normalized adapters; all timestamps and units must be normalized."""

from typing import Protocol

from autohedge.contracts import Instrument
from autohedge.data.contracts import CandleRequest, CandleSeries, NewsArticle, Quote


class QuoteProvider(Protocol):
    def fetch_quote(self, instrument: Instrument) -> Quote: ...


class CandleProvider(Protocol):
    def fetch_candles(self, request: CandleRequest) -> CandleSeries: ...


class MarketDataProvider(QuoteProvider, CandleProvider, Protocol):
    """Composite interface; quote-only sources need not pretend to supply candles."""


class NewsProvider(Protocol):
    def fetch_news(self, instrument: Instrument) -> tuple[NewsArticle, ...]: ...
