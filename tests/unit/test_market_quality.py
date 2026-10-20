from datetime import timedelta
from decimal import Decimal, localcontext
import unittest

from autohedge.data.contracts import CandleRequest, QualityPolicy
from autohedge.data.quality import (
    parse_market_data,
    validate_candle_response,
    validate_market_data,
)
from autohedge.errors import DataQualityError
from market_fixtures import bundle, INSTRUMENT, NOW


class MarketQualityTests(unittest.TestCase):
    def test_unvalidated_policy_copy_cannot_disable_freshness_limits(self):
        with self.assertRaises(DataQualityError) as error:
            self.check(
                policy=QualityPolicy().model_copy(update={"max_quote_age_seconds": -1})
            )
        self.assertEqual(error.exception.code, "invalid_policy")

    def check(self, data=None, **kwargs):
        return validate_market_data(
            data if data is not None else bundle(),
            expected=kwargs.pop("expected", INSTRUMENT),
            now=kwargs.pop("now", NOW),
            **kwargs,
        )

    def assert_rejected(self, payload, code, **kwargs):
        with self.assertRaises(DataQualityError) as error:
            self.check(parse_market_data(payload), **kwargs)
        self.assertEqual(error.exception.code, code)

    def test_valid_data_has_auditable_assessment_and_frozen_policy(self):
        result = self.check(bundle(news=True))
        self.assertEqual(result.checked_at, NOW)
        self.assertEqual(result.quote_age_seconds, Decimal("1"))
        self.assertEqual(result.candle_count, 30)
        self.assertEqual(result.news_count, 1)
        self.assertEqual(result.price_deviation_bps, 0)
        self.assertEqual(result.policy, QualityPolicy())

    def test_wrong_requested_identity_is_rejected_even_if_bundle_is_consistent(self):
        for update in (
            {"asset_id": "wrong-mint"},
            {"venue": "other-venue"},
            {"quote_currency": "USD"},
        ):
            with (
                self.subTest(update=update),
                self.assertRaises(DataQualityError) as error,
            ):
                self.check(expected=INSTRUMENT.model_copy(update=update))
            self.assertEqual(error.exception.code, "instrument_mismatch")

    def test_staleness_and_transport_delay_are_distinct(self):
        stale = bundle().model_dump()
        stale["quote"].update(
            event_time=NOW - timedelta(seconds=61),
            received_at=NOW - timedelta(seconds=60),
        )
        self.assert_rejected(stale, "stale_quote")
        delayed = bundle().model_dump()
        delayed["quote"]["event_time"] = NOW - timedelta(seconds=31)
        self.assert_rejected(delayed, "quote_transport_lag")

    def test_exact_quote_age_boundary_and_subsecond_overrun(self):
        payload = bundle().model_dump()
        payload["quote"].update(
            event_time=NOW - timedelta(seconds=60),
            received_at=NOW - timedelta(seconds=59),
        )
        self.assertEqual(self.check(parse_market_data(payload)).quote_age_seconds, 60)
        payload["quote"]["event_time"] -= timedelta(microseconds=1)
        self.assert_rejected(payload, "stale_quote")

    def test_future_receive_and_event_times_are_rejected(self):
        for field in ("quote", "history"):
            payload = bundle().model_dump()
            payload[field]["received_at"] = NOW + timedelta(seconds=1)
            if field == "quote":
                payload[field]["event_time"] = NOW + timedelta(seconds=1)
            with self.subTest(field=field):
                self.assert_rejected(payload, "future_data")

    def test_missing_and_stale_candles_are_rejected(self):
        with self.assertRaises(DataQualityError) as error:
            self.check(bundle(count=29))
        self.assertEqual(error.exception.code, "insufficient_candles")
        with self.assertRaises(DataQualityError) as error:
            self.check(bundle(end=NOW - timedelta(seconds=121)))
        self.assertEqual(error.exception.code, "stale_candles")
        self.check(bundle(end=NOW - timedelta(seconds=120)))

    def test_price_disagreement_has_exact_policy_boundary(self):
        self.assertEqual(
            self.check(bundle(quote_price="110")).price_deviation_bps, 1000
        )
        with self.assertRaises(DataQualityError) as error:
            self.check(bundle(quote_price="110.00000000000000000001"))
        self.assertEqual(error.exception.code, "price_deviation")
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(
                self.check(bundle(quote_price="105")).price_deviation_bps, 500
            )

    def test_optional_required_stale_and_future_news(self):
        self.check(bundle())
        with self.assertRaises(DataQualityError) as error:
            self.check(policy=QualityPolicy(require_news=True))
        self.assertEqual(error.exception.code, "missing_news")
        payload = bundle(news=True).model_dump()
        payload["news"][0]["published_at"] = NOW - timedelta(days=2)
        self.assert_rejected(payload, "stale_news")
        payload["news"][0].update(
            published_at=NOW + timedelta(seconds=1),
            received_at=NOW + timedelta(seconds=1),
        )
        self.assert_rejected(payload, "future_news")

    def test_naive_clock_is_not_assumed_to_be_utc(self):
        with self.assertRaises(DataQualityError) as error:
            self.check(now=NOW.replace(tzinfo=None))
        self.assertEqual(error.exception.code, "invalid_clock")

    def test_candle_response_matches_requested_interval_count_and_asof(self):
        series = bundle().history
        request = CandleRequest(
            instrument=INSTRUMENT, interval_seconds=60, count=30, as_of=NOW
        )
        self.assertEqual(validate_candle_response(series, request), series)
        for update, code in [
            ({"interval_seconds": 120}, "interval_mismatch"),
            ({"count": 31}, "insufficient_candles"),
            ({"as_of": NOW - timedelta(seconds=1)}, "future_candles"),
            ({"as_of": NOW + timedelta(seconds=60)}, "incomplete_window"),
        ]:
            with (
                self.subTest(update=update),
                self.assertRaises(DataQualityError) as error,
            ):
                validate_candle_response(series, request.model_copy(update=update))
            self.assertEqual(error.exception.code, code)
