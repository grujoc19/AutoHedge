"""Compatibility JSON helper for validated Jupiter token discovery metadata."""

import json

from autohedge.config import load_settings
from autohedge.data.jupiter import JupiterProvider


def search_tokens(query: str) -> str:
    """Return id, name, symbol and decimals. Symbols are not unique identities."""
    if isinstance(query, str) and not query.strip():
        return "[]"
    tokens = JupiterProvider(load_settings()).search_tokens(query)
    return json.dumps([token.model_dump(mode="json") for token in tokens])
