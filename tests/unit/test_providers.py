import json
import unittest
from unittest.mock import patch

import httpx

from autohedge.config import Settings
from autohedge.errors import ProviderError
from autohedge.providers import request_json
from autohedge.tools.exa_search_tool import exa_search
from autohedge.tools.polygon_api import get_ticker_overview


class ProviderTests(unittest.TestCase):
    def test_auth_base_url_payload_and_timeout_from_settings(self):
        settings = Settings(
            exa_api_key="fixture-secret",
            exa_base_url="https://fixture.example",
            request_timeout=7,
        )

        def respond(request):
            self.assertEqual(str(request.url), "https://fixture.example/search")
            self.assertEqual(request.headers["x-api-key"], "fixture-secret")
            self.assertEqual(request.extensions["timeout"]["read"], 7)
            payload = json.loads(request.content)
            self.assertEqual(payload["contents"]["text"]["maxCharacters"], 4000)
            return httpx.Response(200, json={"results": []})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            from autohedge import providers

            def local(*args, **kwargs):
                kwargs["client"] = client
                return providers.request_json(*args, **kwargs)

            with patch("autohedge.data.exa.request_json", side_effect=local):
                self.assertEqual(
                    json.loads(exa_search("query", settings=settings))["results"], []
                )

    def test_http_timeout_and_invalid_json_errors_are_safe(self):
        for status, content in [
            (401, b"Authorization: Bearer secret-response"),
            (200, b"not-json"),
        ]:
            with (
                self.subTest(status=status),
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(status, content=content)
                    )
                ) as client,
            ):
                with self.assertRaises(ProviderError) as error:
                    request_json(
                        "exa",
                        "GET",
                        "https://fixture.example?token=secret-url",
                        settings=Settings(),
                        client=client,
                    )
                self.assertNotIn("secret", str(error.exception))
                self.assertEqual(
                    error.exception.status_code, status if status != 200 else None
                )

        def timeout(request):
            raise httpx.ReadTimeout("secret-response", request=request)

        with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
            with self.assertRaisesRegex(ProviderError, "timed out") as error:
                request_json(
                    "exa",
                    "GET",
                    "https://fixture.example",
                    settings=Settings(),
                    client=client,
                )
            self.assertNotIn("secret", str(error.exception))

    def test_existing_read_adapters_use_shared_configuration(self):
        settings = Settings(
            jupiter_api_key="jupiter-fixture",
            massive_api_key="massive-fixture",
            massive_base_url="https://stocks.example",
        )
        with (
            patch("autohedge.tools.polygon_api.load_settings", return_value=settings),
            patch(
                "autohedge.tools.polygon_api.request_json", return_value={"results": {}}
            ) as request,
        ):
            get_ticker_overview("NVDA")
            self.assertEqual(
                request.call_args.args[2],
                "https://stocks.example/v3/reference/tickers/NVDA",
            )
