"""Lazy public tool exports; research imports never load wallet-signing helpers."""

from importlib import import_module

_EXPORTS = {
    "get_ticker_overview": "polygon_api",
    "get_balance_sheets": "polygon_api",
    "get_daily_ticker_summary": "polygon_api",
    "search_tokens": "jupiter_search",
    "get_token_price": "jupiter_price",
    "execute_trade": "ultra_tools",
    "get_order": "ultra_tools",
    "get_holdings": "ultra_tools",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name in _EXPORTS:
        return getattr(import_module(f"{__name__}.{_EXPORTS[name]}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
