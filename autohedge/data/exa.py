"""Read-only Exa news search with explicit publication and receipt provenance."""

from datetime import timedelta

import httpx

from autohedge.config import Settings
from autohedge.contracts import Instrument
from autohedge.data._adapter import read_clock, utc_now
from autohedge.data.contracts import NewsArticle
from autohedge.errors import DataQualityError, ProviderError
from autohedge.providers import request_json


class ExaNewsProvider:
    def __init__(
        self, settings: Settings, *, client: httpx.Client | None = None, clock=utc_now
    ):
        self.settings = settings
        self.client = client
        self.clock = clock

    def fetch_news(self, instrument: Instrument) -> tuple[NewsArticle, ...]:
        instrument = Instrument.model_validate(instrument)
        return self.search_news(
            f"{instrument.symbol} {instrument.asset_id} {instrument.venue} market news"
        )

    def search_news(self, query: str) -> tuple[NewsArticle, ...]:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 5000:
            raise ValueError("Exa query must contain 1 to 5000 characters")
        as_of = read_clock(self.clock, "exa")
        oldest = as_of - timedelta(
            seconds=self.settings.market_quality.max_news_age_seconds
        )
        payload = request_json(
            "exa",
            "POST",
            str(self.settings.exa_base_url).rstrip("/") + "/search",
            settings=self.settings,
            client=self.client,
            headers=self.settings.headers("exa"),
            read_only_retries=True,
            precise=True,
            json={
                "query": query.strip(),
                "type": "auto",
                "category": "news",
                "numResults": 2,
                "startPublishedDate": oldest.isoformat(),
                "endPublishedDate": as_of.isoformat(),
                "contents": {"text": {"maxCharacters": 4000}},
            },
        )
        received_at = read_clock(self.clock, "exa")
        try:
            if received_at < as_of or not isinstance(payload, dict):
                raise ValueError
            rows = payload["results"]
            if not isinstance(rows, list) or len(rows) > 2:
                raise ValueError
            articles = tuple(
                NewsArticle(
                    article_id=row["id"],
                    source="exa.search.news",
                    url=row["url"],
                    published_at=row["publishedDate"],
                    received_at=received_at,
                    title=row["title"],
                    # This is an excerpt, not an independently verified summary.
                    summary=row.get("text", ""),
                )
                for row in rows
            )
            if len({article.article_id for article in articles}) != len(articles):
                raise ValueError
            if len({str(article.url) for article in articles}) != len(articles):
                raise ValueError
            if any(
                not oldest <= article.published_at <= as_of
                or received_at - article.published_at
                > timedelta(seconds=self.settings.market_quality.max_news_age_seconds)
                for article in articles
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise ProviderError("exa", "missing or invalid dated news") from None
        if not articles and self.settings.market_quality.require_news:
            raise DataQualityError("missing_news")
        return articles
