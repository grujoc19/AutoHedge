from datetime import timedelta
from decimal import Decimal

from autohedge.data.contracts import Candle, CandleSeries
from market_fixtures import INSTRUMENT, NOW


def history(closes, *, interval=60, end=NOW, received_at=NOW):
    closes = tuple(Decimal(value) for value in closes)
    return CandleSeries(
        instrument=INSTRUMENT,
        source="indicator-fixture",
        interval_seconds=interval,
        received_at=received_at,
        candles=tuple(
            Candle(
                open_time=end - timedelta(seconds=(len(closes) - i) * interval),
                close_time=end - timedelta(seconds=(len(closes) - i - 1) * interval),
                open=close,
                high=close,
                low=close,
                close=close,
                volume_quote="1",
            )
            for i, close in enumerate(closes)
        ),
    )
