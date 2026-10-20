from datetime import timedelta
from tempfile import TemporaryDirectory
import unittest

from autohedge.config import Settings
from autohedge.data.contracts import QualityPolicy
from autohedge.domain import MarketSnapshot, ResearchReport, ResearchRequest
from autohedge.errors import DataQualityError
from autohedge.main import AutoHedge
from autohedge.orchestration import ResearchOrchestrator
from market_fixtures import bundle, INSTRUMENT, NOW
from test_run_lifecycle import make_team


class MarketResearchTests(unittest.TestCase):
    def test_rejected_market_data_never_constructs_agents(self):
        created = []
        request = ResearchRequest(
            task="analyze SOL",
            instrument=INSTRUMENT,
            market_data=bundle(quote_price="120"),
        )
        with self.assertRaises(DataQualityError):
            ResearchOrchestrator(
                lambda now: created.append(now), clock=lambda: NOW
            ).run(request)
        self.assertEqual(created, [])

    def test_valid_market_context_and_assessment_reach_every_role(self):
        calls = []
        report = ResearchOrchestrator(
            lambda now: make_team(calls), clock=lambda: NOW
        ).run(
            ResearchRequest(
                task="analyze SOL", instrument=INSTRUMENT, market_data=bundle(news=True)
            )
        )
        self.assertEqual(report.market_quality.candle_count, 30)
        self.assertEqual(report.market_quality.checked_at, NOW)
        self.assertEqual(report.market_quality.policy, QualityPolicy())
        self.assertNotIn("No validated market snapshot", " ".join(report.warnings))
        for _, inputs in calls:
            self.assertEqual(inputs["request"]["market_data"]["quote"]["price"], "100")
            self.assertEqual(
                inputs["request"]["instrument"]["asset_id"], INSTRUMENT.asset_id
            )
        self.assertFalse(report.execution_enabled)
        self.assertEqual(
            ResearchReport.model_validate_json(report.model_dump_json()), report
        )

    def test_data_that_becomes_stale_during_a_call_stops_later_stages(self):
        calls = []
        team = make_team(calls)
        clock = [NOW]
        original = team.director.run

        def slow(task):
            output = original(task)
            clock[0] += timedelta(seconds=61)
            return output

        team.director.run = slow
        request = ResearchRequest(
            task="analyze", instrument=INSTRUMENT, market_data=bundle()
        )
        with self.assertRaises(DataQualityError) as error:
            ResearchOrchestrator(lambda now: team, clock=lambda: clock[0]).run(request)
        self.assertEqual(error.exception.code, "stale_quote")
        self.assertEqual([role for role, _ in calls], ["director"])

    def test_public_api_preserves_last_success_file_but_not_stale_report_state(self):
        calls = []
        with TemporaryDirectory() as directory:
            system = AutoHedge(
                output_dir=directory,
                output_file_path="report.json",
                clock=lambda: NOW,
                settings=Settings(),
                team_factory=lambda now: make_team(calls),
            )
            result = system.run(
                "SOL research", instrument=INSTRUMENT, market_data=bundle()
            )
            self.assertIsInstance(result, list)
            self.assertIsNotNone(system.last_report.market_quality)
            saved = (system.output_dir / "report.json").read_text()
            calls.clear()
            with self.assertRaises(DataQualityError):
                system.run_report(
                    "bad data",
                    instrument=INSTRUMENT,
                    market_data=bundle(quote_price="120"),
                )
            self.assertEqual(calls, [])
            self.assertIsNone(system.last_report)
            self.assertEqual((system.output_dir / "report.json").read_text(), saved)

    def test_legacy_snapshots_are_freshness_checked_but_not_claimed_ohlcv_validated(
        self,
    ):
        for offset in (-61, 1):
            with self.subTest(offset=offset), self.assertRaises(DataQualityError):
                request = ResearchRequest(
                    task="legacy",
                    market=MarketSnapshot(
                        instrument=INSTRUMENT,
                        observed_at=NOW + timedelta(seconds=offset),
                        source="fixture",
                        price=100,
                    ),
                )
                ResearchOrchestrator(lambda now: make_team([]), clock=lambda: NOW).run(
                    request
                )
        request = ResearchRequest(
            task="legacy",
            market=MarketSnapshot(
                instrument=INSTRUMENT, observed_at=NOW, source="fixture", price=100
            ),
        )
        report = ResearchOrchestrator(lambda now: make_team([]), clock=lambda: NOW).run(
            request
        )
        self.assertIsNone(report.market_quality)
        self.assertIn("Legacy price-only", " ".join(report.warnings))

    def test_injected_quality_policy_is_used_and_saved(self):
        policy = QualityPolicy(min_candles=31)
        with TemporaryDirectory() as directory:
            system = AutoHedge(
                output_dir=directory,
                clock=lambda: NOW,
                settings=Settings(market_quality=policy),
                team_factory=lambda now: make_team([]),
            )
            with self.assertRaises(DataQualityError) as error:
                system.run_report(
                    "needs more history", instrument=INSTRUMENT, market_data=bundle()
                )
            self.assertEqual(error.exception.code, "insufficient_candles")
            report = system.run_report(
                "enough history", instrument=INSTRUMENT, market_data=bundle(count=31)
            )
            self.assertEqual(report.market_quality.policy.min_candles, 31)
