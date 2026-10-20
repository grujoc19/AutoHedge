from datetime import timedelta
import unittest

from pydantic import ValidationError

from autohedge.data.contracts import Candle, Quote
from autohedge.data.quality import parse_market_data
from autohedge.domain import ResearchRequest
from autohedge.errors import DataQualityError
from market_fixtures import bundle, INSTRUMENT, NOW


class MarketContractTests(unittest.TestCase):
    def test_wire_round_trip_preserves_units_provenance_and_precision(self):
        data = bundle(quote_price="100.123456789123456789", news=True)
        parsed = parse_market_data(data.model_dump(mode="json"))
        self.assertEqual(parsed, data)
        self.assertEqual(str(parsed.quote.price), "100.123456789123456789")
        self.assertEqual(parsed.quote.base_decimals, 9)
        self.assertEqual(parsed.history.source, "history-fixture")
        self.assertEqual(parsed.news[0].published_at, NOW - timedelta(minutes=5))

    def test_invalid_ohlcv_and_excessive_precision_are_rejected(self):
        row = bundle().history.candles[0].model_dump()
        for update in (
            {"high": 99},
            {"low": 101},
            {"open": 0},
            {"volume_base": -1},
            {"volume_base": True},
            {"close": "NaN"},
            {"close": "Infinity"},
            {"close": "1e999999"},
            {"volume_base": "1e-1000"},
        ):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                Candle(**{**row, **update})

    def test_token_decimals_and_aware_timestamps_are_required(self):
        quote = bundle().quote.model_dump()
        for update in (
            {"base_decimals": -1},
            {"base_decimals": True},
            {"quote_decimals": "6"},
            {"base_decimals": 31},
            {"event_time": NOW.replace(tzinfo=None)},
            {"event_time": NOW + timedelta(seconds=1)},
        ):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                Quote(**{**quote, **update})

    def test_duplicate_out_of_order_gapped_and_wrong_interval_candles_fail(self):
        for operation in ("duplicate", "reverse", "gap", "interval", "incomplete"):
            payload = bundle().model_dump()
            history = payload["history"]
            bars = list(history["candles"])
            if operation == "duplicate":
                bars[1] = bars[0]
            elif operation == "reverse":
                bars.reverse()
            elif operation == "gap":
                bars.pop(10)
            elif operation == "interval":
                history["interval_seconds"] = 120
            else:
                for bar in bars:
                    bar["open_time"] += timedelta(seconds=30)
                    bar["close_time"] += timedelta(seconds=30)
            history["candles"] = bars
            with (
                self.subTest(operation=operation),
                self.assertRaisesRegex(DataQualityError, "invalid_schema"),
            ):
                parse_market_data(payload)

    def test_mixed_assets_currencies_and_duplicate_news_fail(self):
        for operation in ("asset", "currency", "news_id", "news_url"):
            payload = bundle(news=True).model_dump()
            if operation == "asset":
                payload["history"]["instrument"]["asset_id"] = "different-mint"
            elif operation == "currency":
                payload["history"]["instrument"]["quote_currency"] = "USD"
            else:
                article = dict(payload["news"][0])
                if operation == "news_url":
                    article["article_id"] = "different-id"
                payload["news"] = (*payload["news"], article)
            with self.subTest(operation=operation), self.assertRaises(DataQualityError):
                parse_market_data(payload)

    def test_news_with_credentials_future_publication_or_missing_source_fails(self):
        for update in (
            {"url": "https://user:secret@news.example/story"},
            {"published_at": NOW + timedelta(seconds=1)},
            {"source": " "},
        ):
            payload = bundle(news=True).model_dump()
            payload["news"][0].update(update)
            with self.subTest(update=update), self.assertRaises(DataQualityError):
                parse_market_data(payload)

    def test_api_requires_explicit_target_and_cannot_mix_legacy_and_bundle(self):
        data = bundle()
        with self.assertRaises(ValidationError):
            ResearchRequest(task="research", market_data=data)
        legacy = dict(
            instrument=INSTRUMENT, source="legacy", observed_at=NOW, price="100"
        )
        with self.assertRaises(ValidationError):
            ResearchRequest(
                task="research", instrument=INSTRUMENT, market=legacy, market_data=data
            )

    def test_nonvalidating_model_copy_cannot_bypass_boundary_validation(self):
        invalid = bundle().model_copy(
            update={"quote": bundle().quote.model_copy(update={"price": -1})}
        )
        with self.assertRaises(DataQualityError):
            parse_market_data(invalid)

    def test_safe_schema_error_does_not_echo_provider_payload(self):
        with self.assertRaises(DataQualityError) as error:
            parse_market_data({"provider-body": "private-payload-secret"})
        self.assertEqual(error.exception.code, "invalid_schema")
        self.assertNotIn("private-payload", str(error.exception))
