"""Exercise the public research path with deterministic fake model roles."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autohedge.config import Settings
from autohedge.errors import ConfigurationError, ResearchError
from autohedge.main import AutoHedge
from autohedge.orchestration import AgentTeam

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


class FakeAgent:
    def __init__(self, calls, name, fail=False):
        self.calls, self.name, self.fail = calls, name, fail

    def run(self, task):
        data = json.loads(task)
        self.calls.append((self.name, data))
        if self.fail:
            raise RuntimeError("raw provider error must not escape")
        return f"{self.name}: {data['request']['task']}"


def make_team(calls, fail=None):
    return AgentTeam(
        **{
            role: FakeAgent(calls, role, fail == role)
            for role in ("director", "quant", "sentiment", "risk")
        }
    )


class RunLifecycleTests(unittest.TestCase):
    def test_local_profile_cannot_silently_call_openai_through_the_default_pipeline(
        self,
    ):
        settings = Settings(
            role_models={
                "quant": {"target": {"provider": "ollama", "model": "local-quant"}}
            }
        )
        settings.require_model_credentials("quant")
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("autohedge.workers.build_agent_team") as factory,
        ):
            system = AutoHedge(output_dir=directory, settings=settings)
            with self.assertRaisesRegex(ConfigurationError, "not implemented"):
                system.run_report("research")
            factory.assert_not_called()
            self.assertIsNone(system.last_report)

    def test_missing_credentials_fail_before_default_team_construction(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("autohedge.workers.build_agent_team") as factory,
        ):
            system = AutoHedge(output_dir=directory, settings=Settings())
            with self.assertRaisesRegex(ConfigurationError, "OPENAI_API_KEY"):
                system.run_report("research")
            factory.assert_not_called()
            self.assertIsNone(system.last_report)

    def test_each_run_builds_a_fresh_team_and_resets_cycle_output(self):
        calls, teams = [], []

        def factory(now):
            self.assertEqual(now, NOW)
            team = make_team(calls)
            teams.append(team)
            return team

        with tempfile.TemporaryDirectory() as directory:
            system = AutoHedge(
                output_dir=directory,
                team_factory=factory,
                settings=Settings(),
                clock=lambda: NOW,
            )
            first = system.run_report("first research")
            second = system.run_report("second research")
            self.assertNotEqual(first.run_id, second.run_id)
            self.assertNotIn(
                "first research", str(system.conversation.return_messages_as_list())
            )
        self.assertEqual(len(teams), 2)
        self.assertIsNot(teams[0], teams[1])
        self.assertFalse(second.execution_enabled)
        self.assertEqual(len(second.warnings), 3)

    def test_failure_propagates_without_stale_report_and_retry_uses_fresh_team(self):
        calls = []
        teams = iter([make_team(calls), make_team(calls, "quant"), make_team(calls)])
        with tempfile.TemporaryDirectory() as directory:
            system = AutoHedge(
                output_dir=directory,
                settings=Settings(),
                team_factory=lambda now: next(teams),
            )
            system.run_report("previous success")
            calls.clear()
            with self.assertRaisesRegex(ResearchError, "quant") as error:
                system.run_report("failure")
            self.assertNotIn("raw provider", str(error.exception))
            self.assertIsNone(system.last_report)
            self.assertEqual([role for role, _ in calls], ["director", "quant"])
            self.assertNotIn(
                "previous success", str(system.conversation.return_messages_as_list())
            )
            self.assertEqual(system.run_report("retry").request.task, "retry")

    def test_output_formats_and_atomic_report_file(self):
        with tempfile.TemporaryDirectory() as directory:
            for output_type, expected in [("dict", dict), ("str", str), ("list", list)]:
                system = AutoHedge(
                    output_dir=str(Path(directory) / "nested"),
                    output_file_path="reports/result.json",
                    output_type=output_type,
                    settings=Settings(),
                    team_factory=lambda now: make_team([]),
                )
                result = system.run("research")
                self.assertIsInstance(result, expected)
                saved = json.loads(
                    (system.output_dir / "reports/result.json").read_text()
                )
                self.assertEqual(saved["run_id"], str(system.last_report.run_id))
                self.assertFalse(saved["execution_enabled"])
