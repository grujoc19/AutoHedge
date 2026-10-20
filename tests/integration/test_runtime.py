"""Real installed packages, with outbound socket connections forbidden."""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class RuntimeTests(unittest.TestCase):
    def run_offline(self, code):
        child_env = os.environ.copy()
        child_env.update(
            SWARMS_TELEMETRY_ON="false",
            LITELLM_LOCAL_MODEL_COST_MAP="True",
        )
        for name in (
            "OPENAI_API_KEY",
            "AUTOHEDGE_OPENAI_API_KEY",
            "EXA_API_KEY",
            "JUPITER_API_KEY",
            "SOLANA_PRIVATE_KEY",
            "MASSIVE_API_KEY",
        ):
            child_env.pop(name, None)
        with tempfile.TemporaryDirectory() as directory:
            # Stop dotenv discovery before it reaches a developer's real .env.
            pathlib.Path(directory, ".env").touch()
            child_env["WORKSPACE_DIR"] = str(pathlib.Path(directory) / "workspace")
            # Select this checkout even if Python has another editable install.
            child_env["PYTHONPATH"] = str(ROOT)
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    """
import socket
network_attempts = []
def deny_network(*args, **kwargs):
    network_attempts.append(True)
    raise AssertionError('Runtime smoke tests must not access the network')
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
socket.create_connection = deny_network
"""
                    + code
                    + "\nassert not network_attempts, 'Unexpected network attempt'\n",
                ],
                cwd=directory,
                env=child_env,
                capture_output=True,
                text=True,
                timeout=90,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_cli_help_and_version_do_not_load_agents(self):
        for argument in ("--help", "--version"):
            with self.subTest(argument=argument):
                output = self.run_offline(f"""
import sys
from autohedge.cli import main
sys.argv = ['autohedge', {argument!r}]
try:
    main()
except SystemExit as exc:
    assert exc.code == 0
assert 'swarms' not in sys.modules
assert 'autohedge.workers' not in sys.modules
assert 'solders' not in sys.modules
""")
                self.assertIn("autohedge", output.lower())
                self.assertNotIn("OPENAI_API_KEY", output)

    def test_real_agents_conversation_and_tool_schemas(self):
        output = self.run_offline("""
import json
import sys
import tempfile
from datetime import datetime, timezone
from autohedge import AutoHedge
from autohedge.workers import build_agent_team
first = build_agent_team(now=datetime(2026, 10, 20, 8, 45, tzinfo=timezone.utc))
second = build_agent_team()
assert first is not second
for role in ('director', 'quant', 'sentiment', 'risk'):
    a, b = getattr(first, role), getattr(second, role)
    assert a is not b
    assert a.short_memory is not b.short_memory
    assert not a.handoffs
    assert not a.print_on
assert 'October 20, 2026 at 08:45 UTC' in first.director.system_prompt
assert any(s['function']['name'] == 'exa_search' for s in first.sentiment.tools_list_dictionary)
assert 'autohedge.tools.ultra_tools' not in sys.modules
for a in (first.director, first.quant, first.sentiment, first.risk):
    assert not any(getattr(t, '__name__', '') in ('execute_trade', 'get_order') for t in a.tools or [])
with tempfile.TemporaryDirectory() as output_dir:
    system = AutoHedge(output_dir=output_dir)
    system.conversation.add(role='user', content='offline integration check')
    assert 'offline integration check' in str(system.conversation.return_messages_as_list())
print(json.dumps({'agents': 4, 'signing_tools': 0}))
""")
        self.assertIn(json.dumps({"agents": 4, "signing_tools": 0}), output)

    def test_complete_cycle_through_real_swarms_with_only_model_responses_replaced(
        self,
    ):
        self.run_offline("""
import tempfile
from autohedge import AutoHedge
from autohedge.config import Settings
from autohedge.workers import build_agent_team
settings = Settings(search_enabled=False)
calls = []
def factory(now):
    team = build_agent_team(now, settings=settings)
    for role in ('director', 'quant', 'sentiment', 'risk'):
        agent = getattr(team, role)
        def response(*args, _role=role, **kwargs):
            calls.append(_role)
            return 'offline research by ' + _role
        agent.call_llm = response
    return team
with tempfile.TemporaryDirectory() as directory:
    system = AutoHedge(output_dir=directory, settings=settings, team_factory=factory)
    report = system.run_report('offline real-stack research')
assert calls == ['director', 'quant', 'sentiment', 'risk', 'director']
assert len(report.stages) == 5
assert report.stages[-1].content == 'offline research by director'
assert report.execution_enabled is False
""")


if __name__ == "__main__":
    unittest.main()
