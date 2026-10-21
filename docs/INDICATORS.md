# Deterministic indicators (phase 2.4)

`autohedge.indicators` computes versioned analytical features from existing
CandleSeries/HistorySnapshot contracts. It performs no HTTP or LLM calls, needs
no API keys, creates no signals and changes no agent permissions. The algorithms
use positive Decimal closing prices, not float conversions. Arithmetic has its
own 128-significant-digit, half-even context, including validation of results;
the caller's precision, rounding and decimal traps are preserved.

## Definitions, units and warm-up

| Field | Definition | First available with defaults |
| --- | --- | --- |
| rsi | Wilder RSI(14); first mean gains/losses over 14 close-to-close changes, then Wilder smoothing | 15 closes |
| macd | EMA(12) - EMA(26); each EMA independently seeded by the arithmetic mean of its own initial closes | 26 closes |
| macd_signal | EMA(9) of available MACD values, initially their nine-value arithmetic mean | 34 closes |
| macd_histogram | macd - macd_signal | 34 closes |
| volatility | Sample standard deviation of the latest 20 logarithmic close returns, centered at their mean, denominator n-1 | 21 closes |
| annualized_volatility | volatility * sqrt(31,536,000 / interval_seconds), using a continuous 365-day spot year | 21 closes |

An EMA update is `previous + 2/(period+1) * (current-previous)`. Wilder smoothing
is `((period-1)*previous_average + current_gain_or_loss)/period`.
RSI is `100*average_gain/(average_gain+average_loss)`; a fully flat window has
RSI=50, gains without losses give 100, and losses without gains give 0. This
neutral-flat convention is explicit, rather than emitting NaN from 0/0.

Log returns are `ln(close[t]/close[t-1])`. Constant geometric growth has constant
log returns and zero centered sample volatility; drift is a separate quantity.
Volatility is a fraction per candle interval, and annualized_volatility is an
annualized fraction, **not a percentage**. RSI is dimensionless on 0..100; MACD,
signal and histogram are in the instrument's quote-currency units per token.
Periods denote candle counts, not fixed minutes. A 14-period RSI on hourly bars
covers a different horizon than one on minute bars. Seeds and variance conventions
are part of autohedge.indicators.v1; results can differ from libraries using
first-observation/offset EMA seeds or other volatility definitions.

Insufficient warm-up yields None (JSON null) at each affected point. It never
backfills an unavailable value or pretends it is zero. Legitimate zeros remain
available values. Every input candle produces one aligned output point.
`result.latest.ready` is a Python property requiring RSI, MACD signal/histogram
and volatility; it is not a serialized field. Defaults need **34 candles** for
full readiness. The market gate's existing minimum of 30 is a separate check
and does not establish strategy/indicator readiness.

IndicatorConfig accepts integer periods 2..1000, with macd_fast < macd_slow.
volatility_window >=2 ensures a defined sample denominator. minimum_candles
reports the largest required warm-up. Together with CandleSeries' 2,000-bar bound,
these are finite, bounded calculations. No partially initialized EMA values are
used for MACD signal seeding. Rolling variance uses a centered two-pass calculation
rather than subtraction of nearly equal large squared sums.

## API and provenance

```python
from datetime import datetime, timezone
from autohedge.indicators import IndicatorConfig, calculate_indicators

# series is a validated CandleSeries, for example from HistoryStore.replay().
features = calculate_indicators(
    series,
    as_of=datetime.now(timezone.utc),
    config=IndicatorConfig(),
)
if features.latest.ready:
    rsi = features.latest.rsi
    macd = features.latest.macd
    volatility = features.latest.volatility
else:
    # Collect sufficient actual history; no automatic trading fallback.
    missing = features.config.minimum_candles - len(features.points)
```

`indicators_from_snapshot(snapshot, as_of=..., config=...)` first revalidates
recorded page provenance and then calculates from its CandleSeries. It works
with a HistoryStore-loaded acquisition without making any network request.

Inputs and nonvalidating model_copy updates are revalidated. Missing prices,
NaN/Infinity, nonpositive prices, malformed OHLCV, naive times, duplicate/gapped/
unsorted intervals and incomplete bars are rejected with safe DataQualityError
categories. as_of is **knowledge time**: history.received_at must be <= as_of.
Downloading historical candles today does not make them available yesterday.
No future data is silently trimmed to pass validation. Calculations are causal:
each point uses only its prefix of completed closes; later closes cannot change
an earlier result. The recorded receipt time still applies to the whole input
acquisition, so a prefix's mathematical value alone does not prove historical
availability.

IndicatorSeries records implementation version, config, exact instrument/source,
interval, input receipt time, requested knowledge time, and a SHA-256 history_digest.
The digest hashes sorted, compact JSON of the validated CandleSeries, including
its identity, prices, volumes and receipt time. This differs from HistoryStore's
snapshot ID, which also covers pages/cursors/endpoints. The [research snapshot builder](RESEARCH_SNAPSHOTS.md)
links the acquisition ID as well as feature provenance when recorded pages are supplied. Decimal
representations are preserved; identical serialized series give identical hashes.
Hashes do not independently authenticate a vendor or prove a caller-supplied
feature artifact was calculated honestly. Schemas check warm-up, chronology,
histogram and annualization consistency, not the origin of arbitrary claims.

The API does not reject historical data merely for being old: it supports offline
analysis/replay. Expected asset, freshness, source requirements and readiness for
a strategy must still pass the separate market/snapshot gates. There is no
automatic collection, report/prompt attachment or full ResearchSnapshot assembly
inside this calculation module; [phase 2.5](RESEARCH_SNAPSHOTS.md) supplies that integration. Indicators provide features
for the planned Market Anomaly role and quantitative baselines, not a complete
trading strategy. See [ENGINE_ARCHITECTURE.md](ENGINE_ARCHITECTURE.md).

## Verification

[Reference fixtures](../tests/fixtures/indicators/README.md) contain hand-derived
Wilder examples and exact MACD fractions, plus a closed-form volatility example.
Tests also cover warm-up boundaries, flat/rising/falling/geometric prices,
sub-float-resolution price changes, price scaling, input/context independence,
prefix causality, future receipt times, invalid values, bounded maximum windows,
serialization and persisted-history replay. Runtime and installed-wheel checks
exercise the installed package without remote model/API calls. Neither indicator
correctness nor green tests establish strategy predictiveness or profitability.
