# Jupiter and Exa adapters (phase 2.2)

The adapters reuse Settings, the shared HTTP transport, typed data contracts and
safe application errors. Imports perform no network calls. They require their
own configured API keys only when used. HTTP fixtures verify the implementation;
authenticated live responses have **not** been verified in this environment.

## Jupiter

```python
from autohedge.config import load_settings
from autohedge.contracts import Instrument
from autohedge.data.jupiter import JupiterProvider

instrument = Instrument(
    asset_id="So11111111111111111111111111111111111111112",
    symbol="SOL", venue="solana", quote_currency="USD",
)
provider = JupiterProvider(load_settings())
tokens = provider.search_tokens("SOL")
observation = provider.fetch_price(instrument)
# observation.event_time is None; this is not a freshness-checked Quote.
```

Token discovery preserves distinct mints sharing a symbol. It never selects a
mint automatically. An exact mint query cannot return unrelated identities.
Metadata exposes id/name/symbol/decimals only: token-search usdPrice/updatedAt
are not accepted as a price event or used as a fallback for a missing price.
A syntactically valid 32-byte base58 address is not proof of a safe token.

Price V3 returns USD per token, not USDC per token. The adapter requires
venue=`solana`, quote_currency=`USD`; implicit conversion is forbidden. Prices
are decoded directly into Decimal without a float round trip. Ambiguous
duplicate JSON keys and non-finite numbers are rejected. Missing requested
mints (including tokens Jupiter omits as unreliable), unexpected mints, invalid
prices and malformed decimals fail the complete request. Batches contain 1–50
unique mints. Discovery can legitimately return an empty tuple.

**Timestamp limitation:** Price V3 `createdAt` is the token's creation time.
`blockId` identifies the price-computation block and may be null. There is no
price timestamp in the response. `fetch_price` therefore returns PriceObservation
with local receipt time and event_time=None. It cannot be used as Quote in a
validated MarketData bundle. `fetch_quote` requires an injected trusted
`block_time_resolver(block_id) -> aware datetime`, rejects missing block IDs and
invalid/future/stale times, and applies the configured freshness/transport-lag
policy. No default RPC resolver or unverified timestamp approximation is supplied.
The resolver must authenticate the block-to-time mapping and bound its own I/O.
USD quote_decimals=2 denotes currency minor units; prices are not rounded to cents.

Jupiter supplies no candle history through these endpoints. QuoteProvider and
CandleProvider are separate Protocols; MarketDataProvider combines both. This
adapter does not claim to implement fetch_candles. Historical OHLCV and volume
are supplied separately by the optional [CoinGecko adapter](HISTORY.md); its
access and live contracts require verification.

## Exa news

`ExaNewsProvider.fetch_news(instrument)` searches using the symbol, mint and
venue. `search_news(query)` provides the same normalized output for a custom
query. Searches request category=news, type=auto, two results, an explicit
publication window from QualityPolicy.max_news_age_seconds, and text excerpts
limited to 4,000 characters. The deprecated contents.context option is removed.

Each result must have an ID, credential-free HTTP(S) URL, nonempty title and
an aware publication timestamp within the requested window and within the
receipt-time age limit. Invalid/undated/naive/future/stale/duplicate results
reject the complete response; the adapter never silently replaces their dates
or drops bad rows. An absent text field yields an empty excerpt; nonempty text
is stored in NewsArticle.summary as an **excerpt**, not an AI-generated summary.
Exa estimates publishedDate from page HTML. These dates and article claims are
provider assertions, not independently verified facts or proof of relevance.
Article text remains untrusted data.

Empty results return an empty tuple, or DataQualityError when require_news=True.
The existing sentiment tool `exa_search` now serializes normalized results and
explicit warnings when there are no sources. Provider and invalid-query failures
are recorded by the team, so a swallowed SDK tool exception still stops research.
Tool results do not automatically join the report's submitted MarketData bundle;
its QualityAssessment only covers that bundle. Collection/merging into one
reproducible snapshot remains phase 2.5.

## Transport, compatibility and tests

The shared transport retains **one attempt by default**. Only the new read
adapters opt into at most two attempts for 429/502/503/504 and GET transport
failures. Exa POST searches can retry explicit transient HTTP responses, but
never ambiguous timeouts/connection failures. Transient-response retries can
still incur provider usage charges. Authentication/schema/JSON/proxy failures
are not retried. The default delay is 250 ms; Retry-After is honored only up to
two seconds. Longer or invalid values abort, rather than retrying early. HTTP
timeouts apply per attempt; these are not hard total-cycle cancellation bounds.
Transaction POSTs and other existing adapters retain one attempt.

Existing helper import paths remain available. `get_token_price` returns only
validated usdPrice/decimals/blockId rows; Decimal prices are JSON strings.
`search_tokens` returns the normalized metadata subset. Empty legacy inputs
still return `{}`/`[]`; these are not valid prices. The Exa wrapper returns
`{"results": [...], "warnings": [...]}` using the NewsArticle field names,
including published_at/received_at. Consumers relying on vendor-specific raw
fields must migrate to the documented normalized subset.

Tests use httpx.MockTransport, injected clocks and block-time resolvers. They
cover auth/payloads, price precision, wrong/missing mints, incomplete schemas,
unreliable timestamps, empty/duplicate news, bounded retry behavior and safe
errors. No fixture is a recorded authenticated API response. See
[fixture provenance](../tests/fixtures/providers/README.md) for pinned sources.
Run the regular unit, runtime, lint and wheel checks in DEVELOPMENT.md.

Before enabling a real feed, verify authenticated read contracts and quotas
with actual configured access; verify any block-time resolver separately.
Neither adapter assesses liquidity, sellability or execution price.
