from datetime import timedelta
from decimal import Inexact, Rounded, ROUND_UP, localcontext
import json
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from autohedge.data.contracts import QualityPolicy
from autohedge.data.history import HistoryStore
from autohedge.data.provenance import content_id
from autohedge.errors import DataQualityError, ProviderError
from autohedge.snapshots import (
    build_research_snapshot,
    check_snapshot_quality,
    collect_research_snapshot,
    parse_research_snapshot,
    snapshot_context,
)
from market_fixtures import INSTRUMENT, NOW, bundle
from snapshot_fixtures import acquisition, snapshot


class SnapshotTests(unittest.TestCase):
    def test_round_trip_binds_ready_features_quality_and_observations(self):
        result = snapshot(pages=True)
        self.assertEqual(
            parse_research_snapshot(json.loads(result.model_dump_json())), result
        )
        self.assertTrue(result.indicators.latest.ready)
        self.assertEqual(result.indicators.latest.rsi, 50)
        self.assertEqual(result.quality.policy.min_candles, 34)
        self.assertTrue(result.quality.policy.require_news)
        self.assertEqual(result.quality.checked_at, NOW)
        self.assertEqual(result.indicators.as_of, NOW)
        self.assertEqual(result.snapshot_id, snapshot(pages=True).snapshot_id)
        self.assertEqual(
            result.indicators.history_digest, content_id(result.market_data.history)
        )
        with TemporaryDirectory() as directory:
            self.assertEqual(
                HistoryStore(directory).save(result.history_acquisition),
                result.history_acquisition_id,
            )
        self.assertEqual(
            [ref.kind for ref in result.evidence],
            ["quote", "history", "indicators", "news"],
        )
        self.assertEqual(len({ref.evidence_id for ref in result.evidence}), 4)
        for ref in result.evidence:
            self.assertLessEqual(ref.event_time, ref.received_at)
            self.assertLessEqual(ref.received_at, ref.known_at)
            self.assertLessEqual(ref.known_at, result.as_of)

    def test_snapshot_identity_is_independent_of_decimal_rounding_and_traps(self):
        data = bundle(count=40, news=True)
        bars = tuple(
            c.model_copy(update={key: "99" for key in ("open", "high", "low", "close")})
            for c in data.history.candles
        )
        data = data.model_copy(
            update={"history": data.history.model_copy(update={"candles": bars})}
        )
        expected = build_research_snapshot(data, expected=INSTRUMENT, as_of=NOW)
        with localcontext() as context:
            context.prec = 6
            context.rounding = ROUND_UP
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            actual = build_research_snapshot(data, expected=INSTRUMENT, as_of=NOW)
            self.assertEqual(actual, expected)
            self.assertEqual(
                parse_research_snapshot(actual.model_dump(mode="json")), expected
            )
            self.assertEqual(context.prec, 6)
            self.assertTrue(context.traps[Inexact])

    def test_bad_input_missing_required_news_and_incomplete_warmup_abort(self):
        for data, code in (
            (bundle(count=30, news=True), "indicators_not_ready"),
            (bundle(count=40), "missing_news"),
            (bundle(count=40, quote_price="150", news=True), "price_deviation"),
        ):
            with (
                self.subTest(code=code),
                self.assertRaisesRegex(DataQualityError, code),
            ):
                build_research_snapshot(data, expected=INSTRUMENT, as_of=NOW)
        with self.assertRaisesRegex(DataQualityError, "instrument_mismatch"):
            build_research_snapshot(
                bundle(count=40, news=True),
                expected=INSTRUMENT.model_copy(update={"asset_id": "other"}),
                as_of=NOW,
            )
        with self.assertRaisesRegex(DataQualityError, "invalid_clock"):
            build_research_snapshot(
                bundle(count=40, news=True),
                expected=INSTRUMENT,
                as_of=NOW.replace(tzinfo=None),
            )
        with self.assertRaisesRegex(DataQualityError, "invalid_snapshot_configuration"):
            snapshot(policy=QualityPolicy().model_copy(update={"min_candles": True}))

    def test_later_backfill_and_future_news_never_become_past_evidence(self):
        data = bundle(count=40, news=True)
        future_history = data.history.model_copy(
            update={"received_at": NOW + timedelta(seconds=1)}
        )
        with self.assertRaisesRegex(DataQualityError, "future_data"):
            build_research_snapshot(
                data.model_copy(update={"history": future_history}),
                expected=INSTRUMENT,
                as_of=NOW,
            )
        with self.assertRaisesRegex(DataQualityError, "future_news"):
            build_research_snapshot(
                data.model_copy(
                    update={
                        "news": (
                            data.news[0].model_copy(
                                update={"received_at": NOW + timedelta(seconds=1)}
                            ),
                        )
                    }
                ),
                expected=INSTRUMENT,
                as_of=NOW,
            )

    def test_edited_sources_change_content_ids_without_claiming_semantic_novelty(self):
        first = snapshot()
        news = first.market_data.news[0].model_copy(
            update={"summary": "An edited excerpt"}
        )
        edited = first.market_data.model_copy(update={"news": (news,)})
        second = build_research_snapshot(edited, expected=INSTRUMENT, as_of=NOW)
        self.assertNotEqual(first.snapshot_id, second.snapshot_id)
        self.assertNotEqual(
            first.evidence[-1].evidence_id, second.evidence[-1].evidence_id
        )
        self.assertEqual(
            first.evidence[-1].source_event_id, second.evidence[-1].source_event_id
        )
        self.assertEqual(first.evidence[:-1], second.evidence[:-1])

    def test_rehashing_a_forged_snapshot_does_not_bypass_recalculation(self):
        original = snapshot(pages=True)
        points = original.indicators.points[:-1] + (
            original.indicators.latest.model_copy(update={"rsi": "25"}),
        )
        variants = (
            original.model_copy(
                update={
                    "indicators": original.indicators.model_copy(
                        update={"points": points}
                    )
                }
            ),
            original.model_copy(
                update={"evidence": tuple(reversed(original.evidence))}
            ),
            original.model_copy(update={"history_acquisition_id": "0" * 64}),
            original.model_copy(
                update={
                    "quality": original.quality.model_copy(
                        update={"quote_age_seconds": "0"}
                    )
                }
            ),
            original.model_copy(
                update={
                    "market_data": original.market_data.model_copy(
                        update={
                            "quote": original.market_data.quote.model_copy(
                                update={"price": "101"}
                            )
                        }
                    )
                }
            ),
        )
        for invalid in variants:
            wire = invalid.model_dump(mode="json")
            wire["snapshot_id"] = content_id(
                {key: value for key, value in wire.items() if key != "snapshot_id"}
            )
            with (
                self.subTest(keys=wire.keys()),
                self.assertRaisesRegex(DataQualityError, "invalid_research_snapshot"),
            ):
                parse_research_snapshot(wire)
        with self.assertRaisesRegex(DataQualityError, "invalid_research_snapshot"):
            parse_research_snapshot(
                original.model_copy(update={"snapshot_id": "0" * 64})
            )

    def test_snapshot_and_runtime_constraints_only_strengthen_each_other(self):
        captured_strict = snapshot(
            policy=QualityPolicy(max_quote_age_seconds=5, require_news=True)
        )
        at = NOW + timedelta(seconds=6)
        with self.assertRaisesRegex(DataQualityError, "stale_quote"):
            check_snapshot_quality(
                captured_strict, expected=INSTRUMENT, now=at, policy=QualityPolicy()
            )
        runtime_strict = QualityPolicy(max_quote_age_seconds=5)
        with self.assertRaisesRegex(DataQualityError, "stale_quote"):
            check_snapshot_quality(
                snapshot(), expected=INSTRUMENT, now=at, policy=runtime_strict
            )
        result = check_snapshot_quality(
            snapshot(), expected=INSTRUMENT, now=NOW, policy=QualityPolicy()
        )
        self.assertTrue(result.policy.require_news)
        self.assertEqual(result.policy.min_candles, 34)
        self.assertEqual(result.checked_at, NOW)

    def test_optional_news_requires_explicit_policy_and_runtime_can_require_it(self):
        result = snapshot(news=False, policy=QualityPolicy(require_news=False))
        self.assertEqual(len(result.evidence), 3)
        with self.assertRaisesRegex(DataQualityError, "missing_news"):
            check_snapshot_quality(
                result,
                expected=INSTRUMENT,
                now=NOW,
                policy=QualityPolicy(require_news=True),
            )

    def test_context_keeps_sources_and_latest_calculations_without_arrays(self):
        result = snapshot(count=1000)
        context = snapshot_context(result)
        self.assertEqual(context["snapshot_id"], result.snapshot_id)
        self.assertEqual(context["history"]["candle_count"], 1000)
        self.assertNotIn("candles", context["history"])
        self.assertNotIn("points", context["indicators"])
        self.assertEqual(context["indicators"]["latest"]["rsi"], "50")
        self.assertEqual(
            context["news"], [result.market_data.news[0].model_dump(mode="json")]
        )
        self.assertEqual(
            context["evidence"],
            [item.model_dump(mode="json") for item in result.evidence],
        )
        self.assertLess(len(json.dumps(context)), 16000)
        self.assertGreater(len(result.model_dump_json()), len(json.dumps(context)))


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.data = bundle(count=40, news=True)
        self.history = acquisition(self.data)
        self.calls = []
        self.quote = SimpleNamespace(
            fetch_quote=Mock(
                side_effect=lambda instrument: self._record("quote", self.data.quote)
            )
        )
        self.news = SimpleNamespace(
            fetch_news=Mock(
                side_effect=lambda instrument: self._record("news", self.data.news)
            )
        )
        self.provider = SimpleNamespace(
            fetch_history=Mock(
                side_effect=lambda request: self._record("history", self.history)
            )
        )

    def _record(self, name, value):
        self.calls.append(name)
        return value

    def collect(self, **kwargs):
        return collect_research_snapshot(
            self.history.request,
            quote_provider=self.quote,
            history_provider=self.provider,
            news_provider=self.news,
            clock=lambda: NOW,
            **kwargs,
        )

    def test_collection_order_freezes_sources_and_acquisition(self):
        result = self.collect()
        self.assertEqual(self.calls, ["history", "news", "quote"])
        self.assertEqual(result.market_data, self.data)
        self.assertEqual(result.history_acquisition, self.history)
        self.assertEqual(
            result,
            build_research_snapshot(
                self.data,
                expected=INSTRUMENT,
                as_of=NOW,
                history_acquisition=self.history,
            ),
        )
        self.provider.fetch_history.assert_called_once_with(self.history.request)
        self.news.fetch_news.assert_called_once_with(INSTRUMENT)
        self.quote.fetch_quote.assert_called_once_with(INSTRUMENT)

    def test_provider_failure_returns_no_partial_or_fallback_and_stops_later_reads(
        self,
    ):
        self.news.fetch_news.side_effect = ProviderError("exa", "unavailable")
        with self.assertRaises(ProviderError):
            self.collect()
        self.quote.fetch_quote.assert_not_called()
        self.provider.fetch_history.assert_called_once()
        self.provider.fetch_history.reset_mock()
        self.news.fetch_news.side_effect = RuntimeError(
            "sensitive-fixture-provider-body"
        )
        with self.assertRaisesRegex(ProviderError, "collection failed") as error:
            self.collect()
        self.assertNotIn("sensitive-fixture", str(error.exception))

    def test_missing_news_provider_and_short_request_fail_before_reads(self):
        with self.assertRaisesRegex(DataQualityError, "missing_news"):
            collect_research_snapshot(
                self.history.request,
                quote_provider=self.quote,
                history_provider=self.provider,
                news_provider=None,
                clock=lambda: NOW,
            )
        self.provider.fetch_history.assert_not_called()
        with self.assertRaisesRegex(DataQualityError, "indicators_not_ready"):
            collect_research_snapshot(
                self.history.request.model_copy(update={"count": 30}),
                quote_provider=self.quote,
                history_provider=self.provider,
                news_provider=self.news,
                clock=lambda: NOW,
            )
        self.assertEqual(self.calls, [])

    def test_wrong_request_identity_future_data_and_clock_regression_are_rejected(self):
        request = self.history.request.model_copy(update={"count": 39})
        with self.assertRaisesRegex(DataQualityError, "history_request_mismatch"):
            collect_research_snapshot(
                request,
                quote_provider=self.quote,
                history_provider=self.provider,
                news_provider=self.news,
                clock=lambda: NOW,
            )
        self.quote.fetch_quote.assert_not_called()
        clock = Mock(side_effect=[NOW, NOW - timedelta(seconds=1)])
        with self.assertRaisesRegex(DataQualityError, "clock_regression"):
            collect_research_snapshot(
                self.history.request,
                quote_provider=self.quote,
                history_provider=self.provider,
                news_provider=self.news,
                clock=clock,
            )
        with self.assertRaisesRegex(DataQualityError, "future_history_request"):
            collect_research_snapshot(
                self.history.request,
                quote_provider=self.quote,
                history_provider=self.provider,
                news_provider=self.news,
                clock=lambda: NOW - timedelta(seconds=1),
            )


if __name__ == "__main__":
    unittest.main()
