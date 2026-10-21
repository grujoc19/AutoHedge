# Shared research snapshots (phase 2.5)

A ResearchSnapshot binds a MarketData bundle, Python-computed indicators, a
quality assessment, evidence references and optional recorded history pages to
one explicit knowledge time. It is frozen, serializable and content-addressed.
Every research role receives the same snapshot ID and data view. The report
retains the complete acquisition and calculation inputs for audit and replay.
No scheduler, trading signal generator, risk approval or order execution is
introduced here. Live access/entitlements and actual remote contracts still
need separate verification.

## Build from normalized observations

```python
from datetime import datetime, timezone
from autohedge import AutoHedge
from autohedge.config import load_settings
from autohedge.snapshots import build_research_snapshot

# data: validated MarketData; expected: caller-selected Instrument.
# Optional acquisition: a HistoryStore-loaded HistorySnapshot for data.history.
snapshot = build_research_snapshot(
    data, expected=expected, as_of=datetime.now(timezone.utc),
    history_acquisition=acquisition,
)
engine = AutoHedge(settings=load_settings())
report = engine.run_report("Analyze supplied evidence", instrument=expected, snapshot=snapshot)
```

The example uses already supplied data; it is not an automatic live feed.
By default the builder requires dated news and complete indicator warm-up.
With default IndicatorConfig, that is at least 34 actual closed candles.
QualityPolicy is reused, and its captured min_candles is raised to cover the
configured indicator periods. Explicit QualityPolicy(require_news=False) allows
a news-free snapshot; absence stays visible in the evidence list and report
warnings. This does not authorize a strategy requiring news to trade without it.
Other freshness, identity, price-deviation, volume-unit and gap checks remain
those in [MARKET_DATA.md](MARKET_DATA.md).

The builder calculates indicators itself, accepting no supplied numerical claims.
Snapshot parsing recomputes the indicator series and quality assessment and
checks all IDs and references against the actual observations. Rehashing a
modified RSI, quality result or source record cannot bypass those checks.
Quality and feature arithmetic own fixed Decimal precision/rounding/traps, so
the embedding application's decimal context does not change snapshot identity.

## Explicit collection

`collect_research_snapshot(request, quote_provider=..., history_provider=...,
news_provider=..., clock=...)` combines the existing provider interfaces.
request is a CandleRequest including the exact instrument, interval, count and
history cutoff. The count must already cover policy and indicator warm-up.
Missing required news providers, invalid policy/periods or insufficient requested
history fail before reads. Each adapter checks its own credentials when used;
the generic collector does not verify account entitlements in advance.

Collection proceeds **history -> news -> quote**, taking the quote last to
minimize its age. It validates the returned history acquisition against the
exact request and records the final local clock as snapshot.as_of. Backward
clocks, future receipts, wrong instruments, stale data, incomplete news/history
and any provider failure reject the whole result. There is no partial snapshot,
automatic cached-data substitution or collector-level retry loop. Existing
bounded read retries remain inside the provider transport.

The existing CoinGeckoCandleProvider implements HistoryProvider.fetch_history;
JupiterProvider supplies fetch_quote only with a trusted injected block-time
resolver; ExaNewsProvider supplies fetch_news. These adapters accept injected
HTTP clients and clocks for offline tests. Real collection requires suitable
configured access and authenticated read-contract verification. Jupiter's
createdAt remains token creation, never a substitute price event time.

Collection does not publish incomplete acquisitions. A completed snapshot embeds
all recorded history pages and their content ID; callers can save those through
HistoryStore after successful collection. That ID matches HistoryStore.save().
If manually supplied data has no acquisition, its history fingerprint still binds
the normalized values, but the report explicitly marks provenance as
caller-asserted. Included pages are also provider assertions, not cryptographic
proof of a remote response.

## Identity, evidence and knowledge time

snapshot_id hashes compact sorted JSON of the normalized snapshot content,
excluding the ID itself. It covers the exact instrument, as_of, MarketData,
IndicatorSeries/config/version, captured quality policy and assessment, optional
history acquisition/ID, and evidence manifest. Decimal representations and
source order are preserved. Identical serialized inputs produce identical IDs;
changed observations, configuration or knowledge time produce a new identity.
The existing history and indicator hashes retain their serialization format.

EvidenceReference contains kind, source, optional source_event_id, event_time,
received_at, known_at and a content-based evidence_id. References are separately
bound to quote, history, indicators and each news article, with domain-separated
record hashes. Publication is the news event time; the local receipt is its
knowledge time. Indicator knowledge is the snapshot cutoff, derived from history
already received by it. All reference times must be consistent and <= as_of.

A changed article excerpt creates a new content reference while keeping its
provider event ID. This is **not** semantic novelty, source authentication or
independent confirmation: deduplication of syndicated stories, first_seen/event
versioning, wallet attribution and EvidenceEvent/AgentFinding contracts remain
2.6–3.1 work in [ENGINE_ARCHITECTURE.md](ENGINE_ARCHITECTURE.md).

## Research runtime

Use `run_report(..., instrument=expected, snapshot=snapshot)` or the equivalent
run() keyword. snapshot cannot be mixed with market or market_data. The expected
instrument is mandatory. Snapshot/schema/provenance checks run before team
construction. Freshness runs before and after every role, with an effective
policy that takes the stricter captured/runtime limits: minimum ages/lags/
deviation tolerances, maximum required candle count, and news required if either
policy requires it. A snapshot cannot be used before its as_of. A stale snapshot
stops later roles rather than collecting new evidence in the middle of a cycle.
The report's market_quality records the actual runtime check time/effective
policy; snapshot.quality preserves the original assessment at its cutoff.

Prompts include the quote, latest candle, historical range/count/interval and
history/acquisition IDs, latest calculated indicators/config/readiness, complete
dated news and the evidence manifest. Raw history arrays, full indicator arrays
and acquisition pages remain in the report, keeping long histories out of the
model context. News is not silently trimmed: if it exceeds the configured
character budget, the cycle rejects it before invoking a role. Earlier role
results still count against that budget at each stage.

The **public default AutoHedge snapshot path** constructs a fresh team with
additional Exa tools disabled, even if legacy exploration has search_enabled=True.
Only OpenAI credentials are needed for model calls when the data is already
supplied. The original Settings remain unchanged. Custom/injected teams are
trusted application code and must honor the same frozen-evidence/no-extra-tools
contract. Models are instructed to cite evidence IDs; semantic citation accuracy
and structured BUY/SELL/HOLD outputs are not yet validated by this text pipeline.
All reports retain execution_enabled=false.

Legacy task-only, market and market_data exploration remains supported. A raw
market_data report now explicitly warns that computed indicator/strategy
readiness has not been established. Snapshot readiness is a data prerequisite,
not proof of predictiveness, independent signals or a successful trading strategy.

The regular unit/runtime/wheel checks cover collection order/failures, malicious
copies with recomputed hashes, JSON replay, captured/runtime policy strength,
expiry during a role, large-history compact contexts, oversize news, atomic
report preservation and the actual SDK path with additional search disabled.
HTTP and model responses are replaced locally; these are not live API tests.
