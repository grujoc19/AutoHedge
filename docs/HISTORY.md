# Solana OHLCV collection and immutable replay (phase 2.3)

The optional CoinGecko adapter implements CandleProvider using the existing
Settings, HTTP transport, Decimal quantities and quality contracts. It does not
start a scheduler, call an agent or execute orders. No extra SDK dependency is
needed. This implementation is tested with synthetic HTTP fixtures. Authenticated
live access, account entitlements, actual history coverage and licensing have
not been verified in this environment.

## Source selection

| Candidate | Evidence and fit | Decision |
| --- | --- | --- |
| Existing Jupiter Search/Price | Token metadata and USD price observations; these endpoints do not expose historical OHLCV | Retain for discovery/quotes; do not manufacture history from them |
| Existing Yahoo/Massive adapters | Reusable transport and error handling, but the existing helpers do not establish exact Solana-mint OHLCV coverage | Retain; do not substitute a similarly named listed asset |
| CoinGecko Onchain token OHLCV | Official SDK documents network + exact token address, fixed intervals, USD mode, timestamp cursor, up to 1,000 rows, and exclusion of empty intervals | Implement as an optional initial history source |
| CoinGecko pool OHLCV | Same official SDK, but requires a chosen pool and token orientation | Defer explicit pool selection and associated liquidity/universe policy |

Pinned official SDK source, inspected 2026-10-21:

- [Token OHLCV endpoint and parameters](https://github.com/coingecko/coingecko-python/blob/daa29b33de0389cb13b47f5590be18181447ac0c/src/coingecko_sdk/resources/onchain/networks/tokens/ohlcv.py)
- [Response schema and token metadata](https://github.com/coingecko/coingecko-python/blob/daa29b33de0389cb13b47f5590be18181447ac0c/src/coingecko_sdk/types/onchain/networks/tokens/ohlcv_get_timeframe_response.py)
- [Pro base URL and authentication](https://github.com/coingecko/coingecko-python/blob/daa29b33de0389cb13b47f5590be18181447ac0c/src/coingecko_sdk/_client.py)

The SDK describes currency=usd as returning OHLCV in USD and the six columns as
`[timestamp, open, high, low, close, volume]`. The adapter interprets these as
interval-opening Unix seconds, USD/token prices and USD turnover, and expects
newest-first pages. These semantics, especially volume units, time boundaries,
ordering and historical depth, must also pass live contract checks before using
this source for a trading strategy. Fixture tests cannot establish provider truth.

CoinGecko coverage is provider-defined token history rather than a guaranteed
complete trade tape for every Solana pool. include_inactive_source=false is
explicit: no stale-price fallback from inactive pools is requested. This does
not reconstruct the historical token/pool universe or remove survivorship bias.
Persisted observations preserve revisions; they cannot establish when a vendor
first knew backfilled data. Backtest interpretation remains a later phase.

## Access and units

Set COINGECKO_PRO_API_KEY through the supported environment/secret configuration
when real access is available. The default base is
`https://pro-api.coingecko.com/api/v3`; authenticated calls use x-cg-pro-api-key.
A plan permitting token OHLCV and the desired history is required. This code
neither purchases a plan nor tests a key. The optional HTTPS base override is
AUTOHEDGE_COINGECKO_BASE_URL. It does not implement the Demo/free endpoint or its
authentication scheme. Keys are optional until this provider is called, excluded
from Settings serialization and redacted in logs; no key is stored in history.
The current cloud has no configured keys and its startup HTTP policy does not
list the CoinGecko host. Supported environment configuration is required for a
future live contract check; fixture tests need neither keys nor network access.

Only venue=solana and quote_currency=USD are supported. There is no USD/USDC
conversion. Supported intervals in seconds are 60, 300, 900, 3600, 14400, 43200
and 86400, mapped to the documented minute/hour/day aggregates. Requests contain
1–2,000 bars, with configurable page_size=2..1000 (default 1000) and
max_pages=1..20 (default 10). Each page requests only the remaining count plus
one possible overlap row, capped by page_size. The existing read retry policy
allows at most two HTTP attempts per page. There is no unlimited pagination,
automatic retry job or silent cache fallback. Quotas are provider/account
specific; request budgets do not establish a permissible polling frequency.

Candle now has optional volume_base and volume_quote, with **at least one
required**. Existing explicit base-volume inputs remain compatible. This adapter
sets volume_quote in USD and leaves volume_base=None. It never divides turnover
by a closing price to invent token volume. Consumers must check the available
unit; missing base volume is not zero. Monetary fields retain Decimal precision
and reject non-finite, boolean or negative quantities; prices must be positive.

## Coverage and pagination

The requested window ends at the latest UTC interval boundary <= as_of and
contains exactly count completed bars. as_of must not be in the future relative
to the receipt clock. The first cursor is one second before that boundary;
subsequent cursors use the oldest returned opening timestamp. Both exclusive
pages and identical inclusive boundary overlaps are supported. Overlaps with
different values, stalled cursors, missing intervals, duplicate/unsorted rows,
unexpected mints, unaligned timestamps and incomplete/future bars fail closed.

Each response must identify the requested mint in meta.base.address. A symbol
alone is insufficient. include_empty_intervals=false prevents requesting
synthetic no-trade bars. A missing interval is an error even when genuine market
inactivity caused it. The adapter reverses the vendor's descending order once;
it does not sort arbitrary rows, interpolate or substitute another asset.
Only exact complete windows are returned, and validate_candle_response remains
in force. A failure after successful pages never returns partial history.

## Python API and storage

```python
from datetime import datetime, timezone
from autohedge.config import load_settings
from autohedge.contracts import Instrument
from autohedge.data.contracts import CandleRequest
from autohedge.data.coingecko import CoinGeckoCandleProvider
from autohedge.data.history import HistoryStore

instrument = Instrument(
    asset_id="So11111111111111111111111111111111111111112",
    symbol="SOL", venue="solana", quote_currency="USD",
)
request = CandleRequest(
    instrument=instrument, interval_seconds=60, count=120,
    as_of=datetime.now(timezone.utc),
)
provider = CoinGeckoCandleProvider(load_settings())
snapshot = provider.fetch_history(request)
store = HistoryStore("agent_workspace/history")  # already ignored by Git
snapshot_id = store.save(snapshot)
series = store.replay(snapshot_id, available_at=datetime.now(timezone.utc))
```

This is an opt-in live API example, not a command run by the tests.
fetch_candles(request) returns only the normalized series; fetch_history also
returns all normalized pages, endpoint, cursor, requested limit and individual
receipt times. The aggregate receipt time is the final page's receipt time.
Clock regression is rejected. Unknown/raw provider fields and credentials are
not persisted. HistorySnapshot revalidates that the final exact window can be
reconstructed from those pages without conflicts or identity changes.

HistoryStore serializes schema-versioned canonical JSON, addresses it by SHA-256,
and publishes a completed snapshot with an atomic hard link that cannot replace
an existing artifact. This requires a filesystem supporting hard links within
the store. Identical serialized snapshots reuse an ID; later receipt times, revised values
or different Decimal representations create new IDs. Snapshot publication is atomic; partial failed downloads are not
published or resumable. Temporary files are cleaned up; existing damaged files
cause an error rather than automatic repair. Reads verify hash, size (32 MiB
maximum), schema and provenance. No network fallback occurs during replay.
Hashes detect corruption, not authenticity against an attacker able to replace
the store. Use a trusted local directory and appropriate filesystem permissions.
This is not a replicated or retention-managed database.

`load(id)` retrieves the recorded acquisition for inspection. `replay(id,
available_at=...)` additionally refuses a snapshot acquired after the supplied
knowledge time. A download today for candles from last year retains today's
receipt time; it cannot masquerade as a snapshot observed last year. The caller
must choose a historical-data policy explicitly for later backtests rather than
silently rewriting receipt times. Identical IDs replay without API calls.

Run the normal unit, integration, lint and wheel checks from DEVELOPMENT.md.
Tests cover page overlap/conflicts, exact boundaries, empty/gapped/incomplete
windows, provider outages, precision, limits, provenance, concurrent publication,
corruption and receipt-aware replay. [RSI/MACD/volatility](INDICATORS.md) now
calculate directly from these normalized series or validated snapshots. Assembly
of a full research snapshot remains phase 2.5.
