"""Compatibility JSON helper for validated Jupiter USD price observations."""

import json

from autohedge.config import load_settings
from autohedge.data.jupiter import JupiterProvider


def get_token_price(ids: str | list[str]) -> str:
    """Get up to 50 mint prices; Decimal prices serialize as strings.

    Values have usdPrice, decimals and blockId. They have no event timestamp
    and cannot alone establish freshness. Empty input retains the legacy '{}'.
    """
    if ids == "" or ids == [] or (isinstance(ids, str) and not ids.strip()):
        return "{}"
    mints = ids.split(",") if isinstance(ids, str) else ids
    rows = JupiterProvider(load_settings()).price_rows(mints)
    return json.dumps({mint: row.model_dump(mode="json") for mint, row in rows.items()})
