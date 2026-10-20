"""Offline regression tests: no API keys, networking, or real trades needed."""
import importlib.util
import pathlib
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKERS_FILE = ROOT / "autohedge" / "workers.py"


class FakeAgent:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.memory = []
        self.instances.append(self)

    def run(self, task=None):
        self.memory.append(task)
        return "mock response"


def load_workers():
    """Load workers with stubbed imports to avoid real Swarms API calls."""
    fake_swarms = types.ModuleType("swarms")
    fake_swarms.Agent = FakeAgent
    fake_prompts = types.ModuleType("autohedge.prompts")
    for name in ("DIRECTOR_PROMPT", "EXECUTION_PROMPT", "QUANT_PROMPT",
                 "RISK_PROMPT", "SENTIMENT_PROMPT"):
        setattr(fake_prompts, name, name)
    fake_exa = types.ModuleType("autohedge.tools.exa_search_tool")
    fake_exa.exa_search = lambda query: query
    fake_package = types.ModuleType("autohedge")
    fake_package.__path__ = []
    fake_tools = types.ModuleType("autohedge.tools")
    fake_tools.__path__ = []

    patched = {"swarms": fake_swarms, "autohedge": fake_package,
               "autohedge.prompts": fake_prompts, "autohedge.tools": fake_tools,
               "autohedge.tools.exa_search_tool": fake_exa}
    with patch.dict(sys.modules, patched):
        spec = importlib.util.spec_from_file_location("isolated_workers", WORKERS_FILE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class AgentLifecycleTests(unittest.TestCase):
    def test_fresh_agents_on_every_cycle(self):
        workers = load_workers()
        first = workers.build_director_agent()
        first.run("previous research")
        second = workers.build_director_agent()
        self.assertIsNot(first, second)
        self.assertEqual(second.memory, [])
        self.assertEqual(len(first.kwargs["handoffs"]), 4)
        self.assertFalse(set(map(id, first.kwargs["handoffs"])) &
                         set(map(id, second.kwargs["handoffs"])))

    def test_time_is_passed_to_all_agents(self):
        workers = load_workers()
        now = datetime(2026, 10, 20, 8, 45, tzinfo=timezone.utc)
        director = workers.build_director_agent(now=now)
        for agent in [director, *director.kwargs["handoffs"]]:
            self.assertIn("October 20, 2026 at 08:45 UTC",
                          agent.kwargs["system_prompt"])

    def test_import_does_not_initialize_agents(self):
        FakeAgent.instances.clear()
        load_workers()
        self.assertEqual(FakeAgent.instances, [])


if __name__ == "__main__":
    unittest.main()
