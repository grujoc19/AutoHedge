from datetime import timedelta
from decimal import Decimal, Inexact, ROUND_UP, Rounded, localcontext
from fractions import Fraction
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from autohedge.data.history import HistoryStore
from autohedge.errors import DataQualityError
from autohedge.indicators import (
    IndicatorConfig,
    IndicatorPoint,
    IndicatorSeries,
    calculate_indicators,
    indicators_from_snapshot,
)
from history_fixtures import snapshot
from indicator_fixtures import history
from market_fixtures import NOW
from tempfile import TemporaryDirectory

REFERENCES = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures/indicators/reference.json"
    ).read_text()
)
SMALL = IndicatorConfig(
    rsi_period=2, macd_fast=2, macd_slow=3, macd_signal=2, volatility_window=2
)


class IndicatorTests(unittest.TestCase):
    def assert_fraction(self, value, expected):
        if expected is None:
            self.assertIsNone(value)
            return
        fraction = Fraction(expected)
        with localcontext() as context:
            context.prec = 128
            target = Decimal(fraction.numerator) / fraction.denominator
            self.assertLessEqual(abs(value - target), Decimal("1e-110"))

    def test_wilder_rsi_matches_hand_calculated_recursive_reference(self):
        case = REFERENCES["rsi_wilder_2"]
        result = calculate_indicators(history(case["closes"]), as_of=NOW, config=SMALL)
        for point, expected in zip(result.points, case["expected"]):
            self.assert_fraction(point.rsi, expected)
        case = REFERENCES["rsi_wilder_14"]
        result = calculate_indicators(history(case["closes"]), as_of=NOW)
        for point, expected in zip(result.points[-2:], case["expected_last_two"]):
            self.assert_fraction(point.rsi, expected)

    def test_macd_independent_sma_seeds_and_signal_match_exact_fractions(self):
        case = REFERENCES["macd_2_3_2"]
        result = calculate_indicators(history(case["closes"]), as_of=NOW, config=SMALL)
        for i, point in enumerate(result.points):
            self.assert_fraction(point.macd, case["line"][i])
            self.assert_fraction(point.macd_signal, case["signal"][i])
            self.assert_fraction(point.macd_histogram, case["histogram"][i])

    def test_log_return_sample_volatility_and_continuous_spot_annualization(self):
        result = calculate_indicators(
            history(["1", "2", "2", "4"]), as_of=NOW, config=SMALL
        )
        # Closed-form result, independent of the rolling variance implementation.
        with localcontext() as context:
            context.prec = 128
            expected = Decimal(2).ln() / Decimal(2).sqrt()
            annualized = Decimal(2).ln() * Decimal(262800).sqrt()
            for point in result.points[2:]:
                self.assertLess(abs(point.volatility - expected), Decimal("1e-110"))
                self.assertLess(
                    abs(point.annualized_volatility - annualized), Decimal("1e-108")
                )
        self.assertIsNone(result.points[1].volatility)
        self.assertEqual(result.annualization_seconds, 31536000)
        # Daily sampling uses sqrt(365), not the equity-market sqrt(252).
        daily = calculate_indicators(
            history(["1", "2", "2"], interval=86400), as_of=NOW, config=SMALL
        )
        with localcontext() as context:
            context.prec = 128
            self.assertEqual(
                daily.latest.annualized_volatility,
                daily.latest.volatility * Decimal(365).sqrt(),
            )

    def test_warmup_is_explicit_and_zero_values_are_ready(self):
        for count in (1, 14, 15, 20, 21, 25, 26, 33, 34, 35):
            with self.subTest(count=count):
                result = calculate_indicators(history(["100"] * count), as_of=NOW)
                point = result.latest
                self.assertEqual(point.rsi is not None, count >= 15)
                self.assertEqual(point.macd is not None, count >= 26)
                self.assertEqual(point.macd_signal is not None, count >= 34)
                self.assertEqual(point.volatility is not None, count >= 21)
                self.assertEqual(point.ready, count >= 34)
        self.assertEqual(IndicatorConfig().minimum_candles, 34)
        self.assertEqual(SMALL.minimum_candles, 4)
        flat = calculate_indicators(history(["100"] * 40), as_of=NOW).latest
        self.assertEqual(flat.rsi, 50)
        self.assertEqual(flat.macd, 0)
        self.assertEqual(flat.macd_signal, 0)
        self.assertEqual(flat.macd_histogram, 0)
        self.assertEqual(flat.volatility, 0)
        self.assertTrue(flat.ready)

    def test_monotonic_prices_and_constant_geometric_returns_have_defined_limits(self):
        up = calculate_indicators(history([str(i) for i in range(1, 41)]), as_of=NOW)
        down = calculate_indicators(
            history([str(i) for i in range(40, 0, -1)]), as_of=NOW
        )
        self.assertEqual(up.latest.rsi, 100)
        self.assertEqual(down.latest.rsi, 0)
        geometric = calculate_indicators(
            history([str(2**i) for i in range(40)]), as_of=NOW
        )
        self.assertEqual(geometric.latest.volatility, 0)
        self.assertEqual(geometric.latest.annualized_volatility, 0)

    def test_later_candles_never_rewrite_any_prefix_result(self):
        prices = [str(100 + ((i * 7) % 23)) for i in range(60)]
        full = history(prices)
        result = calculate_indicators(full, as_of=NOW)
        for count in (1, 15, 21, 26, 33, 34, 40, 59):
            bars = full.candles[:count]
            prefix = full.model_copy(
                update={"candles": bars, "received_at": bars[-1].close_time}
            )
            actual = calculate_indicators(prefix, as_of=bars[-1].close_time)
            self.assertEqual(actual.points, result.points[:count])

    def test_tiny_price_changes_are_preserved_instead_of_collapsing_to_flat_float(self):
        tiny = history([f"1.{i:030d}" for i in range(40)])
        result = calculate_indicators(tiny, as_of=NOW)
        self.assertEqual(result.latest.rsi, 100)
        self.assertGreater(result.latest.macd, 0)
        self.assertGreater(result.latest.volatility, 0)
        self.assertEqual(float(tiny.candles[0].close), float(tiny.candles[-1].close))

    def test_price_scaling_preserves_rsi_and_volatility_and_scales_macd(self):
        prices = [Decimal(100 + ((i * 7) % 23)) for i in range(40)]
        first = calculate_indicators(history(prices), as_of=NOW).latest
        for scale in (Decimal("1e-20"), Decimal("1e20")):
            with localcontext() as context:
                context.prec = 128
                scaled = [price * scale for price in prices]
            second = calculate_indicators(history(scaled), as_of=NOW).latest
            with localcontext() as context:
                context.prec = 128
                self.assertLess(abs(second.rsi - first.rsi), Decimal("1e-110"))
                self.assertLess(
                    abs(second.volatility - first.volatility), Decimal("1e-110")
                )
                self.assertLess(
                    abs(second.macd / scale - first.macd), Decimal("1e-100")
                )

    def test_decimal_precision_rounding_and_traps_do_not_change_results_or_global_context(
        self,
    ):
        data = history([str(100 + ((i * 7) % 23)) for i in range(40)])
        expected = calculate_indicators(data, as_of=NOW)
        with localcontext() as context:
            context.prec = 6
            context.rounding = ROUND_UP
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            actual = calculate_indicators(data, as_of=NOW)
            self.assertEqual(actual, expected)
            self.assertEqual(
                IndicatorSeries.model_validate_json(actual.model_dump_json()), actual
            )
            self.assertEqual(context.prec, 6)
            self.assertTrue(context.traps[Inexact])
            self.assertTrue(context.traps[Rounded])

    def test_unreceived_historical_data_and_invalid_knowledge_clocks_are_rejected(self):
        data = history(["100"] * 40)
        with self.assertRaisesRegex(DataQualityError, "not_yet_available"):
            calculate_indicators(data, as_of=NOW - timedelta(microseconds=1))
        for clock in (NOW.replace(tzinfo=None), "sensitive-fixture-invalid-clock"):
            with (
                self.subTest(clock=clock),
                self.assertRaisesRegex(DataQualityError, "invalid_clock") as error,
            ):
                calculate_indicators(data, as_of=clock)
            self.assertNotIn("sensitive-fixture", str(error.exception))

    def test_invalid_ohlcv_missing_prices_and_nonvalidating_copies_fail_safely(self):
        data = history(["100"] * 40)
        bars = data.candles
        variants = (
            data.model_copy(update={"candles": tuple(reversed(bars))}),
            data.model_copy(update={"candles": (bars[0], *bars)}),
            data.model_copy(update={"candles": bars[:20] + bars[21:]}),
            data.model_copy(
                update={
                    "candles": bars[:-1]
                    + (
                        bars[-1].model_copy(
                            update={"close_time": NOW + timedelta(seconds=1)}
                        ),
                    )
                }
            ),
        )
        for value in (None, True, 0, -1, "NaN", "Infinity"):
            variants += (
                data.model_copy(
                    update={
                        "candles": (
                            bars[0].model_copy(update={"close": value}),
                            *bars[1:],
                        )
                    }
                ),
            )
        for invalid in variants:
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(DataQualityError, "invalid_indicator_history"),
            ):
                calculate_indicators(invalid, as_of=NOW)

    def test_config_bounds_and_nonvalidating_config_copies_are_revalidated(self):
        for update in (
            {"rsi_period": True},
            {"rsi_period": 1},
            {"volatility_window": 1},
            {"macd_fast": 26},
            {"macd_slow": 1001},
            {"macd_signal": "9"},
        ):
            with (
                self.subTest(update=update),
                self.assertRaisesRegex(DataQualityError, "invalid_indicator_config"),
            ):
                calculate_indicators(
                    history(["100"]),
                    as_of=NOW,
                    config=IndicatorConfig().model_copy(update=update),
                )

    def test_serialization_provenance_and_readiness_survive_round_trip(self):
        data = history(["100"] * 40)
        result = calculate_indicators(data, as_of=NOW)
        self.assertEqual(
            IndicatorSeries.model_validate_json(result.model_dump_json()), result
        )
        self.assertEqual(result.instrument, data.instrument)
        self.assertEqual(result.source, data.source)
        self.assertEqual(result.received_at, data.received_at)
        self.assertEqual(result.latest.close_time, data.candles[-1].close_time)
        self.assertEqual(len(result.history_digest), 64)
        later = calculate_indicators(
            data.model_copy(update={"received_at": NOW + timedelta(seconds=1)}),
            as_of=NOW + timedelta(seconds=1),
        )
        different_source = calculate_indicators(
            data.model_copy(update={"source": "other-source"}), as_of=NOW
        )
        self.assertNotEqual(result.history_digest, later.history_digest)
        self.assertNotEqual(result.history_digest, different_source.history_digest)
        self.assertEqual(
            result.history_digest,
            calculate_indicators(data, as_of=NOW + timedelta(days=1)).history_digest,
        )
        wire = calculate_indicators(history(["100"]), as_of=NOW).model_dump_json()
        self.assertIn('"rsi":null', wire)
        self.assertNotIn("NaN", wire)

    def test_output_contract_rejects_invalid_values_timeline_and_warmup(self):
        result = calculate_indicators(history(["100"] * 40), as_of=NOW)
        for update in (
            {"rsi": 101},
            {"rsi": True},
            {"macd": "Infinity"},
            {"volatility": -1},
            {"macd_histogram": "1"},
        ):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                IndicatorPoint.model_validate(result.latest.model_copy(update=update))
        for update in (
            {"as_of": NOW - timedelta(seconds=1)},
            {"config": SMALL},
            {"points": tuple(reversed(result.points))},
            {"history_digest": "not-a-hash"},
        ):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                IndicatorSeries.model_validate(result.model_copy(update=update))

    def test_persisted_snapshot_bridge_preserves_history_and_rejects_forged_pages(self):
        acquisition = snapshot()
        with TemporaryDirectory() as directory:
            store = HistoryStore(directory)
            loaded = store.load(store.save(acquisition))
        self.assertEqual(
            indicators_from_snapshot(
                loaded, as_of=loaded.series.received_at, config=SMALL
            ),
            calculate_indicators(
                loaded.series, as_of=loaded.series.received_at, config=SMALL
            ),
        )
        self.assertFalse(
            indicators_from_snapshot(
                loaded, as_of=loaded.series.received_at, config=SMALL
            ).latest.ready
        )
        with self.assertRaisesRegex(DataQualityError, "invalid_history"):
            indicators_from_snapshot(
                loaded.model_copy(update={"pages": ()}), as_of=loaded.series.received_at
            )

    def test_maximum_supported_windows_and_history_length_are_bounded_and_ready(self):
        config = IndicatorConfig(
            rsi_period=1000,
            macd_fast=999,
            macd_slow=1000,
            macd_signal=1000,
            volatility_window=1000,
        )
        result = calculate_indicators(history(["100"] * 2000), as_of=NOW, config=config)
        self.assertEqual(len(result.points), 2000)
        self.assertFalse(result.points[1997].ready)
        self.assertTrue(result.points[1998].ready)
        self.assertEqual(result.latest.volatility, 0)


if __name__ == "__main__":
    unittest.main()
