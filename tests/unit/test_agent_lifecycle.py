"""Fresh configured role objects, without any model calls."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from autohedge.config import Settings
from autohedge.errors import ProviderError
from autohedge.workers import build_agent_team


class FakeAgent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.memory = []


class AgentLifecycleTests(unittest.TestCase):
    def test_tool_failure_is_recorded_even_if_a_framework_swallows_the_exception(self):
        team = self.build()
        tool = team.sentiment.kwargs["tools"][0]
        with patch(
            "autohedge.workers.exa_search",
            side_effect=ProviderError("exa", "unavailable"),
        ):
            with self.assertRaises(ProviderError):
                tool("news")
        self.assertEqual(team.provider_failures, ["exa"])

    def build(self, **kwargs):
        with patch("autohedge.workers.Agent", FakeAgent):
            return build_agent_team(settings=Settings(), **kwargs)

    def test_invalid_tool_query_is_recorded_even_if_swallowed(self):
        team = self.build()
        tool = team.sentiment.kwargs["tools"][0]
        with patch(
            "autohedge.workers.exa_search", side_effect=ValueError("Invalid query")
        ):
            with self.assertRaises(ValueError):
                tool("")
        self.assertEqual(team.provider_failures, ["exa"])

    def test_fresh_roles_and_provider_state_on_every_cycle(self):
        first, second = self.build(), self.build()
        for role in ("director", "quant", "sentiment", "risk"):
            self.assertIsNot(getattr(first, role), getattr(second, role))
        first.director.memory.append("old research")
        first.provider_failures.append("exa")
        self.assertEqual(second.director.memory, [])
        self.assertEqual(second.provider_failures, [])

    def test_utc_time_is_passed_to_all_agents(self):
        now = datetime(2026, 10, 20, 10, 45, tzinfo=timezone(timedelta(hours=2)))
        team = self.build(now=now)
        for agent in (team.director, team.quant, team.sentiment, team.risk):
            self.assertIn(
                "October 20, 2026 at 08:45 UTC", agent.kwargs["system_prompt"]
            )
            self.assertNotIn("handoffs", agent.kwargs)
            self.assertFalse(agent.kwargs["print_on"])
            self.assertEqual(agent.kwargs["retry_attempts"], 1)
            self.assertEqual(agent.kwargs["output_type"], "final")

    def test_naive_time_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build(now=datetime(2026, 10, 20))

    def test_model_and_timeout_settings_are_used_without_signing_tools(self):
        with patch("autohedge.workers.Agent", FakeAgent):
            team = build_agent_team(
                settings=Settings(model="configured-model", request_timeout=12)
            )
        self.assertEqual(team.quant.kwargs["model_name"], "configured-model")
        self.assertEqual(team.quant.kwargs["llm_args"]["timeout"], 12)
        self.assertIsNone(team.director.kwargs["tools"])
        self.assertEqual(
            [f.__name__ for f in team.sentiment.kwargs["tools"]], ["exa_search"]
        )
