"""
Jupiter Tokens API V2 – search tokens by symbol, name or mint address.
API docs: https://dev.jup.ag (see /tokens/v2/search).
"""

import json

from loguru import logger

from autohedge.config import load_settings
from autohedge.providers import request_json

JUPITER_TOKENS_BASE = "https://api.jup.ag/tokens/v2"


def search_tokens(query: str) -> str:
    """
    Search for Solana tokens by symbol, name, or mint address via Jupiter Tokens API V2.
    API key is read from JUPITER_API_KEY in .env (https://portal.jup.ag).

    Parameters
    ----------
    query : str
        Search string: token symbol, name, or mint address.
        Use comma-separated mint addresses to look up multiple mints (max 100).
        Symbol/name search returns up to 20 results by default.

    Returns
    -------
    str
        JSON string: array of token info objects (id, name, symbol, icon, decimals,
        holderCount, fdv, mcap, usdPrice, liquidity, organicScore, isVerified, tags,
        etc.). Returns "[]" when query is empty.

    Raises
    ------
    autohedge.errors.ProviderError
        On HTTP/network errors.

    Examples
    --------
    >>> search_tokens("USDC")
    >>> search_tokens("So11111111111111111111111111111111111111112")
    >>> search_tokens("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v,So11111111111111111111111111111111111111112")
    """
    if not query or not query.strip():
        logger.warning("search_tokens: query is empty")
        return "[]"

    settings = load_settings()
    result = request_json(
        "jupiter",
        "GET",
        str(settings.jupiter_base_url).rstrip("/") + "/tokens/v2/search",
        settings=settings,
        params={"query": query.strip()},
        headers=settings.headers("jupiter"),
    )
    return json.dumps(result)
