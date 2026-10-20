from datetime import datetime, timezone
import unittest

from autohedge.domain import ResearchRequest, Stage
from autohedge.errors import ResearchError
from autohedge.orchestration import ResearchOrchestrator
from test_run_lifecycle import make_team


class OrchestrationTests(unittest.TestCase):
    def test_context_budget_prevents_model_invocation(self):
        calls = []
        with self.assertRaisesRegex(ResearchError, "context character budget"):
            ResearchOrchestrator(
                lambda now: make_team(calls), max_prompt_chars=100
            ).run(ResearchRequest(task="analyze"))
        self.assertEqual(calls, [])

    def test_stage_order_and_complete_dependency_handoffs(self):
        calls = []
        team = make_team(calls)
        report = ResearchOrchestrator(lambda now: team).run(
            ResearchRequest(task="analyze")
        )
        self.assertEqual([d["stage"] for _, d in calls], [s.value for s in Stage])
        for index, (_, data) in enumerate(calls):
            self.assertEqual(
                [p["stage"] for p in data["previous_results"]],
                [s.value for s in list(Stage)[:index]],
            )
        self.assertEqual(len(report.stages), 5)

    def test_empty_wrong_type_and_oversized_outputs_stop_following_stages(self):
        for output in (" ", None, {"text": "not a string"}, "x" * 20001):
            with self.subTest(output_type=type(output)):
                calls = []
                team = make_team(calls)
                team.quant.run = lambda task: output
                with self.assertRaisesRegex(ResearchError, "invalid or empty"):
                    ResearchOrchestrator(lambda now: team).run(
                        ResearchRequest(task="analyze")
                    )
                self.assertEqual([name for name, _ in calls], ["director"])

    def test_deadline_stops_the_cycle_after_an_overrunning_stage(self):
        elapsed = [0]
        team = make_team([])

        def slow(task):
            elapsed[0] = 6
            return "late output"

        team.director.run = slow
        orchestrator = ResearchOrchestrator(
            lambda now: team, timeout=5, monotonic=lambda: elapsed[0]
        )
        with self.assertRaisesRegex(ResearchError, "deadline"):
            orchestrator.run(ResearchRequest(task="analyze"))

    def test_swallowed_provider_failure_cannot_be_accepted_as_success(self):
        team = make_team([])

        def swallowed(task):
            team.provider_failures.append("exa")
            return "looks valid despite a failed tool"

        team.sentiment.run = swallowed
        with self.assertRaisesRegex(ResearchError, "required provider failed"):
            ResearchOrchestrator(lambda now: team).run(ResearchRequest(task="analyze"))

    def test_naive_clock_fails_before_team_creation(self):
        created = []
        orchestrator = ResearchOrchestrator(
            lambda now: created.append(now), clock=lambda: datetime(2026, 10, 20)
        )
        with self.assertRaises(ValueError):
            orchestrator.run(ResearchRequest(task="analyze"))
        self.assertEqual(created, [])

    def test_ids_and_utc_timestamps_are_unique_and_aware(self):
        orchestrator = ResearchOrchestrator(lambda now: make_team([]))
        first = orchestrator.run(ResearchRequest(task="first"))
        second = orchestrator.run(ResearchRequest(task="second"))
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(first.started_at.tzinfo, timezone.utc)
