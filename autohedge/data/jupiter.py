"""Jupiter metadata and USD prices; receipt time never implies price freshness."""

from datetime import datetime
from typing import Annotated, Callable, Literal

import httpx
from pydantic import AwareDatetime, ConfigDict, Field, StrictInt, ValidationError

from autohedge.config import Settings
from autohedge.contracts import Contract, Instrument, Text
from autohedge.data._adapter import read_clock, utc_now
from autohedge.data.contracts import DataPrice, Decimals, Quote
from autohedge.data.quality import validate_price_time
from autohedge.errors import ProviderError
from autohedge.providers import request_json

# Solana public keys are exactly 32 bytes, base58-encoded without leading zero loss.
_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def validate_mint(mint: str) -> str:
    if not isinstance(mint, str) or not 32 <= len(mint) <= 44:
        raise ValueError("Expected a Solana mint address")
    value = 0
    for char in mint:
        if char not in _ALPHABET:
            raise ValueError("Expected a Solana mint address")
        value = value * 58 + _ALPHABET.index(char)
    size = (value.bit_length() + 7) // 8 + len(mint) - len(mint.lstrip("1"))
    if size != 32:
        raise ValueError("Expected a 32-byte Solana mint address")
    return mint


class _PriceRow(Contract):
    model_config = ConfigDict(extra="ignore")
    usdPrice: DataPrice
    decimals: Decimals
    blockId: Annotated[StrictInt, Field(ge=0, le=2**64 - 1)] | None = None


class TokenInfo(Contract):
    """Discovery metadata only; a matching symbol does not identify a token."""

    id: Text
    name: Text
    symbol: Text
    decimals: Decimals


class PriceObservation(Contract):
    instrument: Instrument
    source: Literal["jupiter.price.v3"] = "jupiter.price.v3"
    received_at: AwareDatetime
    price: DataPrice
    base_decimals: Decimals
    block_id: Annotated[StrictInt, Field(ge=0, le=2**64 - 1)] | None
    event_time: None = None


class JupiterProvider:
    """Read-only adapter. A trusted block-time resolver is required for Quote."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        clock=utc_now,
        block_time_resolver: Callable[[int], datetime] | None = None,
    ):
        self.settings = settings
        self.client = client
        self.clock = clock
        self.block_time_resolver = block_time_resolver

    def _get(self, path, params):
        return request_json(
            "jupiter",
            "GET",
            str(self.settings.jupiter_base_url).rstrip("/") + path,
            settings=self.settings,
            client=self.client,
            headers=self.settings.headers("jupiter"),
            params=params,
            read_only_retries=True,
            precise=True,
        )

    def search_tokens(self, query: str) -> tuple[TokenInfo, ...]:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 5000:
            raise ValueError("Token query must contain 1 to 5000 characters")
        query = query.strip()
        requested_mints = None
        if "," in query:
            mints = query.split(",")
            if len(mints) > 100:
                raise ValueError("At most 100 mints per search")
            for mint in mints:
                validate_mint(mint)
            requested_mints = set(mints)
        else:
            try:
                requested_mints = {validate_mint(query)}
            except ValueError:
                pass  # Name/symbol discovery may intentionally return multiple mints.
        payload = self._get("/tokens/v2/search", {"query": query})
        try:
            if not isinstance(payload, list) or len(payload) > 100:
                raise ValueError
            tokens = tuple(
                TokenInfo.model_validate(
                    {key: row[key] for key in ("id", "name", "symbol", "decimals")}
                )
                for row in payload
            )
            for token in tokens:
                validate_mint(token.id)
            identities = {token.id for token in tokens}
            if len(identities) != len(tokens):
                raise ValueError
            if requested_mints is not None and not identities <= requested_mints:
                raise ValueError
            return tokens
        except (ValidationError, ValueError, TypeError, KeyError):
            raise ProviderError("jupiter", "invalid token metadata") from None

    def price_rows(self, mints: list[str]) -> dict[str, _PriceRow]:
        if not isinstance(mints, list) or not 1 <= len(mints) <= 50:
            raise ValueError("Request between 1 and 50 mint addresses")
        for mint in mints:
            validate_mint(mint)
        if len(set(mints)) != len(mints):
            raise ValueError("Duplicate requested mints")
        payload = self._get("/price/v3", {"ids": ",".join(mints)})
        try:
            if not isinstance(payload, dict) or set(payload) != set(mints):
                raise ValueError
            return {mint: _PriceRow.model_validate(payload[mint]) for mint in mints}
        except (ValidationError, ValueError, TypeError):
            raise ProviderError(
                "jupiter", "missing or invalid requested prices"
            ) from None

    def fetch_price(self, instrument: Instrument) -> PriceObservation:
        instrument = Instrument.model_validate(instrument)
        if instrument.venue != "solana" or instrument.quote_currency != "USD":
            raise ValueError(
                "Jupiter prices require venue solana and quote currency USD"
            )
        row = self.price_rows([instrument.asset_id])[instrument.asset_id]
        return PriceObservation(
            instrument=instrument,
            received_at=read_clock(self.clock, "jupiter"),
            price=row.usdPrice,
            base_decimals=row.decimals,
            block_id=row.blockId,
        )

    def fetch_quote(self, instrument: Instrument) -> Quote:
        if self.block_time_resolver is None:
            raise ProviderError(
                "jupiter", "price event time requires a block-time resolver"
            )
        observation = self.fetch_price(instrument)
        if observation.block_id is None:
            raise ProviderError("jupiter", "price block ID unavailable")
        try:
            event_time = self.block_time_resolver(observation.block_id)
        except Exception:
            raise ProviderError("jupiter", "price block-time lookup failed") from None
        try:
            quote = Quote(
                instrument=instrument,
                source=observation.source,
                received_at=observation.received_at,
                event_time=event_time,
                price=observation.price,
                base_decimals=observation.base_decimals,
                quote_decimals=2,  # USD minor units; prices are not rounded to cents.
            )
        except ValidationError:
            raise ProviderError("jupiter", "invalid price block time") from None
        validate_price_time(
            quote.event_time,
            received_at=quote.received_at,
            now=read_clock(self.clock, "jupiter"),
            policy=self.settings.market_quality,
        )
        return quote
