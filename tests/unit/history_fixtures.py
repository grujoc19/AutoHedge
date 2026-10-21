"""Synthetic OHLCV wire fixtures with fixed UTC clocks; no API calls."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from autohedge.config import Settings
from autohedge.contracts import Instrument
from autohedge.data.coingecko import CoinGeckoCandleProvider
from autohedge.data.contracts import CandleRequest

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures/providers/coingecko-ohlcv.json"
)
NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
MINT = "So11111111111111111111111111111111111111112"
INSTRUMENT = Instrument(
    asset_id=MINT, symbol="SOL", venue="solana", quote_currency="USD"
)
SETTINGS = Settings(
    coingecko_api_key="history-fixture-secret",
    coingecko_base_url="https://history.example/api/v3",
)


def request(count=3, interval=60, as_of=NOW):
    return CandleRequest(
        instrument=INSTRUMENT, interval_seconds=interval, count=count, as_of=as_of
    )


def payload(indices=(1, 2, 3), *, interval=60, end=NOW):
    value = json.loads(FIXTURE.read_text())
    value["data"]["attributes"]["ohlcv_list"] = [
        [
            int((end - timedelta(seconds=i * interval)).timestamp()),
            100,
            102,
            99,
            101,
            1500.25,
        ]
        for i in indices
    ]
    return value


def snapshot(*, received_at=NOW, as_of=NOW, close=101):
    data = payload(end=as_of)
    for row in data["data"]["attributes"]["ohlcv_list"]:
        row[4] = close
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    ) as client:
        return CoinGeckoCandleProvider(
            SETTINGS, client=client, clock=lambda: received_at
        ).fetch_history(request(as_of=as_of))
