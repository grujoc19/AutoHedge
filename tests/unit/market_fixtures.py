"""Deterministic provider-shaped fixtures, with no live network or credentials."""

from datetime import datetime, timedelta, timezone

from autohedge.contracts import Instrument
from autohedge.data.contracts import (
    Candle,
    CandleSeries,
    MarketData,
    NewsArticle,
    Quote,
)

NOW = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
INSTRUMENT = Instrument(
    asset_id="So11111111111111111111111111111111111111112",
    symbol="SOL",
    venue="solana",
    quote_currency="USDC",
)


def bundle(*, count=30, end=NOW, quote_price="100", news=False):
    candles = tuple(
        Candle(
            open_time=end - timedelta(minutes=count - i),
            close_time=end - timedelta(minutes=count - i - 1),
            open="100",
            high="101",
            low="99",
            close="100",
            volume_base="2",
        )
        for i in range(count)
    )
    return MarketData(
        quote=Quote(
            instrument=INSTRUMENT,
            source="quote-fixture",
            event_time=NOW - timedelta(seconds=1),
            received_at=NOW,
            price=quote_price,
            base_decimals=9,
            quote_decimals=6,
        ),
        history=CandleSeries(
            instrument=INSTRUMENT,
            source="history-fixture",
            received_at=NOW,
            interval_seconds=60,
            candles=candles,
        ),
        news=(
            NewsArticle(
                article_id="story-1",
                source="news-fixture",
                url="https://news.example/story/1",
                published_at=NOW - timedelta(minutes=5),
                received_at=NOW,
                title="Fixture news",
            ),
        )
        if news
        else (),
    )
