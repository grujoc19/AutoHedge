"""Contracts for later adapters; all timestamps and units must be normalized."""

from typing import Protocol

from autohedge.contracts import Instrument
from autohedge.data.contracts import CandleRequest, CandleSeries, NewsArticle, Quote


class MarketDataProvider(Protocol):
    def fetch_quote(self, instrument: Instrument) -> Quote: ...

    def fetch_candles(self, request: CandleRequest) -> CandleSeries: ...


class NewsProvider(Protocol):
    def fetch_news(self, instrument: Instrument) -> tuple[NewsArticle, ...]: ...
