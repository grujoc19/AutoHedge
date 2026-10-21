import unittest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

import httpx
from pydantic import ValidationError

from autohedge.config import Settings
from autohedge.data.coingecko import CoinGeckoCandleProvider, SOURCE
from autohedge.data.contracts import Candle
from autohedge.errors import ConfigurationError, DataQualityError, ProviderError
from history_fixtures import FIXTURE, INSTRUMENT, MINT, NOW, SETTINGS, payload, request


class HistoryProviderTests(unittest.TestCase):
    def fetch(self, data, req=None, **kwargs):
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
        ) as client:
            return CoinGeckoCandleProvider(
                SETTINGS, client=client, clock=lambda: NOW, **kwargs
            ).fetch_history(req or request())

    def test_http_contract_and_normalized_closed_usd_volume(self):
        def respond(http_request):
            self.assertEqual(http_request.method, "GET")
            self.assertEqual(
                str(http_request.url).split("?")[0],
                f"https://history.example/api/v3/onchain/networks/solana/tokens/{MINT}/ohlcv/minute",
            )
            self.assertEqual(
                http_request.headers["x-cg-pro-api-key"], "history-fixture-secret"
            )
            self.assertEqual(
                http_request.extensions["timeout"]["read"], SETTINGS.request_timeout
            )
            self.assertEqual(
                dict(http_request.url.params),
                {
                    "aggregate": "1",
                    "currency": "usd",
                    "before_timestamp": str(int(NOW.timestamp()) - 1),
                    "limit": "3",
                    "include_empty_intervals": "false",
                    "include_inactive_source": "false",
                },
            )
            return httpx.Response(200, content=FIXTURE.read_bytes())

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            record = CoinGeckoCandleProvider(
                SETTINGS, client=client, clock=lambda: NOW
            ).fetch_history(request())
        self.assertEqual(len(record.series.candles), 3)
        self.assertEqual(record.series.candles[0].open_time, NOW - timedelta(minutes=3))
        self.assertEqual(record.series.candles[-1].close_time, NOW)
        self.assertEqual(record.series.received_at, NOW)
        self.assertEqual(record.series.source, SOURCE)
        self.assertEqual(record.series.instrument, INSTRUMENT)
        self.assertIsNone(record.series.candles[0].volume_base)
        self.assertEqual(record.series.candles[0].volume_quote, Decimal("1500.25"))
        self.assertEqual(record.pages[0].series, record.series)

    def test_decimal_json_does_not_round_price_or_volume(self):
        wire = (
            FIXTURE.read_text()
            .replace("1500.25", "1500.12345678901234567890123456789")
            .replace("101,", "100.12345678901234567890123456789,")
        )
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=wire))
        ) as client:
            series = CoinGeckoCandleProvider(
                SETTINGS, client=client, clock=lambda: NOW
            ).fetch_candles(request())
        self.assertEqual(
            series.candles[-1].close, Decimal("100.12345678901234567890123456789")
        )
        self.assertEqual(
            series.candles[-1].volume_quote,
            Decimal("1500.12345678901234567890123456789"),
        )

    def test_pagination_excludes_partial_interval_and_accepts_identical_overlap(self):
        cursors = []
        responses = iter([payload((1, 2)), payload((2, 3))])

        def respond(http_request):
            cursors.append(int(http_request.url.params["before_timestamp"]))
            return httpx.Response(200, json=next(responses))

        clock = Mock(
            side_effect=[
                NOW + timedelta(seconds=30),
                NOW + timedelta(seconds=31),
                NOW + timedelta(seconds=32),
            ]
        )
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            record = CoinGeckoCandleProvider(
                SETTINGS, client=client, clock=clock, page_size=2
            ).fetch_history(request(as_of=NOW + timedelta(seconds=30)))
        self.assertEqual(
            cursors, [int(NOW.timestamp()) - 1, int(NOW.timestamp()) - 120]
        )
        self.assertEqual(len(record.pages), 2)
        self.assertEqual(len(record.series.candles), 3)
        self.assertEqual(record.series.candles[-1].close_time, NOW)
        self.assertEqual(record.series.received_at, NOW + timedelta(seconds=32))
        self.assertEqual(
            record.pages[0].series.received_at, NOW + timedelta(seconds=31)
        )

    def test_nonoverlapping_pages_and_older_extras_form_exact_requested_window(self):
        responses = iter([payload((1, 2)), payload((3, 4))])
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=next(responses))
            )
        ) as client:
            record = CoinGeckoCandleProvider(
                SETTINGS, client=client, clock=lambda: NOW, page_size=2
            ).fetch_history(request())
        self.assertEqual(len(record.series.candles), 3)
        self.assertEqual(sum(len(page.series.candles) for page in record.pages), 4)

    def test_changed_overlap_is_rejected_instead_of_last_write_wins(self):
        first, second = payload((1, 2)), payload((2, 3))
        second["data"]["attributes"]["ohlcv_list"][0][5] = 1
        responses = iter([first, second])
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=next(responses))
            )
        ) as client:
            with self.assertRaisesRegex(DataQualityError, "conflicting_candles"):
                CoinGeckoCandleProvider(
                    SETTINGS, client=client, clock=lambda: NOW, page_size=2
                ).fetch_history(request())

    def test_gaps_stalled_pages_empty_pages_and_page_budget_fail_closed(self):
        scenarios = (
            ([payload((1, 2)), payload((4, 5))], 5, 10, "invalid_history"),
            ([payload((1, 2)), payload((2,))], 3, 10, "pagination_stalled"),
            ([payload((1, 2)), payload(())], 3, 10, "insufficient_candles"),
            ([payload((1, 2))], 3, 1, "page_budget"),
        )
        for pages, count, budget, error in scenarios:
            with self.subTest(error=error):
                responses = iter(pages)
                with httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _: httpx.Response(200, json=next(responses))
                    )
                ) as client:
                    with self.assertRaisesRegex(DataQualityError, error):
                        CoinGeckoCandleProvider(
                            SETTINGS,
                            client=client,
                            clock=lambda: NOW,
                            page_size=2,
                            max_pages=budget,
                        ).fetch_history(request(count))

    def test_bad_identity_schema_order_interval_and_amounts_are_rejected(self):
        bad_payloads = [
            None,
            {},
            {"meta": None},
            payload((1, 3)),
            payload((1, 1)),
            payload((2, 1)),
        ]
        other = payload()
        other["meta"]["base"]["address"] = "11111111111111111111111111111111"
        bad_payloads.append(other)
        for field, value in (
            (0, True),
            (0, 1.0),
            (0, -1),
            (0, 2**100),
            (1, True),
            (1, "100"),
            (2, 98),
            (3, 103),
            (4, 0),
            (5, -1),
            (5, None),
        ):
            data = payload()
            data["data"]["attributes"]["ohlcv_list"][0][field] = value
            bad_payloads.append(data)
        data = payload()
        data["data"]["attributes"]["ohlcv_list"][0][0] += 1
        bad_payloads.append(data)
        data = payload()
        data["data"]["attributes"]["ohlcv_list"][0].pop()
        bad_payloads.append(data)
        for data in bad_payloads:
            with (
                self.subTest(data=data),
                self.assertRaises((ProviderError, DataQualityError)) as error,
            ):
                self.fetch(data)
            self.assertNotIn("history-fixture-secret", str(error.exception))

    def test_current_candle_and_missing_latest_completed_candle_fail(self):
        for data in (payload((0, 1, 2)), payload((2, 3, 4))):
            with (
                self.subTest(data=data),
                self.assertRaises((ProviderError, DataQualityError)),
            ):
                self.fetch(data)
        # A later receipt does not make a candle beyond the requested as_of valid.
        with self.assertRaises(ProviderError):
            self.fetch(payload(), request(as_of=NOW - timedelta(minutes=1)))

    def test_supported_intervals_use_documented_timeframe_and_aggregate(self):
        for interval, timeframe, aggregate in (
            (60, "minute", "1"),
            (300, "minute", "5"),
            (900, "minute", "15"),
            (3600, "hour", "1"),
            (14400, "hour", "4"),
            (43200, "hour", "12"),
            (86400, "day", "1"),
        ):
            # NOW=12:00 is not a day boundary; derive the completed grid explicitly.
            end = NOW - timedelta(seconds=int(NOW.timestamp()) % interval)

            def respond(http_request):
                self.assertTrue(http_request.url.path.endswith("/" + timeframe))
                self.assertEqual(http_request.url.params["aggregate"], aggregate)
                return httpx.Response(
                    200, json=payload((1,), interval=interval, end=end)
                )

            with (
                self.subTest(interval=interval),
                httpx.Client(transport=httpx.MockTransport(respond)) as client,
            ):
                record = CoinGeckoCandleProvider(
                    SETTINGS, client=client, clock=lambda: NOW
                ).fetch_history(request(1, interval))
            self.assertEqual(record.series.candles[-1].close_time, end)

    def test_invalid_configuration_requests_and_future_time_never_send(self):
        respond = Mock(return_value=httpx.Response(200, json=payload()))
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            adapter = CoinGeckoCandleProvider(
                SETTINGS, client=client, clock=lambda: NOW
            )
            for change in (
                {"quote_currency": "USDC"},
                {"venue": "ethereum"},
                {"asset_id": "mint"},
            ):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    adapter.fetch_history(
                        request().model_copy(
                            update={"instrument": INSTRUMENT.model_copy(update=change)}
                        )
                    )
            with self.assertRaises(ValueError):
                adapter.fetch_history(request(interval=120))
            with self.assertRaisesRegex(DataQualityError, "future_history_request"):
                adapter.fetch_history(request(as_of=NOW + timedelta(seconds=1)))
            with self.assertRaises(ConfigurationError):
                CoinGeckoCandleProvider(
                    Settings(), client=client, clock=lambda: NOW
                ).fetch_history(request())
        respond.assert_not_called()
        for kwargs in (
            {"page_size": 1},
            {"page_size": 1001},
            {"page_size": True},
            {"max_pages": 0},
            {"max_pages": 21},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CoinGeckoCandleProvider(SETTINGS, **kwargs)

    def test_transport_failure_does_not_return_partial_history(self):
        responses = iter(
            [
                httpx.Response(200, json=payload((1, 2))),
                httpx.Response(401, text="history-fixture-secret"),
            ]
        )
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: next(responses))
        ) as client:
            with self.assertRaises(ProviderError) as error:
                CoinGeckoCandleProvider(
                    SETTINGS, client=client, clock=lambda: NOW, page_size=2
                ).fetch_history(request())
        self.assertEqual(error.exception.status_code, 401)
        self.assertNotIn("history-fixture-secret", str(error.exception))

    def test_clock_regression_and_naive_clock_are_rejected(self):
        for clock in (
            lambda: NOW.replace(tzinfo=None),
            Mock(side_effect=[NOW, NOW - timedelta(seconds=1)]),
        ):
            with httpx.Client(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(200, json=payload())
                )
            ) as client:
                with self.assertRaises(ProviderError):
                    CoinGeckoCandleProvider(
                        SETTINGS, client=client, clock=clock
                    ).fetch_history(request())

    def test_volume_units_require_evidence_and_never_infer_base_volume(self):
        candle = self.fetch(payload()).series.candles[0]
        self.assertIsNone(candle.volume_base)
        self.assertEqual(candle.volume_quote, Decimal("1500.25"))
        for update in (
            {"volume_quote": None},
            {"volume_quote": -1},
            {"volume_quote": True},
        ):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                Candle.model_validate(candle.model_copy(update=update))
        # Legacy explicit base volume remains accepted.
        base = Candle.model_validate(
            candle.model_copy(update={"volume_base": "1", "volume_quote": None})
        )
        self.assertEqual(base.volume_base, Decimal("1"))


if __name__ == "__main__":
    unittest.main()
