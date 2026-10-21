# Fork development guide

## Implemented scope

The default workflow is research-only. Phase-1 steps 1.1–1.6 are implemented
locally: reproducible installation/CI, centralized configuration and logging,
validated domain contracts, and an explicit isolated research pipeline.

The sequence is Director thesis -> Quant -> Sentiment -> Risk -> Director
synthesis. Every step receives the request plus all completed results. There
is no LLM-controlled handoff or Execution-Agent in this path. Existing role
prompts, Swarms, Conversation and provider helpers are reused. The standalone
`build_director_agent()` compatibility factory returns a Director without
handoffs; use AutoHedge for a complete cycle.

`run_report(task, market=..., portfolio=...)` returns a frozen Pydantic
ResearchReport. Each cycle resets Conversation and last_report; failed cycles
leave last_report unset. `run()` preserves list/dict/str formats, but dict/str
now serialize the report, while list is the current cycle's conversation plus
warnings. Optional output_file_path writes a complete JSON report atomically
(relative paths resolve below output_dir). Existing successful files remain if
a subsequent cycle fails; consumers must check run_id and timestamps.

Reports are research text, not executable signals. The domain includes validated
Instrument, MarketSnapshot, PortfolioSnapshot, Signal and Rejection contracts.
Amounts use finite nonnegative/positive Decimal values; timestamps must be
aware and normalize to UTC. Signal proposals have no execution quantity. Freshness/consistency gates now apply to supplied market-data bundles, and
legacy price-only inputs are checked for stale/future observation times. No
scheduler-driven feed collection runs inside the research pipeline.
Explicit snapshot collection and assembly now bind provider observations, replay,
computed indicators and evidence IDs to the research context.
Risk approval and trading remain unimplemented. Missing inputs are identified
in report warnings.

## Installation and dependency updates

Use uv 0.12.23. Python 3.12 is the current local validation baseline; CI also
covers Python 3.10 and 3.11. From the checkout:

```bash
uv sync --locked --python 3.12
uv pip check
```

Runtime dependencies are declared only in `[project.dependencies]` in
`pyproject.toml`. Developer tools are in `[dependency-groups.dev]`. The Poetry
build backend remains, with its own exact version pinned. The old independent
requirements list now delegates to the project. `swarm-models`, FastAPI and
Uvicorn are not direct dependencies of the implemented core workflow; Uvicorn
is still installed transitively by Swarms/MCP.

To intentionally change dependencies, edit the manifest, run `uv lock`, inspect
the lockfile diff and run all checks. Use `uv lock --upgrade-package NAME` for a
specific transitive upgrade. Swarms is pinned because orchestration depends on
its behavior. The lockfile includes compatible resolutions for multiple Python
versions/platforms; CI currently exercises Linux and Python 3.10–3.12 only.

For API-backed research, copy `.env.example` to `.env` and provide
`OPENAI_API_KEY` and `EXA_API_KEY`. Runtime tests do not need real credentials.
Jupiter/Massive credentials are optional for their standalone helpers.

## Tests and CI

```bash
# Standard library only; no third-party package installation required.
python -m unittest discover -s tests -v

# Domain/configuration/orchestration tests with installed dependencies.
SWARMS_TELEMETRY_ON=false uv run --locked python -m unittest discover -s tests/unit -v

# Real Swarms, Conversation, tool schemas and CLI; no network or keys.
uv run --locked python -m unittest discover -s tests/integration -v

uv run --locked ruff check autohedge tests scripts example.py
uv run --locked ruff format --check autohedge tests scripts example.py
uv build --no-sources
uv run --locked python scripts/check_wheel.py dist/autohedge-0.1.5-py3-none-any.whl
```

The dependency-free suite checks that package import stays lazy. Unit tests use
real Pydantic contracts and fake model roles to verify dependency order, empty
and oversized output rejection, timeout boundaries, recovery, UTC timestamps,
secret redaction and safe HTTP errors. HTTP tests use MockTransport, not real
provider endpoints.

Runtime tests construct real agents and schemas, and exercise a full cycle
through real Swarms with only call_llm responses replaced. They reject even
caught outbound socket attempts, use empty local dotenv files and remove known
API/wallet bindings. Swarms telemetry and remote model-cost discovery are
explicitly disabled for these tests. These tests do not validate remote API
access or model quality.

The wheel check uses `uv export --locked` to install hash-pinned runtime
requirements into a temporary environment, installs the built wheel without
resolving new dependencies, checks dependency consistency, and runs import and
CLI checks from a temporary working directory. The wheel is built from the
source distribution, so missing package files cannot be masked by an editable
checkout. Requires uv on PATH and package-registry access/cache for installation.

CI now has separate offline, unit/runtime and lint/build jobs, without API secrets
or write permissions. Copied workflows referring to missing projects, local
Actions, Dockerfiles, Makefiles and documentation builds were removed. The
release publishing and repository metadata workflows remain separate.

`experimental/` scripts are not imported by the main application or active test
suite. They may need undeclared packages such as `cryptoagent`,
`websocket-client` or `swarm-models` and have known correctness problems. Do not
install their dependencies into the core blindly or use their outputs as a
profitability claim. Historical CSVs in `logs/` are not test fixtures.

## Configuration and error contracts

`Settings` reads known names from the nearest `.env` above the working directory,
then overlays the process environment (including explicitly empty values).
Dotenv interpolation is disabled. Explicit Settings objects can be injected in
Python. Keys are optional until their provider is used; default research needs
OpenAI and, unless search is disabled, Exa. AUTOHEDGE_OPENAI_API_KEY is accepted
as a fallback alias for the configured cloud binding. POLYGON_BASE_URL remains
a fallback alias for MASSIVE_BASE_URL. Canonical process names take precedence.

Models, request/cycle time budgets, search enablement, logging and API base URLs
are documented in `.env.example`. API overrides require HTTPS without embedded
credentials/query strings. Secrets are excluded from Settings repr/serialization.
The existing Jupiter, Massive and Exa adapters share configured headers/timeouts
and ProviderError; response bodies, raw URLs and keys are not in these errors.
Yahoo remains a separate yfinance adapter: required fetch failures raise
ProviderError, while optional fields can still return partial data. Full
snapshot data-quality enforcement belongs to phase 2.

`configure_logging` explicitly owns process-wide Loguru sinks at the application
boundary, replacing Swarms' implicit sinks. Embedders may supply a sink. Known
secret values, credential headers and sensitive extra fields are redacted;
tracebacks/local-variable dumps are suppressed. Logs contain run IDs, stages and
safe failure categories, not raw research prompts or model responses. No custom
sink is installed at package import.

There are five application stages, at most six normal model turns (Sentiment
gets up to two turns to consume search results), no application-level model/tool
retries and no hidden tool-summary or context-compression model calls. Prompt
size is limited to 32,000 characters, model output budget to 1,500 tokens per
turn, and each recorded stage to 20,000 characters. Search-provider failures
are recorded independently so a swallowed Swarms tool error still aborts the
cycle. Empty, invalid or oversized stage output never proceeds to later stages.

The cycle deadline is checked before and after synchronous agent calls; it
prevents subsequent stages, but cannot forcibly interrupt arbitrary Python
code already running. SDK/HTTP request timeouts bound normal provider calls;
there is no claim of a hard wall-clock cancellation guarantee. A hung custom
agent requires external process supervision in the later runtime phase.

## Development safety requirements

- Paper-trade before integrating any live execution.
- Keep the signing helpers detached from language-model agents.
- Introduce a deterministic risk gate before any wallet API call.
- Default to disabled live trading; require separate explicit operator approval.
- Use a separate wallet with limited capital for any future authorized live test.
- Evaluate realistic fills, transaction costs and slippage.
- Never commit dotenv files, private keys, wallet secrets or authenticated logs.

Installation, local smoke tests and green CI do not establish market quality,
remote API readiness, profitable performance or authorization to trade.

## Phase 2.1: supplied market-data validation

The shared value types were extracted into `autohedge.contracts`; imports such
as `from autohedge.domain import Instrument` remain compatible. Domain contracts
revalidate model instances, so nonvalidating model_copy updates are not accepted
at validated boundaries.

`autohedge.data` adds Quote, Candle, CandleSeries, NewsArticle, MarketData,
CandleRequest, QualityPolicy and QualityAssessment, plus provider Protocols.
Pass an explicit expected Instrument together with market_data to run/run_report.
A request cannot combine a legacy market snapshot and a normalized bundle.
Structural validation rejects inconsistent OHLCV, mixed assets/currencies,
non-finite/unbounded numbers, duplicate/gapped/unsorted bars, partial candles,
invalid publication/receipt ordering and duplicate news. There is no sorting,
interpolation, unit guessing, currency conversion or fake-price fallback.

The runtime quality gate checks data at the injected UTC clock before creating
agents and before/after every role call. A bundle that becomes stale during a
call aborts the cycle rather than proceeding. Successful reports record the
actual quality policy, check time, quote age, last candle close, counts and price
deviation. This is data validation, not risk approval or proof that a provider's
reported values are true. Normalized Exa tool results remain separate from a
submitted bundle and are not included in its QualityAssessment.

Configure a per-application policy through
`Settings(market_quality=QualityPolicy(...))`. Defaults and units, the new Python
API and explicit limitations are documented in [docs/MARKET_DATA.md](docs/MARKET_DATA.md).
No API credentials or live endpoints are needed by the added contract/gate tests.

## Phase 2.2: Jupiter and Exa adapters

Jupiter token discovery and price helpers now delegate to a validated adapter;
Exa sentiment search returns normalized dated news excerpts. The shared HTTP
transport supports explicit read-only retries (at most two attempts) while
transaction calls keep one attempt. Decimal JSON prices retain their precision.
MockTransport fixtures cover schemas, identity, timestamps and failure behavior.
No additional dependencies were introduced.

Jupiter Price V3 does not supply a price event timestamp: createdAt is token
creation. PriceObservation therefore has event_time=None; a strict Quote needs
an injected, trusted block-time resolver and freshness validation. No default
RPC resolver is supplied. OHLCV and explicit snapshot collection
are available through phases 2.3 and 2.5. Exa publication
dates are provider estimates, and malformed/undated news fail closed. Legacy
helper paths remain available with normalized subsets rather than raw vendor
fields; see [docs/PROVIDERS.md](docs/PROVIDERS.md) for the output changes,
retry semantics, pinned API sources and integration limits. These fixtures do
not replace authenticated read-only provider contract checks.

## Phase 2.3: Solana history and replay

The optional CoinGecko Onchain CandleProvider collects exact completed USD
windows by mint and bounded pagination, rejecting gaps and conflicting overlaps.
Candle.volume_base is now optional, and volume_quote is supported; at least one
explicit unit is required. Existing base-volume payloads remain compatible.
CoinGecko's USD-mode turnover maps to volume_quote; no token volume is inferred.

HistorySnapshot preserves normalized pages, endpoint/cursors and receipt times.
HistoryStore writes complete canonical snapshots atomically without overwrites,
verifies their hashes and schemas, and requires an explicit availability clock
for replay. A later backfill cannot impersonate a previously observed snapshot.
There is no network/cache fallback during replay. Provider outages never return
or publish partial windows. Solana address validation is shared with Jupiter.

COINGECKO_PRO_API_KEY is optional, secret-protected and only used by this adapter;
AUTOHEDGE_COINGECKO_BASE_URL can override its HTTPS Pro endpoint. No dependencies
were added. Source selection, API assumptions, unit compatibility, usage and the
remaining live-access/entitlement checks are in [docs/HISTORY.md](docs/HISTORY.md).
Fixtures are synthetic and do not prove live data coverage or license rights.
Historical-universe bias and provider revisions require separate backtest policy.

## Phase 2.4: deterministic indicators

`autohedge.indicators` computes Wilder RSI, independently SMA-seeded MACD and
sample log-return volatility from validated CandleSeries. It preserves Decimal
prices without a float round trip and owns a 128-digit arithmetic context;
no new numerical dependencies were added. There is no existing active RSI/MACD
implementation to replace. NumPy/Pandas remain available for later measured ML
work, rather than being added as a direct dependency for these bounded windows.

None marks unavailable warm-up values. Defaults require 34 candles for complete
readiness, which is separate from the market-quality gate's 30-candle minimum.
Input receipt time must be <= the explicit knowledge time; future/incomplete,
gapped, malformed or not-yet-received history fails validation. Series record
implementation/config, input digest, identity, interval and timestamps. The
calculation API does not itself create a trade signal or change agent prompts.
Full snapshot assembly is available through the phase-2.5 API. Definitions, units, reference fixtures
and API usage are in [docs/INDICATORS.md](docs/INDICATORS.md).

The user-specified later engine architecture is tracked in
[docs/ENGINE_ARCHITECTURE.md](docs/ENGINE_ARCHITECTURE.md) and the corresponding
plan rows (2.5–2.8, 3.1a–3.4). Preserve the functioning research components during
migration. Local/cloud role routing, source novelty, on-chain event attribution,
independent evidence and manipulation evaluation have explicit acceptance tests
to implement; they are not implied by passing the current numeric unit suite.

## Phase 2.5: shared research snapshots

`build_research_snapshot` binds validated MarketData, freshly calculated
indicators, quality policy/assessment, evidence references and optional history
acquisition pages to one knowledge time and content ID. `collect_research_snapshot`
coordinates the existing injected providers in history/news/quote order, without
partial results or hidden fallbacks. Dated news is required by default; warm-up
raises the captured minimum to cover all configured indicators. Source records,
features and hashes are revalidated/recomputed at input/report boundaries.
Quality arithmetic now owns rounding/traps as well as precision for reproducible
IDs. Canonical JSON/hash utilities preserve the earlier history/feature ID format.

AutoHedge.run/run_report accept snapshot= with the exact expected instrument.
All roles receive the same compact current values and evidence manifest; full
arrays and pages stay in the audit report. Captured and runtime policies combine
conservatively and freshness remains checked around every stage. The public
default snapshot factory disables extra Exa searches without changing Settings;
injected factories must honor that contract. Legacy exploration remains usable
with explicit computed-readiness limitations. Semantic citation validation,
strategy signals and risk approval remain later steps. API behavior, defaults,
collection limits and live-access caveats are in
[docs/RESEARCH_SNAPSHOTS.md](docs/RESEARCH_SNAPSHOTS.md).
