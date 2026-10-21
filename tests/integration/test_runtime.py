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
            "COINGECKO_PRO_API_KEY",
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

    def test_real_sentiment_tool_uses_normalized_news_and_records_schema_failure(self):
        self.run_offline("""
import json
from datetime import datetime, timezone
from unittest.mock import patch
import httpx
from autohedge.config import Settings
from autohedge.data.exa import ExaNewsProvider
from autohedge.errors import ProviderError
from autohedge.workers import build_agent_team
settings = Settings(exa_api_key='fixture-key')
now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
payload = {'results': [{
    'id': 'fixture-article', 'url': 'https://news.example/solana',
    'title': 'Fixture news', 'publishedDate': '2026-10-21T11:30:00Z',
    'text': 'Untrusted excerpt'
}]}
def respond(request):
    assert request.headers['x-api-key'] == 'fixture-key'
    assert json.loads(request.content)['contents'] == {'text': {'maxCharacters': 4000}}
    return httpx.Response(200, json=payload)
team = build_agent_team(settings=settings)
tool = next(t for t in team.sentiment.tools if t.__name__ == 'exa_search')
with httpx.Client(transport=httpx.MockTransport(respond)) as client:
    provider = ExaNewsProvider(settings, client=client, clock=lambda: now)
    with patch('autohedge.tools.exa_search_tool.ExaNewsProvider', return_value=provider):
        result = json.loads(tool('Solana news'))
        assert result['results'][0]['source'] == 'exa.search.news'
        assert result['results'][0]['published_at'] == '2026-10-21T11:30:00Z'
        assert not team.provider_failures
        payload['results'][0].pop('publishedDate')
        try:
            tool('Solana news')
        except ProviderError:
            pass
        else:
            raise AssertionError('Undated news must fail')
assert team.provider_failures == ['exa']
""")

    def test_collected_and_replayed_history_reaches_real_research_quality_gate(self):
        self.run_offline("""
import json
import tempfile
from datetime import datetime, timezone
import httpx
from autohedge import AutoHedge
from autohedge.config import Settings
from autohedge.contracts import Instrument
from autohedge.data.coingecko import CoinGeckoCandleProvider
from autohedge.data.contracts import CandleRequest, MarketData
from autohedge.data.history import HistoryStore
from autohedge.data.jupiter import JupiterProvider
from autohedge.indicators import calculate_indicators, indicators_from_snapshot
from autohedge.workers import build_agent_team
now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
mint = 'So11111111111111111111111111111111111111112'
instrument = Instrument(asset_id=mint, symbol='SOL', venue='solana', quote_currency='USD')
settings = Settings(search_enabled=False, coingecko_api_key='fixture-key', jupiter_api_key='fixture-key')
before_cursors = []
def respond(request):
    if request.url.path == '/price/v3':
        return httpx.Response(200, json={mint: {'usdPrice': 101, 'decimals': 9, 'blockId': 1}})
    before = int(request.url.params['before_timestamp'])
    limit = int(request.url.params['limit'])
    before_cursors.append(before)
    rows = [[int(now.timestamp()) - i * 60, 100, 102, 99, 101, 1500.25] for i in range(1, 41)]
    rows = [row for row in rows if row[0] <= before][:limit]
    return httpx.Response(200, json={
        'data': {'id': 'fixture', 'type': 'ohlcv_request_response', 'attributes': {'ohlcv_list': rows}},
        'meta': {'base': {'address': mint}},
    })
calls = []
def factory(clock):
    team = build_agent_team(clock, settings=settings)
    for role in ('director', 'quant', 'sentiment', 'risk'):
        def response(*args, _role=role, **kwargs):
            calls.append(_role)
            return 'Offline research with supplied data: ' + _role
        getattr(team, role).call_llm = response
    return team
with tempfile.TemporaryDirectory() as directory:
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        record = CoinGeckoCandleProvider(settings, client=client, clock=lambda: now, page_size=20).fetch_history(
            CandleRequest(instrument=instrument, interval_seconds=60, count=40, as_of=now))
        quote = JupiterProvider(settings, client=client, clock=lambda: now,
            block_time_resolver=lambda block: now).fetch_quote(instrument)
    store = HistoryStore(directory + '/history')
    digest = store.save(record)
    replayed = store.replay(digest, available_at=now)
    assert len(record.pages) == 3
    assert len(before_cursors) == 3
    assert replayed.candles[-1].volume_base is None
    assert str(replayed.candles[-1].volume_quote) == '1500.25'
    features = calculate_indicators(replayed, as_of=now)
    assert features == indicators_from_snapshot(store.load(digest), as_of=now)
    assert features.latest.ready
    assert features.latest.rsi == 50
    assert features.latest.macd == 0
    assert features.latest.volatility == 0
    assert not features.points[32].ready
    assert features.points[33].ready
    data = MarketData(quote=quote, history=replayed)
    engine = AutoHedge(output_dir=directory + '/reports', settings=settings,
        team_factory=factory, clock=lambda: now)
    report = engine.run_report('Analyze supplied SOL history', instrument=instrument, market_data=data)
assert calls == ['director', 'quant', 'sentiment', 'risk', 'director']
assert report.market_quality.candle_count == 40
assert report.market_quality.price_deviation_bps == 0
assert report.request.market_data.history == replayed
assert report.execution_enabled is False
""")

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
