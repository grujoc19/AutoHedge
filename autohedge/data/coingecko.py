"""Opt-in CoinGecko Onchain token OHLCV in USD, without inferred base volume."""

from datetime import timedelta
from decimal import Decimal

import httpx
from pydantic import ValidationError

from autohedge.config import Settings
from autohedge.data._adapter import read_clock, utc_now
from autohedge.data.contracts import Candle, CandleRequest, CandleSeries
from autohedge.data.history import (
    CandlePage,
    EPOCH,
    HistorySnapshot,
    epoch_seconds,
    window_end,
)
from autohedge.data.quality import validate_candle_response
from autohedge.data.solana import validate_mint
from autohedge.errors import DataQualityError, ProviderError
from autohedge.providers import request_json

_INTERVALS = {
    60: ("minute", "1"),
    300: ("minute", "5"),
    900: ("minute", "15"),
    3600: ("hour", "1"),
    14400: ("hour", "4"),
    43200: ("hour", "12"),
    86400: ("day", "1"),
}
SOURCE = "coingecko.onchain.tokens.v1"


class CoinGeckoCandleProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        clock=utc_now,
        page_size: int = 1000,
        max_pages: int = 10,
    ):
        if type(page_size) is not int or not 2 <= page_size <= 1000:
            raise ValueError("Page size must be between 2 and 1000")
        if type(max_pages) is not int or not 1 <= max_pages <= 20:
            raise ValueError("Page budget must be between 1 and 20")
        self.settings = settings
        self.client = client
        self.clock = clock
        self.page_size = page_size
        self.max_pages = max_pages

    def fetch_candles(self, request: CandleRequest) -> CandleSeries:
        return self.fetch_history(request).series

    def fetch_history(self, request: CandleRequest) -> HistorySnapshot:
        request = CandleRequest.model_validate(request)
        instrument = request.instrument
        if instrument.venue != "solana" or instrument.quote_currency != "USD":
            raise ValueError(
                "Onchain history requires venue solana and quote currency USD"
            )
        validate_mint(instrument.asset_id)
        if request.interval_seconds not in _INTERVALS:
            raise ValueError("Unsupported Onchain OHLCV interval")
        timeframe, aggregate = _INTERVALS[request.interval_seconds]
        end = window_end(request)
        start = end - timedelta(seconds=request.count * request.interval_seconds)
        if start < EPOCH:
            raise ValueError("History window predates Unix epoch")
        headers = self.settings.headers("coingecko")
        received_at = read_clock(self.clock, "coingecko")
        if request.as_of > received_at:
            raise DataQualityError("future_history_request")
        cursor = epoch_seconds(end) - 1
        endpoint = (
            str(self.settings.coingecko_base_url).rstrip("/")
            + f"/onchain/networks/solana/tokens/{instrument.asset_id}/ohlcv/{timeframe}"
        )
        pages, candles = [], {}
        for _ in range(self.max_pages):
            remaining = request.count - sum(start <= key < end for key in candles)
            # Reserve one row for a possibly inclusive boundary on later pages.
            limit = min(self.page_size, remaining + bool(pages))
            payload = request_json(
                "coingecko",
                "GET",
                endpoint,
                settings=self.settings,
                client=self.client,
                headers=headers,
                read_only_retries=True,
                precise=True,
                params={
                    "aggregate": aggregate,
                    "currency": "usd",
                    "before_timestamp": cursor,
                    "limit": limit,
                    "include_empty_intervals": "false",
                    "include_inactive_source": "false",
                },
            )
            previous_receipt = received_at
            received_at = read_clock(self.clock, "coingecko")
            if received_at < previous_receipt:
                raise ProviderError("coingecko", "receipt clock moved backwards")
            page = self._parse_page(
                payload, request, cursor, received_at, endpoint, limit
            )
            next_cursor = epoch_seconds(page.series.candles[0].open_time)
            if next_cursor >= cursor:
                raise DataQualityError("history_pagination_stalled")
            for candle in page.series.candles:
                existing = candles.get(candle.open_time)
                if existing is not None and existing != candle:
                    raise DataQualityError("conflicting_candles")
                candles[candle.open_time] = candle
            pages.append(page)
            cursor = next_cursor
            if page.series.candles[0].open_time <= start:
                break
        else:
            raise DataQualityError("history_page_budget")
        try:
            series = CandleSeries(
                instrument=instrument,
                source=SOURCE,
                received_at=received_at,
                interval_seconds=request.interval_seconds,
                candles=tuple(
                    candles[key] for key in sorted(candles) if start <= key < end
                ),
            )
            validate_candle_response(series, request)
            return HistorySnapshot(request=request, series=series, pages=tuple(pages))
        except ValidationError:
            raise DataQualityError("invalid_history") from None

    def _parse_page(self, payload, request, cursor, received_at, endpoint, limit):
        try:
            if not isinstance(payload, dict):
                raise ValueError
            if payload["meta"]["base"]["address"] != request.instrument.asset_id:
                raise DataQualityError("instrument_mismatch")
            data = payload["data"]
            if not isinstance(data["id"], str) or not data["id"].strip():
                raise ValueError
            if not isinstance(data["type"], str) or not data["type"].strip():
                raise ValueError
            rows = data["attributes"]["ohlcv_list"]
            if not isinstance(rows, list) or len(rows) > limit:
                raise ValueError
            if not rows:
                raise DataQualityError("insufficient_candles")
            bars = []
            for row in rows:
                if (
                    not isinstance(row, list)
                    or len(row) != 6
                    or type(row[0]) is not int
                    or any(type(value) not in (int, Decimal) for value in row[1:])
                ):
                    raise ValueError
                opened = EPOCH + timedelta(seconds=row[0])
                bars.append(
                    Candle(
                        open_time=opened,
                        close_time=opened + timedelta(seconds=request.interval_seconds),
                        open=row[1],
                        high=row[2],
                        low=row[3],
                        close=row[4],
                        volume_quote=row[5],
                    )
                )
            # Provider pages are newest first; normalized contracts are oldest first.
            series = CandleSeries(
                instrument=request.instrument,
                source=SOURCE,
                received_at=received_at,
                interval_seconds=request.interval_seconds,
                candles=tuple(reversed(bars)),
            )
            return CandlePage(
                endpoint=endpoint,
                before_timestamp=cursor,
                requested_limit=limit,
                series=series,
            )
        except (ValueError, TypeError, KeyError, OverflowError):
            raise ProviderError("coingecko", "invalid OHLCV response") from None
