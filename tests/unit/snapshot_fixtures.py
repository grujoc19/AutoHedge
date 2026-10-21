from autohedge.data.contracts import CandleRequest
from autohedge.data.history import CandlePage, HistorySnapshot
from autohedge.snapshots import build_research_snapshot
from market_fixtures import INSTRUMENT, NOW, bundle


def acquisition(data):
    return HistorySnapshot(
        request=CandleRequest(
            instrument=data.history.instrument,
            interval_seconds=60,
            count=len(data.history.candles),
            as_of=NOW,
        ),
        series=data.history,
        pages=(
            CandlePage(
                endpoint="https://history.example/ohlcv/minute",
                before_timestamp=int(NOW.timestamp()) - 1,
                requested_limit=len(data.history.candles),
                series=data.history,
            ),
        ),
    )


def snapshot(*, count=40, news=True, policy=None, pages=False):
    data = bundle(count=count, news=news)
    return build_research_snapshot(
        data,
        expected=INSTRUMENT,
        as_of=NOW,
        policy=policy,
        history_acquisition=acquisition(data) if pages else None,
    )
