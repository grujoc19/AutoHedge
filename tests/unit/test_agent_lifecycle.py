"""Fresh configured role objects, without any model calls."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from autohedge.config import Settings
from autohedge.errors import ConfigurationError, ProviderError
from autohedge.workers import build_agent_team, build_director_agent


class FakeAgent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.memory = []


class AgentLifecycleTests(unittest.TestCase):
    def test_openai_profiles_control_each_existing_role_without_new_agents(self):
        settings = Settings(
            search_enabled=False,
            role_models={
                role: {
                    "target": {"provider": "openai", "model": "model-" + role},
                    "max_output_tokens": 100,
                    "timeout_seconds": 8,
                }
                for role in (
                    "quant",
                    "news_intelligence",
                    "trading_director",
                    "risk_manager",
                )
            },
        )
        with patch("autohedge.workers.Agent", FakeAgent):
            team = build_agent_team(settings=settings)
        for name, role in (
            ("director", "trading_director"),
            ("quant", "quant"),
            ("sentiment", "news_intelligence"),
            ("risk", "risk_manager"),
        ):
            worker = getattr(team, name)
            self.assertEqual(worker.agent.kwargs["model_name"], "model-" + role)
            self.assertEqual(worker.agent.kwargs["max_tokens"], 100)
            self.assertEqual(worker.bridge.config.timeout_seconds, 8)
            self.assertIsNone(worker.agent.kwargs["tools"])
            self.assertEqual(worker.agent.kwargs["max_loops"], 1)

    def test_role_overrides_fail_before_any_legacy_agent_is_constructed(self):
        for role in ("quant", "news_intelligence", "trading_director", "risk_manager"):
            settings = Settings(
                role_models={
                    role: {
                        "target": {"provider": "deepseek", "model": "new-role-model"}
                    }
                }
            )
            with patch("autohedge.workers.Agent") as agent:
                with (
                    self.subTest(role=role),
                    self.assertRaisesRegex(ConfigurationError, "not implemented"),
                ):
                    build_agent_team(settings=settings)
                agent.assert_not_called()

    def test_tool_failure_is_recorded_even_if_a_framework_swallows_the_exception(self):
        team = self.build()
        tool = team.sentiment.search
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
        tool = team.sentiment.search
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
        first.director.agent.memory.append("old research")
        first.provider_failures.append("exa")
        self.assertEqual(second.director.agent.memory, [])
        self.assertEqual(second.provider_failures, [])

    def test_utc_time_is_passed_to_all_agents(self):
        now = datetime(2026, 10, 20, 10, 45, tzinfo=timezone(timedelta(hours=2)))
        team = self.build(now=now)
        for agent in (team.director, team.quant, team.sentiment, team.risk):
            self.assertIn(
                "October 20, 2026 at 08:45 UTC", agent.agent.kwargs["system_prompt"]
            )
            self.assertNotIn("handoffs", agent.agent.kwargs)
            self.assertFalse(agent.agent.kwargs["print_on"])
            self.assertEqual(agent.agent.kwargs["retry_attempts"], 1)
            self.assertEqual(agent.agent.kwargs["output_type"], "final")

    def test_naive_time_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build(now=datetime(2026, 10, 20))

    def test_standalone_director_keeps_validated_runner_and_sdk_access(self):
        with patch("autohedge.workers.Agent", FakeAgent):
            director = build_director_agent(settings=Settings())
        self.assertEqual(director.agent.kwargs["agent_name"], "Trading-Director")
        self.assertIs(director.bridge, director.agent.kwargs["llm"])
        for model in ("openai/", "anthropic/claude"):
            with self.assertRaises(ConfigurationError):
                Settings(model=model).require_research_model_configuration()

    def test_model_and_timeout_settings_are_used_without_signing_tools(self):
        with patch("autohedge.workers.Agent", FakeAgent):
            team = build_agent_team(
                settings=Settings(model="configured-model", request_timeout=12)
            )
        self.assertEqual(team.quant.agent.kwargs["model_name"], "configured-model")
        self.assertEqual(team.quant.bridge.config.timeout_seconds, 12)
        self.assertIsNone(team.director.agent.kwargs["tools"])
        self.assertEqual(team.sentiment.search.__name__, "exa_search")
        self.assertIsNone(team.sentiment.agent.kwargs["tools"])
