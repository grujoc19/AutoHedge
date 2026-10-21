"""Compatibility tool backed by validated, dated Exa news results."""

import json

from autohedge.config import Settings, load_settings
from autohedge.data.exa import ExaNewsProvider


def exa_search(query: str, *, settings: Settings | None = None) -> str:
    """Return dated news excerpts, or explicitly report that no sources exist."""
    settings = settings if settings is not None else load_settings()
    articles = ExaNewsProvider(settings).search_news(query)
    return json.dumps(
        {
            "results": [article.model_dump(mode="json") for article in articles],
            "warnings": [] if articles else ["No dated news sources returned"],
        }
    )
