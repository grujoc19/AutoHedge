# AutoHedge

AutoHedge is a Python multi-agent market research project. This fork is being
stabilized into a modular trading system in small, independently tested steps.

**Current behavior:** each research cycle creates a fresh Swarms team and runs
Director thesis -> Quant -> Sentiment -> Risk -> Director synthesis. Each stage
receives the preceding results and returns bounded research text. It does not execute trades, enforce portfolio
risk limits, or provide an integrated paper-trading engine. Market-data and
Solana swap helpers exist separately; only Exa search is currently attached to
the research agents. Numerical model output is not a verified market signal.

## Development setup

Python 3.10+ is supported by the manifest. CI is configured for Python 3.10,
3.11 and 3.12; Python 3.12 is the development baseline. Install
[uv](https://docs.astral.sh/uv/) and run from the repository root:

```bash
uv sync --locked --python 3.12
uv run --locked autohedge --help
```

`pyproject.toml` is the dependency source; `uv.lock` records exact resolutions
and artifact hashes. The locked development toolchain uses uv 0.12.23.
For a conventional pip installation, `python -m pip install -e .` also works,
but it does not reproduce the lockfile. `requirements.txt` delegates to the same
manifest rather than maintaining a second dependency list.

Copy `.env.example` to an untracked `.env` and provide `OPENAI_API_KEY` and
`EXA_API_KEY` for research requests. Keys are not needed for help, version,
offline tests or runtime smoke tests. Do not commit secrets.

```bash
uv run --locked autohedge
```

Enter a research task, `help`, or `quit`. Leave `SOLANA_PRIVATE_KEY` empty.
Standalone Jupiter and Massive helpers have their own optional API-key
requirements documented in `.env.example`.

## Checks

```bash
python -m unittest discover -s tests -v
SWARMS_TELEMETRY_ON=false uv run --locked python -m unittest discover -s tests/unit -v
uv run --locked python -m unittest discover -s tests/integration -v
uv run --locked ruff check autohedge tests scripts example.py
uv run --locked ruff format --check autohedge tests scripts example.py
uv build --no-sources
uv run --locked python scripts/check_wheel.py dist/autohedge-0.1.5-py3-none-any.whl
```

The first suite requires only the standard library. Unit tests use installed
Pydantic and role fakes, covering settings, redaction, data validation and failure
paths. Runtime smoke tests use
actual installed packages and prohibit outbound socket connections. The wheel
check creates a fresh environment with locked dependencies and verifies package
imports and CLI entry points outside the checkout. These checks do not measure
trading performance or prove compatibility with remote APIs.

`AutoHedge.run_report()` returns a validated report with a unique run ID, UTC
timestamps, all five stages and explicit missing-data warnings. Supplied
`market_data` bundles and computed `snapshot` inputs are checked before team
construction and before/after every stage; stale, inconsistent or incomplete data stops research. Reports always
have `execution_enabled=false`; BUY/SELL/HOLD schemas exist but agent-generated
trading signals, risk approval and execution are not yet implemented. Settings
are loaded from the nearest working-directory `.env` and the process environment
without mutating the latter. Process values take precedence.

See [DEVELOPMENT.md](DEVELOPMENT.md) for dependency maintenance, scope and safety
boundaries. See [docs/MARKET_DATA.md](docs/MARKET_DATA.md) for the new provider-neutral
quote/OHLCV/news contracts and data-quality policy. Validated Jupiter and Exa
adapters are described in [docs/PROVIDERS.md](docs/PROVIDERS.md). Jupiter USD prices
require a trusted block-time resolver before they qualify as freshness-checked
quotes. The optional [Solana history adapter](docs/HISTORY.md) adds completed USD
OHLCV with explicit volume units and immutable, receipt-aware replay. It requires
CoinGecko Pro access with suitable entitlements; live access is unverified.
Python [RSI, MACD and volatility](docs/INDICATORS.md) now have deterministic
Decimal calculations and explicit warm-up rules. The
[shared snapshot](docs/RESEARCH_SNAPSHOTS.md) combines them with evidence IDs,
recomputed features, policy checks and a compact view for every role. Collection
is explicit; scheduler-driven feeds and trading remain future work.

Historical scripts under `experimental/` are separate prototypes,
not supported trading entry points, and are excluded from the active lint scope.

The planned engine roles, local/cloud routing, independent-evidence checks and
minutes-to-hours strategy horizon are recorded in
[docs/ENGINE_ARCHITECTURE.md](docs/ENGINE_ARCHITECTURE.md). News Intelligence,
Market Anomaly and On-Chain Intelligence will supply a Trading Director; a
separate deterministic Risk Engine will control order eligibility. These roles
and model routes are future work, not currently running agents.

## Roadmap

1. Stabilize lifecycle, configuration, dependencies, CI and orchestration.
2. Normalize real market data and calculate indicators in Python.
3. Produce validated BUY, SELL and HOLD proposals with supporting evidence.
4. Enforce deterministic portfolio limits, liquidity checks and a kill-switch.
5. Validate realistic backtesting and paper trading.
6. Add recoverable operation and separately authorized live execution.

Live trading must remain disabled by default. Any future activation requires
explicit operator approval and a separate wallet with limited capital.

## License and origins

MIT. This fork builds on [The Swarm Corporation's AutoHedge](https://github.com/The-Swarm-Corporation/AutoHedge)
and preserves its existing components where useful.
