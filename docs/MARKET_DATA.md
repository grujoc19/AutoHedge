# Market-data contracts and quality gates (phase 2.1)

These contracts validate supplied data. They do not fetch it automatically,
authenticate its provenance, calculate indicators, approve trades or sign orders.
Jupiter/Exa adapters are documented in [PROVIDERS.md](PROVIDERS.md). The initial policy assumes
continuous spot markets such as Solana; it is not an exchange-calendar model.

## Normalized units and identity

- Instrument includes the exact asset ID/mint, venue, symbol and quote currency.
  The caller specifies the expected instrument; mismatches are rejected.
- Quote prices and candle OHLC are **quote-currency units per one base token**.
  USD is not automatically treated as USDC. No implicit currency conversion occurs.
- Quote volume fields distinguish base-token and quote-currency 24h volume.
  Candle volume is explicitly in base-token units, never raw lamports.
- Quote base/quote token decimals are explicit integers from 0 to 30. Values must
  be normalized by the adapter; declaring decimals does not perform scaling.
- Monetary inputs use finite Decimal values (at most 80 digits, at most 40
  fractional places in market-data contracts). Positive prices and nonnegative
  volume are required; booleans are rejected as quantities.
- Source identifiers, event/publication times and local received_at times are
  distinct. Timestamps must be timezone-aware and normalize to UTC.

## Candles and news

Candle intervals are half-open `[open_time, close_time)`. Each duration must
match the declared fixed interval. Rows must already be ordered, unique,
gap-free and completed at receipt. No missing rows are invented or filled with
zero volume. Monthly/variable-length/calendar sessions need a separate adapter
contract rather than pretending they are a fixed number of seconds.

A bundle contains one quote and one candle series for the same instrument,
plus optional news. Quote and history may come from different declared sources.
News has a source, ID, URL, title, publication time and receipt time; duplicate
source/ID pairs or URLs are rejected. Credential-bearing URLs are not accepted.
Article content remains untrusted input, not an instruction to an agent.

## Default policy

| Check | Default |
| --- | --- |
| Maximum quote event age | 60 seconds |
| Maximum event-to-receipt transport lag | 30 seconds |
| Minimum complete, contiguous candles | 30 |
| Maximum latest candle close age | 2 declared candle intervals |
| Quote versus latest candle close deviation | 1,000 basis points (10%) |
| Maximum publication age of supplied news | 86,400 seconds (24h) |
| News required in the bundle | No; absence is reported explicitly |

Exact limits are inclusive; future data is rejected without clock-skew
allowances. Event age is used, so recently receiving an old quote does not make
it fresh. A genuine volatile move can exceed the deviation limit and be
rejected: the threshold is an explicit conservative policy, not a proof that
the market price is wrong. Indicator-specific warm-up requirements belong to
phase 2.4; 30 candles alone does not establish readiness for every indicator.

## Python API

```python
from autohedge import AutoHedge
from autohedge.config import load_settings
from autohedge.contracts import Instrument
from autohedge.data.contracts import QualityPolicy
from autohedge.data.quality import parse_market_data

# provider_payload must already use the documented fields, UTC times and units.
data = parse_market_data(provider_payload)
expected = Instrument(
    asset_id="So11111111111111111111111111111111111111112",
    symbol="SOL",
    venue="solana",
    quote_currency="USDC",
)
settings = load_settings().model_copy(
    update={"market_quality": QualityPolicy(min_candles=60)}
)
engine = AutoHedge(settings=settings)
report = engine.run_report("Research SOL", instrument=expected, market_data=data)
assessment = report.market_quality
```

`parse_market_data` accepts provider-shaped dictionaries or already constructed
models, revalidates them and exposes a safe DataQualityError on schema failure.
The example is an API sketch, not a live feed: callers still need normalized
data and valid OpenAI/Exa bindings for real model calls. load_settings reads
credentials without printing or copying them into the report.

The gate runs before any agent is constructed and before/after each stage. If
the data becomes stale while an agent is running, later stages are not invoked.
Only completed reports receive a QualityAssessment containing the policy and
actual check time, quote age, last candle close, candle/news counts and deviation.
All reports still have execution_enabled=false.

`run_report(..., market=legacy_snapshot)` remains supported for price-only
research. Its observation time is checked, but it never receives an OHLCV
assessment and its limitations are included in warnings. The old and new market
arguments cannot be mixed. Research without market data is still allowed with
explicit missing-data warnings; automatic collection/enforcement for strategies
will be implemented in subsequent phases.

## Provider interfaces and tests

`MarketDataProvider.fetch_quote(instrument)` returns a normalized Quote;
`fetch_candles(CandleRequest)` returns a CandleSeries. Adapters must use
`validate_candle_response` to check requested identity, interval, count and as_of
coverage (including the latest completed interval). NewsProvider returns
timestamped NewsArticle objects. QuoteProvider and CandleProvider also expose
those capabilities separately. Jupiter implements quotes only with a trusted
block-time resolver; Exa implements news. Historical candles remain a later step.

Run the installed-dependency tests:

```bash
SWARMS_TELEMETRY_ON=false uv run --locked python -m unittest discover -s tests/unit -v
```

Fixtures cover numeric/timestamp boundaries, wrong mint/currency, OHLC
inconsistency, duplicate/gapped/partial candles, stale/future/news data, delayed
quotes, decimal-context independence, and rejection before team construction or
after a stage makes data stale. Existing runtime and wheel checks remain required.

Freshness/shape consistency cannot prove liquidity, sellability, execution price
or honest provider data. Those checks belong to later provider/risk/execution
work. Normalized Exa tool output remains separate from a submitted bundle and
is not covered by its news assessment. No new API or private-key binding is needed
to run this phase's tests.
