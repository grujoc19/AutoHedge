from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from autohedge.config import Settings
from autohedge.domain import ResearchReport, ResearchRequest
from autohedge.errors import ConfigurationError, DataQualityError, ResearchError
from autohedge.main import AutoHedge
from autohedge.orchestration import ResearchOrchestrator
from autohedge.snapshots import build_research_snapshot
from market_fixtures import INSTRUMENT, NOW, bundle
from snapshot_fixtures import snapshot
from test_run_lifecycle import make_team


class SnapshotResearchTests(unittest.TestCase):
    def test_all_roles_receive_same_compact_snapshot_and_precomputed_evidence(self):
        calls = []
        snap = snapshot()
        request = ResearchRequest(
            task="SOL snapshot research", instrument=INSTRUMENT, snapshot=snap
        )
        report = ResearchOrchestrator(
            lambda now: make_team(calls), clock=lambda: NOW
        ).run(request)
        self.assertEqual(len(calls), 5)
        for role, payload in calls:
            self.assertEqual(
                payload["request"]["snapshot"]["snapshot_id"], snap.snapshot_id
            )
            self.assertEqual(
                payload["request"]["snapshot"]["indicators"]["latest"]["rsi"], "50"
            )
            self.assertTrue(payload["request"]["snapshot"]["indicators"]["ready"])
            self.assertEqual(
                payload["request"]["snapshot"]["evidence"],
                [item.model_dump(mode="json") for item in snap.evidence],
            )
            self.assertNotIn("points", payload["request"]["snapshot"]["indicators"])
            self.assertIsNone(payload["request"]["market_data"])
            self.assertIn("frozen snapshot", payload["instruction"])
        self.assertEqual(report.market_quality.candle_count, 40)
        self.assertEqual(report.market_quality.policy.min_candles, 34)
        self.assertTrue(report.market_quality.policy.require_news)
        self.assertFalse(report.execution_enabled)
        self.assertEqual(report.request.snapshot, snap)
        self.assertEqual(
            ResearchReport.model_validate_json(report.model_dump_json()), report
        )
        self.assertIn("caller-asserted", " ".join(report.warnings))

    def test_thousand_bar_audit_input_does_not_exceed_prompt_budget(self):
        calls = []
        snap = snapshot(count=1000)
        self.assertGreater(len(snap.model_dump_json()), 32000)
        report = ResearchOrchestrator(
            lambda now: make_team(calls), clock=lambda: NOW
        ).run(
            ResearchRequest(task="large history", instrument=INSTRUMENT, snapshot=snap)
        )
        self.assertEqual(len(calls), 5)
        self.assertEqual(len(report.request.snapshot.indicators.points), 1000)
        for _, payload in calls:
            self.assertLess(len(json.dumps(payload)), 32000)

    def test_snapshot_cannot_be_mixed_with_legacy_inputs_or_wrong_instrument(self):
        snap = snapshot()
        for kwargs in (
            {},
            {"instrument": INSTRUMENT, "market_data": bundle()},
            {"instrument": INSTRUMENT.model_copy(update={"quote_currency": "USD"})},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                ResearchRequest(task="mixed", snapshot=snap, **kwargs)

    def test_forged_stale_or_not_yet_known_snapshot_never_constructs_team(self):
        created = []
        orchestrator = ResearchOrchestrator(
            lambda now: created.append(now), clock=lambda: NOW + timedelta(seconds=61)
        )
        req = ResearchRequest(task="stale", instrument=INSTRUMENT, snapshot=snapshot())
        with self.assertRaisesRegex(DataQualityError, "stale_quote"):
            orchestrator.run(req)
        self.assertEqual(created, [])
        bad = req.model_copy(
            update={
                "snapshot": req.snapshot.model_copy(update={"snapshot_id": "0" * 64})
            }
        )
        with self.assertRaisesRegex(DataQualityError, "invalid_research_snapshot"):
            orchestrator.run(bad)
        self.assertEqual(created, [])
        future = build_research_snapshot(
            bundle(count=40, news=True),
            expected=INSTRUMENT,
            as_of=NOW + timedelta(seconds=1),
        )
        orchestrator.clock = lambda: NOW
        with self.assertRaisesRegex(DataQualityError, "future_research_snapshot"):
            orchestrator.run(
                ResearchRequest(task="future", instrument=INSTRUMENT, snapshot=future)
            )
        self.assertEqual(created, [])

    def test_snapshot_expiring_inside_role_stops_remaining_roles(self):
        calls = []
        clock = [NOW]
        team = make_team(calls)
        original = team.director.run

        def slow(task):
            output = original(task)
            clock[0] += timedelta(seconds=61)
            return output

        team.director.run = slow
        with self.assertRaisesRegex(DataQualityError, "stale_quote"):
            ResearchOrchestrator(lambda now: team, clock=lambda: clock[0]).run(
                ResearchRequest(
                    task="expires", instrument=INSTRUMENT, snapshot=snapshot()
                )
            )
        self.assertEqual([role for role, payload in calls], ["director"])

    def test_default_snapshot_factory_requires_only_openai_and_disables_live_search(
        self,
    ):
        calls = []
        settings = Settings(openai_api_key="model-fixture-key", search_enabled=True)
        with (
            TemporaryDirectory() as directory,
            patch(
                "autohedge.workers.build_agent_team",
                side_effect=lambda *args, **kwargs: make_team(calls),
            ) as factory,
        ):
            engine = AutoHedge(
                output_dir=directory, settings=settings, clock=lambda: NOW
            )
            report = engine.run_report(
                "frozen", instrument=INSTRUMENT, snapshot=snapshot()
            )
            self.assertEqual(len(report.stages), 5)
            passed_settings = factory.call_args.kwargs["settings"]
            self.assertFalse(passed_settings.search_enabled)
            self.assertTrue(engine.settings.search_enabled)
            self.assertIsNone(passed_settings.exa_api_key)
            with self.assertRaisesRegex(ConfigurationError, "EXA_API_KEY"):
                engine.run_report("legacy exploration")
            self.assertEqual(factory.call_count, 1)

    def test_atomic_report_preserves_audit_input_and_clears_failed_cycle_state(self):
        with TemporaryDirectory() as directory:
            engine = AutoHedge(
                output_dir=directory,
                output_file_path="report.json",
                output_type="dict",
                settings=Settings(),
                clock=lambda: NOW,
                team_factory=lambda now: make_team([]),
            )
            snap = snapshot(pages=True)
            result = engine.run("frozen", instrument=INSTRUMENT, snapshot=snap)
            self.assertEqual(
                result["request"]["snapshot"]["snapshot_id"], snap.snapshot_id
            )
            path = Path(directory) / "report.json"
            saved = path.read_text()
            self.assertIn('"history_acquisition"', saved)
            self.assertEqual(
                ResearchReport.model_validate_json(saved).request.snapshot, snap
            )
            with self.assertRaises(DataQualityError):
                engine.run_report(
                    "bad",
                    instrument=INSTRUMENT,
                    snapshot=snap.model_copy(update={"snapshot_id": "0" * 64}),
                )
            self.assertIsNone(engine.last_report)
            self.assertEqual(path.read_text(), saved)

    def test_oversized_news_is_not_silently_trimmed_and_never_invokes_a_role(self):
        data = bundle(count=40, news=True)
        article = data.news[0]
        first = article.model_copy(update={"title": "a" * 20000})
        second = article.model_copy(
            update={
                "article_id": "second",
                "url": "https://news.example/second",
                "title": "b" * 20000,
            }
        )
        data = data.model_copy(update={"news": (first, second)})
        snap = build_research_snapshot(data, expected=INSTRUMENT, as_of=NOW)
        created = []
        with self.assertRaisesRegex(ResearchError, "context character budget"):
            ResearchOrchestrator(
                lambda now: created.append(now), clock=lambda: NOW
            ).run(
                ResearchRequest(
                    task="too much news", instrument=INSTRUMENT, snapshot=snap
                )
            )
        self.assertEqual(created, [])


if __name__ == "__main__":
    unittest.main()
