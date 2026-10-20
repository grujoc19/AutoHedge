"""Exa search adapter with the same settings and error contract as other APIs."""

import json

from autohedge.config import Settings, load_settings
from autohedge.providers import request_json


def exa_search(query: str, *, settings: Settings | None = None) -> str:
    """Search Exa for current sources. Provider failures abort required research."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Exa query must not be empty")
    settings = settings if settings is not None else load_settings()
    result = request_json(
        "exa",
        "POST",
        str(settings.exa_base_url).rstrip("/") + "/search",
        settings=settings,
        headers=settings.headers("exa"),
        json={
            "query": query.strip(),
            "type": "auto",
            "numResults": 2,
            "contents": {"text": True, "context": {"maxCharacters": 4000}},
        },
    )
    return json.dumps(result)
