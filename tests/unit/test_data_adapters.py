"""Provider contracts tested against local schema-shaped HTTP fixtures, never live APIs."""

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from autohedge.config import Settings
from autohedge.contracts import Instrument
from autohedge.data.contracts import QualityPolicy
from autohedge.data.exa import ExaNewsProvider
from autohedge.data.jupiter import JupiterProvider, validate_mint
from autohedge.errors import DataQualityError, ProviderError
from autohedge.providers import request_json
from autohedge.tools.exa_search_tool import exa_search
from autohedge.tools.jupiter_price import get_token_price
from autohedge.tools.jupiter_search import search_tokens

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers"
MINT = "So11111111111111111111111111111111111111112"
OTHER_MINT = "11111111111111111111111111111111"
NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
SETTINGS = Settings(jupiter_api_key="fixture-key", exa_api_key="fixture-key")
SOL = Instrument(asset_id=MINT, symbol="SOL", venue="solana", quote_currency="USD")


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def client_for(payload):
    return httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )


class TransportRetryTests(unittest.TestCase):
    def call(self, handler, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return request_json(
                "fixture",
                kwargs.pop("method", "GET"),
                "https://fixture.example/private?token=fixture-secret",
                settings=SETTINGS,
                client=client,
                **kwargs,
            )

    def test_read_retry_is_bounded_and_honors_short_retry_after(self):
        for status in (429, 502, 503, 504):
            with self.subTest(status=status):
                respond = Mock(
                    side_effect=[
                        httpx.Response(status, headers={"Retry-After": "1"}),
                        httpx.Response(200, json={"ok": True}),
                    ]
                )
                sleep = Mock()
                self.assertEqual(
                    self.call(respond, read_only_retries=True, sleep=sleep),
                    {"ok": True},
                )
                self.assertEqual(respond.call_count, 2)
                sleep.assert_called_once_with(1)
        respond = Mock(return_value=httpx.Response(503))
        with self.assertRaises(ProviderError):
            self.call(respond, read_only_retries=True, sleep=Mock())
        self.assertEqual(respond.call_count, 2)

    def test_long_invalid_or_nonfinite_retry_after_does_not_retry_early(self):
        for header in (
            "60",
            "NaN",
            "Infinity",
            "invalid",
            "-1",
            "Wed, 21 Oct 2099 12:00:00 GMT",
        ):
            with self.subTest(header=header):
                respond = Mock(
                    return_value=httpx.Response(429, headers={"Retry-After": header})
                )
                sleep = Mock()
                with self.assertRaises(ProviderError):
                    self.call(respond, read_only_retries=True, sleep=sleep)
                self.assertEqual(respond.call_count, 1)
                sleep.assert_not_called()

    def test_auth_json_and_proxy_errors_are_never_retried(self):
        responses = (
            httpx.Response(401, text="fixture-secret"),
            httpx.Response(403, text="fixture-secret"),
            httpx.Response(200, text="not-json fixture-secret"),
            httpx.Response(200, text='{"price": NaN}'),
            httpx.Response(200, text='{"price":1,"price":2}'),
            httpx.Response(200, text='{"price":1e999999999999999999999999999999}'),
        )
        for response in responses:
            respond = Mock(return_value=response)
            with self.assertRaises(ProviderError) as error:
                self.call(respond, read_only_retries=True, precise=True, sleep=Mock())
            self.assertEqual(respond.call_count, 1)
            self.assertNotIn("fixture-secret", str(error.exception))
        respond = Mock(side_effect=httpx.ProxyError("fixture-secret"))
        with self.assertRaises(ProviderError):
            self.call(respond, read_only_retries=True, sleep=Mock())
        self.assertEqual(respond.call_count, 1)

    def test_get_timeout_retries_but_ambiguous_post_timeout_does_not(self):
        for method, attempts in (("GET", 2), ("POST", 1)):
            respond = Mock(side_effect=httpx.ReadTimeout("fixture-secret"))
            with self.assertRaisesRegex(ProviderError, "timed out"):
                self.call(respond, method=method, read_only_retries=True, sleep=Mock())
            self.assertEqual(respond.call_count, attempts)

    def test_post_search_can_retry_status_but_default_transaction_cannot(self):
        respond = Mock(side_effect=[httpx.Response(503), httpx.Response(200, json={})])
        self.call(respond, method="POST", read_only_retries=True, sleep=Mock())
        self.assertEqual(respond.call_count, 2)
        respond = Mock(return_value=httpx.Response(503))
        with self.assertRaises(ProviderError):
            self.call(respond, method="POST", sleep=Mock())
        self.assertEqual(respond.call_count, 1)

    def test_decimal_json_does_not_round_through_float(self):
        result = self.call(
            lambda request: httpx.Response(
                200, content=b'{"price":0.12345678901234567890123456789}'
            ),
            precise=True,
        )
        self.assertEqual(result["price"], Decimal("0.12345678901234567890123456789"))


class JupiterAdapterTests(unittest.TestCase):
    def test_http_contract_and_price_observation_have_no_invented_event_time(self):
        def respond(request):
            self.assertEqual(request.url.path, "/price/v3")
            self.assertEqual(request.url.params["ids"], MINT)
            self.assertEqual(request.headers["x-api-key"], "fixture-key")
            return httpx.Response(
                200, content=(FIXTURES / "jupiter-price.json").read_bytes()
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            observation = JupiterProvider(
                SETTINGS, client=client, clock=lambda: NOW
            ).fetch_price(SOL)
        self.assertIsNone(observation.event_time)
        self.assertEqual(observation.received_at, NOW)
        self.assertEqual(observation.block_id, 312345678)
        self.assertEqual(
            observation.price, Decimal("152.12345678901234567890123456789012345678")
        )
        self.assertEqual(observation.base_decimals, 9)

    def test_quote_requires_explicit_block_time_not_created_at(self):
        with client_for(fixture("jupiter-price.json")) as client:
            adapter = JupiterProvider(SETTINGS, client=client, clock=lambda: NOW)
            with self.assertRaisesRegex(
                ProviderError, "requires a block-time resolver"
            ):
                adapter.fetch_quote(SOL)
            resolver = Mock(return_value=NOW - timedelta(seconds=5))
            adapter.block_time_resolver = resolver
            quote = adapter.fetch_quote(SOL)
            resolver.assert_called_once_with(312345678)
            self.assertEqual(quote.event_time, NOW - timedelta(seconds=5))
            self.assertEqual(quote.quote_decimals, 2)
            self.assertEqual(quote.instrument.quote_currency, "USD")

    def test_future_naive_stale_and_failed_block_times_are_rejected(self):
        for event_time in (
            NOW + timedelta(seconds=1),
            NOW.replace(tzinfo=None),
            NOW - timedelta(minutes=2),
        ):
            with (
                self.subTest(event_time=event_time),
                client_for(fixture("jupiter-price.json")) as client,
            ):
                adapter = JupiterProvider(
                    SETTINGS,
                    client=client,
                    clock=lambda: NOW,
                    block_time_resolver=lambda block: event_time,
                )
                with self.assertRaises((ProviderError, DataQualityError)):
                    adapter.fetch_quote(SOL)
        with client_for(fixture("jupiter-price.json")) as client:
            adapter = JupiterProvider(
                SETTINGS,
                client=client,
                clock=lambda: NOW,
                block_time_resolver=Mock(side_effect=RuntimeError("fixture-secret")),
            )
            with self.assertRaisesRegex(ProviderError, "lookup failed") as error:
                adapter.fetch_quote(SOL)
            self.assertNotIn("fixture-secret", str(error.exception))

    def test_missing_block_never_uses_creation_or_receipt_time(self):
        for block in (None, "absent"):
            payload = fixture("jupiter-price.json")
            if block == "absent":
                payload[MINT].pop("blockId")
            else:
                payload[MINT]["blockId"] = block
            with client_for(payload) as client:
                adapter = JupiterProvider(
                    SETTINGS,
                    client=client,
                    clock=lambda: NOW,
                    block_time_resolver=Mock(),
                )
                self.assertIsNone(adapter.fetch_price(SOL).event_time)
                with self.assertRaisesRegex(ProviderError, "block ID unavailable"):
                    adapter.fetch_quote(SOL)
                adapter.block_time_resolver.assert_not_called()

    def test_missing_unexpected_and_malformed_price_rows_fail_closed(self):
        base = fixture("jupiter-price.json")
        payloads = [{}, [], {OTHER_MINT: base[MINT]}, {**base, OTHER_MINT: base[MINT]}]
        for field, value in (
            ("usdPrice", 0),
            ("usdPrice", -1),
            ("usdPrice", True),
            ("usdPrice", "NaN"),
            ("usdPrice", "Infinity"),
            ("decimals", True),
            ("decimals", 31),
            ("blockId", True),
            ("blockId", -1),
        ):
            payload = copy.deepcopy(base)
            payload[MINT][field] = value
            payloads.append(payload)
        for payload in payloads:
            with self.subTest(payload=payload), client_for(payload) as client:
                with self.assertRaises(ProviderError):
                    JupiterProvider(SETTINGS, client=client).fetch_price(SOL)

    def test_usd_is_not_usdc_and_invalid_requests_never_send(self):
        with client_for({}) as client:
            adapter = JupiterProvider(SETTINGS, client=client)
            with self.assertRaisesRegex(ValueError, "currency USD"):
                adapter.fetch_price(SOL.model_copy(update={"quote_currency": "USDC"}))
            for mints in (
                [],
                [MINT] * 51,
                [MINT, MINT],
                ["mint"],
                ["0" * 44],
                ["1" * 33],
            ):
                with self.subTest(mints=mints), self.assertRaises(ValueError):
                    adapter.price_rows(mints)
        self.assertEqual(validate_mint(MINT), MINT)
        self.assertEqual(validate_mint(OTHER_MINT), OTHER_MINT)

    def test_search_preserves_ambiguous_symbols_without_promoting_metadata_price(self):
        payload = fixture("jupiter-tokens.json")
        payload.append({**payload[0], "id": OTHER_MINT})
        with client_for(payload) as client:
            tokens = JupiterProvider(SETTINGS, client=client).search_tokens("SOL")
        self.assertEqual(len(tokens), 2)
        self.assertNotEqual(tokens[0].id, tokens[1].id)
        self.assertNotIn("usdPrice", tokens[0].model_dump())
        self.assertNotIn("updatedAt", tokens[0].model_dump())
        self.assertFalse(hasattr(JupiterProvider, "fetch_candles"))

    def test_exact_mint_search_cannot_return_another_identity(self):
        row = fixture("jupiter-tokens.json")[0]
        with client_for([{**row, "id": OTHER_MINT}]) as client:
            with self.assertRaises(ProviderError):
                JupiterProvider(SETTINGS, client=client).search_tokens(MINT)

    def test_search_empty_duplicate_bad_schema_and_query_bounds(self):
        with client_for([]) as client:
            self.assertEqual(
                JupiterProvider(SETTINGS, client=client).search_tokens("SOL"), ()
            )
        row = fixture("jupiter-tokens.json")[0]
        for payload in (
            {},
            [row, row],
            [{**row, "id": "invalid"}],
            [{**row, "decimals": "9"}],
            [None],
        ):
            with self.subTest(payload=payload), client_for(payload) as client:
                with self.assertRaises(ProviderError):
                    JupiterProvider(SETTINGS, client=client).search_tokens("SOL")
        for query in ("", " ", "a" * 5001, ",".join([MINT] * 101), "invalid,mint"):
            with self.assertRaises(ValueError):
                JupiterProvider(SETTINGS).search_tokens(query)

    def test_legacy_helpers_delegate_validation_and_keep_empty_behavior(self):
        self.assertEqual(get_token_price([]), "{}")
        self.assertEqual(search_tokens(" "), "[]")
        with client_for(fixture("jupiter-price.json")) as client:
            adapter = JupiterProvider(SETTINGS, client=client)
            with patch(
                "autohedge.tools.jupiter_price.JupiterProvider", return_value=adapter
            ):
                row = json.loads(get_token_price(MINT))[MINT]
                self.assertIsInstance(row["usdPrice"], str)
                self.assertEqual(row["decimals"], 9)
        with client_for(fixture("jupiter-tokens.json")) as client:
            with patch(
                "autohedge.tools.jupiter_search.JupiterProvider",
                return_value=JupiterProvider(SETTINGS, client=client),
            ):
                self.assertEqual(json.loads(search_tokens("SOL"))[0]["id"], MINT)


class ExaAdapterTests(unittest.TestCase):
    def test_http_contract_normalizes_dated_text_with_explicit_search_window(self):
        def respond(request):
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/search")
            self.assertEqual(request.headers["x-api-key"], "fixture-key")
            payload = json.loads(request.content)
            self.assertEqual(payload["contents"], {"text": {"maxCharacters": 4000}})
            self.assertEqual(payload["category"], "news")
            self.assertEqual(payload["numResults"], 2)
            self.assertIn(MINT, payload["query"])
            self.assertEqual(payload["endPublishedDate"], NOW.isoformat())
            self.assertEqual(
                payload["startPublishedDate"], (NOW - timedelta(days=1)).isoformat()
            )
            return httpx.Response(200, json=fixture("exa-news.json"))

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            article = ExaNewsProvider(
                SETTINGS, client=client, clock=lambda: NOW
            ).fetch_news(SOL)[0]
        self.assertEqual(article.source, "exa.search.news")
        self.assertEqual(article.published_at, NOW - timedelta(minutes=30))
        self.assertEqual(article.received_at, NOW)
        self.assertEqual(
            article.summary, fixture("exa-news.json")["results"][0]["text"]
        )

    def test_missing_naive_stale_future_and_invalid_news_do_not_become_evidence(self):
        base = fixture("exa-news.json")
        payloads = [{}, [], {"results": {}}, {"results": [None]}]
        for key, value in (
            ("publishedDate", None),
            ("publishedDate", "2026-10-21T11:30:00"),
            ("publishedDate", "2026-10-21T12:00:01Z"),
            ("publishedDate", "2026-10-19T12:00:00Z"),
            ("url", "https://user:password@news.example"),
            ("title", " "),
            ("text", "a" * 4001),
        ):
            payload = copy.deepcopy(base)
            payload["results"][0][key] = value
            payloads.append(payload)
        missing = copy.deepcopy(base)
        missing["results"][0].pop("publishedDate")
        payloads.append(missing)
        payloads.append({"results": base["results"] * 2})
        for payload in payloads:
            with self.subTest(payload=payload), client_for(payload) as client:
                with self.assertRaisesRegex(ProviderError, "invalid dated news"):
                    ExaNewsProvider(
                        SETTINGS, client=client, clock=lambda: NOW
                    ).search_news("SOL")

    def test_empty_news_respects_policy_and_tool_reports_missing_sources(self):
        with client_for({"results": []}) as client:
            adapter = ExaNewsProvider(SETTINGS, client=client, clock=lambda: NOW)
            self.assertEqual(adapter.search_news("SOL"), ())
            with patch(
                "autohedge.tools.exa_search_tool.ExaNewsProvider", return_value=adapter
            ):
                result = json.loads(exa_search("SOL", settings=SETTINGS))
            self.assertEqual(result["results"], [])
            self.assertTrue(result["warnings"])
            adapter.settings = SETTINGS.model_copy(
                update={"market_quality": QualityPolicy(require_news=True)}
            )
            with self.assertRaisesRegex(DataQualityError, "missing_news"):
                adapter.search_news("SOL")

    def test_tool_serializes_normalized_news_and_keeps_provider_errors(self):
        with client_for(fixture("exa-news.json")) as client:
            adapter = ExaNewsProvider(SETTINGS, client=client, clock=lambda: NOW)
            with patch(
                "autohedge.tools.exa_search_tool.ExaNewsProvider", return_value=adapter
            ):
                result = json.loads(exa_search("SOL", settings=SETTINGS))
            self.assertEqual(
                result["results"][0]["published_at"], "2026-10-21T11:30:00Z"
            )
            self.assertEqual(result["warnings"], [])
        with client_for({"results": [{}]}) as client:
            adapter = ExaNewsProvider(SETTINGS, client=client, clock=lambda: NOW)
            with patch(
                "autohedge.tools.exa_search_tool.ExaNewsProvider", return_value=adapter
            ):
                with self.assertRaises(ProviderError):
                    exa_search("SOL", settings=SETTINGS)

    def test_clocks_and_query_bounds_are_enforced(self):
        for query in ("", " ", "a" * 5001, None):
            with self.assertRaises(ValueError):
                ExaNewsProvider(SETTINGS).search_news(query)
        with self.assertRaises(ProviderError):
            ExaNewsProvider(
                SETTINGS, clock=lambda: NOW.replace(tzinfo=None)
            ).search_news("SOL")
        with client_for(fixture("exa-news.json")) as client:
            clock = Mock(side_effect=[NOW, NOW - timedelta(seconds=1)])
            with self.assertRaises(ProviderError):
                ExaNewsProvider(SETTINGS, client=client, clock=clock).search_news("SOL")


if __name__ == "__main__":
    unittest.main()
