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
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "DEEPSEEK_API_KEY",
            "AUTOHEDGE_OPENAI_API_KEY",
            "EXA_API_KEY",
            "JUPITER_API_KEY",
            "SOLANA_PRIVATE_KEY",
            "MASSIVE_API_KEY",
            "COINGECKO_PRO_API_KEY",
        ):
            child_env.pop(name, None)
        for name in tuple(child_env):
            if name.startswith("AUTOHEDGE_"):
                child_env.pop(name)
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

    def test_model_boundary_needs_no_sdk_credentials_or_network(self):
        output = self.run_offline("""
import sys
from autohedge.ai import (
    ModelMessage, ModelReply, ModelRequest, ModelTarget, TokenUsage, complete_model,
)
class ScriptedProvider:
    calls = 0
    def complete(self, request):
        self.calls += 1
        return ModelReply(
            request_id=request.request_id,
            target=request.target,
            finish_reason='complete',
            content='Offline evidence assessment.',
            usage=TokenUsage(status='unknown'),
        )
provider = ScriptedProvider()
request = ModelRequest(
    target=ModelTarget(provider='ollama', model='offline-fixture'),
    messages=(ModelMessage(role='user', content='Assess supplied evidence.'),),
)
result = complete_model(provider, request)
assert provider.calls == 1
assert result.reply.usage.status == 'unknown'
assert result.reply.usage.total_tokens is None
assert result.reply.target == request.target
for module in ('swarms', 'litellm', 'openai', 'solders', 'autohedge.workers'):
    assert module not in sys.modules, module
print('Independent model boundary passed')
""")
        self.assertIn("Independent model boundary passed", output)

    def test_role_configuration_drives_offline_gateway_without_cloud_credentials(self):
        output = self.run_offline("""
import os
import sys
from autohedge.ai import AgentRole, ModelMessage, ModelReply, ModelRequest, TokenUsage, complete_model
from autohedge.config import load_settings
before = dict(os.environ)
environ = {'AUTOHEDGE_SEARCH_ENABLED': 'false'}
for role in AgentRole:
    prefix = 'AUTOHEDGE_' + role.value.upper() + '_'
    environ[prefix + 'PROVIDER'] = 'ollama'
    environ[prefix + 'MODEL'] = 'fixture-' + role.value
    environ[prefix + 'MAX_OUTPUT_TOKENS'] = '200'
    environ[prefix + 'TIMEOUT'] = '10'
settings = load_settings(environ=environ)
class ScriptedProvider:
    calls = 0
    def complete(self, request):
        self.calls += 1
        assert request.target.provider == 'ollama'
        assert request.max_output_tokens == 200
        assert request.timeout_seconds == 10
        return ModelReply(
            request_id=request.request_id, target=request.target,
            finish_reason='complete', content='Offline assessment only.',
            usage=TokenUsage(status='unknown'),
        )
provider = ScriptedProvider()
for role in AgentRole:
    settings.require_model_credentials(role)
    assert settings.model_api_key(role) is None
    request = ModelRequest(
        messages=(ModelMessage(role='user', content='Frozen evidence'),),
        **settings.model_for(role).model_dump(),
    )
    result = complete_model(provider, request)
    assert result.reply.target.model == 'fixture-' + role.value
assert provider.calls == 6
settings.require_research_credentials()
assert dict(os.environ) == before
for module in ('swarms', 'litellm', 'openai', 'anthropic', 'solders'):
    assert module not in sys.modules, module
print('Role configuration and offline gateway passed')
""")
        self.assertIn("Role configuration and offline gateway passed", output)

    def test_public_openai_pipeline_uses_profiles_and_one_read_only_news_lookup(self):
        self.run_offline("""
import json
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch
import httpx
from autohedge import AutoHedge
from autohedge.ai.openai import OpenAIProvider
from autohedge.config import Settings
from autohedge.data.exa import ExaNewsProvider
now=datetime(2026,10,21,12,tzinfo=timezone.utc)
settings=Settings(openai_api_key='fixture-model-key',exa_api_key='fixture-news-key',role_models={
    'quant':{'target':{'provider':'openai','model':'gpt-4.1-quant'},'max_output_tokens':100,'timeout_seconds':7}})
models=[]
queries=[]
def respond(request):
    body=json.loads(request.content)
    if request.url.host=='api.exa.ai':
        queries.append(body['query'])
        assert request.headers['x-api-key']=='fixture-news-key'
        return httpx.Response(200,json={'results':[{'id':'story','url':'https://news.example/story',
            'title':'Fixture news','publishedDate':'2026-10-21T11:55:00Z','text':'Untrusted excerpt'}]})
    assert request.url.host=='api.openai.com'
    assert request.headers['authorization']=='Bearer fixture-model-key'
    assert not body.get('tools')
    assert body['messages'][0]['role']=='system'
    assert 'This is research only.' in body['messages'][0]['content']
    models.append(body)
    return httpx.Response(200,json={'model':body['model']+'-fixture','choices':[{'index':0,'finish_reason':'stop',
        'message':{'role':'assistant','content':'offline research '+body['model']}}],
        'usage':{'prompt_tokens':10,'completion_tokens':2}})
with httpx.Client(transport=httpx.MockTransport(respond)) as client:
    with tempfile.TemporaryDirectory() as directory:
        with patch('autohedge.workers.OpenAIProvider',side_effect=lambda settings:OpenAIProvider(settings,client=client)), patch(
            'autohedge.tools.exa_search_tool.ExaNewsProvider',return_value=ExaNewsProvider(settings,client=client,clock=lambda:now)):
            engine=AutoHedge(settings=settings,output_dir=directory,output_file_path='report.json',clock=lambda:now)
            report=engine.run_report('Public offline research')
assert len(models)==5
assert queries==['Public offline research']
assert models[1]['model']=='gpt-4.1-quant'
assert models[1]['max_completion_tokens']==100
assert all(len(stage.model_calls)==1 for stage in report.stages)
assert len({stage.model_calls[0].request_id for stage in report.stages})==5
assert all(stage.model_calls[0].usage.total_tokens==12 for stage in report.stages)
sentiment=json.loads(models[2]['messages'][-1]['content'])
assert sentiment['supplemental_news']['results'][0]['source']=='exa.search.news'
for sent in models:
    for message in sent['messages']:
        if message['role']=='user':
            data=json.loads(message['content'])
            assert all('model_calls' not in prior for prior in data.get('previous_results',[]))
assert report.execution_enabled is False
""")

    def test_real_swarms_stops_after_first_rejected_openai_response(self):
        self.run_offline("""
import tempfile
from unittest.mock import patch
import httpx
from autohedge import AutoHedge
from autohedge.ai.openai import OpenAIProvider
from autohedge.config import Settings
from autohedge.errors import ResearchError
for status,reason in ((429,'stop'),(200,'length'),(200,'content_filter'),(200,'tool_calls')):
    requests=[]
    def respond(request):
        requests.append(request)
        return httpx.Response(status,json={'model':'fixture','choices':[{'index':0,'finish_reason':reason,
            'message':{'role':'assistant','content':'private rejected output'}}],
            'usage':{'prompt_tokens':10,'completion_tokens':2}})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with tempfile.TemporaryDirectory() as directory:
            with patch('autohedge.workers.OpenAIProvider',side_effect=lambda settings:OpenAIProvider(settings,client=client)):
                engine=AutoHedge(output_dir=directory,settings=Settings(openai_api_key='fixture',search_enabled=False))
                try:
                    engine.run_report('offline failing research')
                except ResearchError as error:
                    assert error.stage=='thesis'
                    assert 'private rejected output' not in str(error)
                else:
                    raise AssertionError('Rejected response must stop the research cycle')
                assert engine.last_report is None
    assert len(requests)==1, len(requests)
""")

    def test_public_local_and_mixed_pipelines_use_exact_role_backends_and_audits(self):
        self.run_offline("""
import json
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch
import httpx
from autohedge import AutoHedge
from autohedge.ai.openai import OpenAIProvider
from autohedge.ai.ollama import OllamaProvider
from autohedge.config import Settings
now=datetime(2026,10,21,12,tzinfo=timezone.utc)
roles=('trading_director','quant','news_intelligence','risk_manager')
for mixed in (False, True):
    profiles={role:{'target':{'provider':'openai' if mixed and role=='trading_director' else 'ollama',
                'model':'model-'+role},'max_output_tokens':100+i,'timeout_seconds':7+i}
              for i,role in enumerate(roles)}
    settings=Settings(role_models=profiles,search_enabled=False,
        openai_api_key='fixture-cloud' if mixed else None)
    settings.require_research_credentials()
    calls=[]
    def respond(request):
        body=json.loads(request.content)
        calls.append((request,body))
        assert not body.get('tools')
        assert body['messages'][0]['role']=='system'
        assert 'This is research only.' in body['messages'][0]['content']
        if request.url.host=='api.openai.com':
            assert mixed
            assert request.headers['authorization']=='Bearer fixture-cloud'
            return httpx.Response(200,json={'model':body['model'],'choices':[{
                'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'offline cloud evidence'}}],
                'usage':{'prompt_tokens':10,'completion_tokens':2}})
        assert request.url.host=='127.0.0.1'
        assert request.url.path=='/api/chat'
        assert 'authorization' not in request.headers and 'x-api-key' not in request.headers
        assert body['truncate'] is False and body['shift'] is False
        return httpx.Response(200,json={'model':body['model']+':latest','done':True,'done_reason':'stop',
            'message':{'role':'assistant','content':'offline local evidence'},'prompt_eval_count':10,'eval_count':2})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with tempfile.TemporaryDirectory() as directory:
            with patch('autohedge.workers.OpenAIProvider',side_effect=lambda settings:OpenAIProvider(settings,client=client)), patch(
                'autohedge.workers.OllamaProvider',side_effect=lambda settings:OllamaProvider(settings,client=client)):
                engine=AutoHedge(settings=settings,output_dir=directory,clock=lambda:now)
                report=engine.run_report('Frozen supplied evidence')
    assert len(calls)==5
    expected_roles=(roles[0],roles[1],roles[2],roles[3],roles[0])
    assert [body['model'] for request,body in calls]==['model-'+role for role in expected_roles]
    assert sum(request.url.host=='api.openai.com' for request,body in calls)==(2 if mixed else 0)
    for stage,(request,body),role in zip(report.stages,calls,expected_roles):
        audit,=stage.model_calls
        config=settings.model_for(role)
        assert audit.role.value==role and audit.target==config.target
        assert audit.usage.total_tokens==12
        assert audit.max_output_tokens==config.max_output_tokens
        assert audit.timeout_seconds==config.timeout_seconds
        assert request.extensions['timeout']['read']==config.timeout_seconds
        limit=body['max_completion_tokens'] if config.target.provider=='openai' else body['options']['num_predict']
        assert limit==config.max_output_tokens
    assert len({stage.model_calls[0].request_id for stage in report.stages})==5
    assert report.execution_enabled is False
""")

    def test_failed_local_quant_never_uses_cloud_fallback_or_finishes_report(self):
        self.run_offline("""
import json
import tempfile
from unittest.mock import patch
import httpx
from autohedge import AutoHedge
from autohedge.ai.openai import OpenAIProvider
from autohedge.ai.ollama import OllamaProvider
from autohedge.config import Settings
from autohedge.errors import ResearchError
settings=Settings(openai_api_key='fixture-cloud',search_enabled=False,role_models={
    'quant':{'target':{'provider':'ollama','model':'offline-quant'}}})
for failure in ('timeout','unavailable','missing_model','bad_json','length','tool_calls','remote'):
    calls=[]
    def respond(request):
        calls.append(request)
        if request.url.host=='api.openai.com':
            assert len(calls)==1, 'No Cloud fallback or downstream call is allowed'
            return httpx.Response(200,json={'model':'fixture-director','choices':[{
                'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'offline thesis'}}]})
        assert request.url.host=='127.0.0.1' and request.url.path=='/api/chat'
        if failure=='timeout':
            raise httpx.ReadTimeout('private-server-body')
        if failure=='unavailable':
            raise httpx.ConnectError('private-server-body')
        if failure=='missing_model':
            return httpx.Response(404,text='private-server-body')
        if failure=='bad_json':
            return httpx.Response(200,text='private-server-body')
        raw={'model':'offline-quant','done':True,'done_reason':'length' if failure=='length' else 'stop',
            'message':{'role':'assistant','content':'private-server-body'}}
        if failure=='tool_calls':
            raw['message']['tool_calls']=[{'function':{'name':'execute_trade'}}]
        if failure=='remote':
            raw['remote_host']='https://cloud.example'
        return httpx.Response(200,json=raw)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with tempfile.TemporaryDirectory() as directory:
            with patch('autohedge.workers.OpenAIProvider',side_effect=lambda settings:OpenAIProvider(settings,client=client)), patch(
                'autohedge.workers.OllamaProvider',side_effect=lambda settings:OllamaProvider(settings,client=client)):
                engine=AutoHedge(settings=settings,output_dir=directory)
                try:
                    engine.run_report('offline research')
                except ResearchError as error:
                    assert error.stage=='quant'
                    assert 'private-server-body' not in str(error)
                else:
                    raise AssertionError('Failed local model must stop the cycle')
                assert engine.last_report is None
    assert len(calls)==2, (failure,len(calls))
""")

    def test_public_anthropic_and_three_backend_research_preserve_limits_and_usage(
        self,
    ):
        self.run_offline("""
import json
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch
import httpx
from autohedge import AutoHedge
from autohedge.ai.anthropic import AnthropicProvider
from autohedge.ai.openai import OpenAIProvider
from autohedge.ai.ollama import OllamaProvider
from autohedge.config import Settings
roles=('trading_director','quant','news_intelligence','risk_manager')
now=datetime(2026,10,21,12,tzinfo=timezone.utc)
for mixed in (False,True):
    backends=('anthropic','ollama','openai','ollama') if mixed else ('anthropic',)*4
    profiles={role:{'target':{'provider':backend,'model':('anthropic/' if backend=='anthropic' else '')+'model-'+role},
        'max_output_tokens':100+i,'timeout_seconds':7+i} for i,(role,backend) in enumerate(zip(roles,backends))}
    settings=Settings(role_models=profiles,search_enabled=False,anthropic_api_key='fixture-anthropic',
        openai_api_key='fixture-openai' if mixed else None)
    settings.require_research_credentials()
    calls=[]
    def respond(request):
        body=json.loads(request.content)
        calls.append((request,body))
        assert not body.get('tools') and body['stream'] is False
        if request.url.host=='api.anthropic.com':
            assert request.url.path=='/v1/messages'
            assert request.headers['x-api-key']=='fixture-anthropic'
            assert 'authorization' not in request.headers
            assert not body['model'].startswith('anthropic/')
            assert 'system' in body and 'thinking' not in body and 'cache_control' not in body
            assert 'This is research only.' in body['system']
            for message in body['messages']:
                assert message['role'] in ('user','assistant')
                assert all(block['type']=='text' for block in message['content'])
            usage={'input_tokens':10,'output_tokens':2,'cache_creation_input_tokens':3,'cache_read_input_tokens':4}
            if body['model'].endswith('risk_manager'):
                usage.pop('cache_read_input_tokens')
            return httpx.Response(200,json={'type':'message','role':'assistant','model':body['model']+'-revision',
                'content':[{'type':'text','text':'offline Anthropic evidence'}],'stop_reason':'end_turn','usage':usage})
        if request.url.host=='api.openai.com':
            assert mixed and request.headers['authorization']=='Bearer fixture-openai'
            return httpx.Response(200,json={'model':body['model'],'choices':[{'index':0,'finish_reason':'stop',
                'message':{'role':'assistant','content':'offline OpenAI evidence'}}],
                'usage':{'prompt_tokens':10,'completion_tokens':2}})
        assert mixed and request.url.host=='127.0.0.1'
        assert 'authorization' not in request.headers and 'x-api-key' not in request.headers
        return httpx.Response(200,json={'model':body['model'],'done':True,'done_reason':'stop',
            'message':{'role':'assistant','content':'offline local evidence'},'prompt_eval_count':10,'eval_count':2})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client, tempfile.TemporaryDirectory() as directory:
        with patch('autohedge.workers.AnthropicProvider',side_effect=lambda settings:AnthropicProvider(settings,client=client)), patch(
            'autohedge.workers.OpenAIProvider',side_effect=lambda settings:OpenAIProvider(settings,client=client)), patch(
            'autohedge.workers.OllamaProvider',side_effect=lambda settings:OllamaProvider(settings,client=client)):
            engine=AutoHedge(settings=settings,output_dir=directory,clock=lambda:now)
            report=engine.run_report('Supplied frozen evidence')
    assert len(calls)==5
    for stage,(sent,body),role in zip(report.stages,calls,(roles[0],roles[1],roles[2],roles[3],roles[0])):
        audit,=stage.model_calls
        config=settings.model_for(role)
        assert audit.role.value==role and audit.target==config.target
        assert audit.timeout_seconds==config.timeout_seconds==sent.extensions['timeout']['read']
        assert audit.max_output_tokens==config.max_output_tokens
        if config.target.provider=='anthropic':
            assert body['max_tokens']==config.max_output_tokens
            assert audit.resolved_model==body['model']+'-revision'
            if role=='risk_manager':
                assert audit.usage.status=='partial' and audit.usage.input_tokens is None
            else:
                assert audit.usage.input_tokens==17 and audit.usage.total_tokens==19
        elif config.target.provider=='openai':
            assert body['max_completion_tokens']==config.max_output_tokens
        else:
            assert body['options']['num_predict']==config.max_output_tokens
    assert len({stage.model_calls[0].request_id for stage in report.stages})==5
    assert report.execution_enabled is False
""")

    def test_failed_anthropic_stage_never_retries_switches_backend_or_finishes_report(
        self,
    ):
        self.run_offline("""
import json
import tempfile
from unittest.mock import patch
import httpx
from autohedge import AutoHedge
from autohedge.ai.anthropic import AnthropicProvider
from autohedge.ai.openai import OpenAIProvider
from autohedge.config import Settings
from autohedge.errors import ResearchError
settings=Settings(openai_api_key='fixture-openai',anthropic_api_key='fixture-anthropic',search_enabled=False,
    role_models={'quant':{'target':{'provider':'anthropic','model':'claude-fixture'}}})
for failure in ('auth','rate_limit','overloaded','timeout','bad_json','refusal','max_tokens','tool_use','pause_turn','thinking'):
    calls=[]
    def respond(request):
        calls.append(request)
        if request.url.host=='api.openai.com':
            assert len(calls)==1, 'No fallback or downstream call is allowed'
            return httpx.Response(200,json={'model':'fixture','choices':[{'index':0,'finish_reason':'stop',
                'message':{'role':'assistant','content':'offline thesis'}}]})
        assert request.url.host=='api.anthropic.com'
        if failure in ('auth','rate_limit','overloaded'):
            return httpx.Response({'auth':401,'rate_limit':429,'overloaded':529}[failure],text='private-body')
        if failure=='timeout':
            raise httpx.ReadTimeout('private-body')
        if failure=='bad_json':
            return httpx.Response(200,text='private-body')
        return httpx.Response(200,json={'type':'message','role':'assistant','model':'fixture',
            'stop_reason':'end_turn' if failure=='thinking' else failure,
            'content':[{'type':'thinking','thinking':'private-body'}] if failure=='thinking' else [{'type':'text','text':'private-body'}],
            'usage':{'input_tokens':10,'output_tokens':2,'cache_creation_input_tokens':0,'cache_read_input_tokens':0}})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client, tempfile.TemporaryDirectory() as directory:
        with patch('autohedge.workers.AnthropicProvider',side_effect=lambda settings:AnthropicProvider(settings,client=client)), patch(
            'autohedge.workers.OpenAIProvider',side_effect=lambda settings:OpenAIProvider(settings,client=client)):
            engine=AutoHedge(settings=settings,output_dir=directory)
            try:
                engine.run_report('Offline failing research')
            except ResearchError as error:
                assert error.stage=='quant' and 'private-body' not in str(error)
            else:
                raise AssertionError('Rejected Anthropic response must stop the cycle')
            assert engine.last_report is None
    assert len(calls)==2, (failure,len(calls))
""")

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
    a, b = getattr(first, role).agent, getattr(second, role).agent
    assert a is not b
    assert a.short_memory is not b.short_memory
    assert not a.handoffs
    assert not a.print_on
assert 'October 20, 2026 at 08:45 UTC' in first.director.agent.system_prompt
assert first.sentiment.search.__name__ == 'exa_search'
assert 'autohedge.tools.ultra_tools' not in sys.modules
for a in (first.director.agent, first.quant.agent, first.sentiment.agent, first.risk.agent):
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
tool = team.sentiment.search
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
from unittest.mock import patch
from datetime import datetime, timezone
import httpx
from autohedge import AutoHedge
from autohedge.config import Settings
from autohedge.contracts import Instrument
from autohedge.data.coingecko import CoinGeckoCandleProvider
from autohedge.data.exa import ExaNewsProvider
from autohedge.data.contracts import CandleRequest, MarketData
from autohedge.data.history import HistoryStore
from autohedge.data.jupiter import JupiterProvider
from autohedge.indicators import calculate_indicators, indicators_from_snapshot
from autohedge.workers import build_agent_team
from autohedge.snapshots import collect_research_snapshot, parse_research_snapshot
now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
mint = 'So11111111111111111111111111111111111111112'
instrument = Instrument(asset_id=mint, symbol='SOL', venue='solana', quote_currency='USD')
settings = Settings(search_enabled=False, coingecko_api_key='fixture-key', jupiter_api_key='fixture-key', exa_api_key='fixture-key')
before_cursors = []
def respond(request):
    if request.url.path == '/search':
        return httpx.Response(200, json={'results': [{
            'id': 'fixture-story', 'url': 'https://news.example/solana',
            'title': 'Fixture Solana news', 'publishedDate': '2026-10-21T11:55:00Z',
            'text': 'Untrusted news excerpt',
        }]})
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
from autohedge.ai import ModelReply, TokenUsage
calls = []
class Provider:
    def __init__(self, role): self.role = role
    def complete(self, request):
        role = {'trading_director':'director','quant':'quant','news_intelligence':'sentiment','risk_manager':'risk'}[self.role.value]
        calls.append(role)
        return ModelReply(request_id=request.request_id, target=request.target,
            finish_reason='complete', content='Offline research with supplied data: ' + role,
            usage=TokenUsage(status='unknown'))
def factory(clock):
    return build_agent_team(clock, settings=settings, clock=lambda:now,
        provider_factory=lambda role, config: Provider(role))
with tempfile.TemporaryDirectory() as directory:
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        frozen = collect_research_snapshot(
            CandleRequest(instrument=instrument, interval_seconds=60, count=40, as_of=now),
            history_provider=CoinGeckoCandleProvider(settings, client=client, clock=lambda: now, page_size=20),
            quote_provider=JupiterProvider(settings, client=client, clock=lambda: now, block_time_resolver=lambda block: now),
            news_provider=ExaNewsProvider(settings, client=client, clock=lambda: now), clock=lambda: now)
        record = frozen.history_acquisition
        quote = frozen.market_data.quote
    store = HistoryStore(directory + '/history')
    digest = store.save(record)
    replayed = store.replay(digest, available_at=now)
    assert frozen.history_acquisition_id == digest
    assert parse_research_snapshot(json.loads(frozen.model_dump_json())) == frozen
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
    data = MarketData(quote=quote, history=replayed, news=frozen.market_data.news)
    assert data == frozen.market_data
    assert features == frozen.indicators
    engine = AutoHedge(output_dir=directory + '/reports', settings=settings,
        team_factory=factory, clock=lambda: now)
    report = engine.run_report('Analyze supplied SOL history', instrument=instrument, snapshot=frozen)
assert calls == ['director', 'quant', 'sentiment', 'risk', 'director']
assert report.market_quality.candle_count == 40
assert report.market_quality.price_deviation_bps == 0
assert report.request.snapshot.market_data.history == replayed
assert report.request.snapshot.snapshot_id == frozen.snapshot_id
assert report.market_quality.policy.require_news
assert report.execution_enabled is False

# The public default path must create real SDK agents with search tools disabled,
# even when the application's legacy exploration setting permits live search.
routes = []
def offline_default_factory(when, *, settings, **options):
    routes.append(settings.search_enabled)
    team = build_agent_team(when, settings=settings,
        provider_factory=lambda role, config:Provider(role), **options)
    assert team.sentiment.search is None
    assert not team.sentiment.agent.tools
    return team
with tempfile.TemporaryDirectory() as directory:
    with patch('autohedge.workers.build_agent_team', side_effect=offline_default_factory):
        default_engine = AutoHedge(output_dir=directory, clock=lambda:now,
            settings=Settings(openai_api_key='fixture-model-key', search_enabled=True))
        default_report = default_engine.run_report('Frozen default factory', instrument=instrument, snapshot=frozen)
assert routes == [False]
assert default_engine.settings.search_enabled is True
assert default_report.request.snapshot == frozen
assert default_report.execution_enabled is False
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
from autohedge.ai import ModelReply, TokenUsage
calls = []
class Provider:
    def __init__(self, role): self.role=role
    def complete(self, request):
        role={'trading_director':'director','quant':'quant','news_intelligence':'sentiment','risk_manager':'risk'}[self.role.value]
        calls.append(role)
        return ModelReply(request_id=request.request_id,target=request.target,finish_reason='complete',
            content='offline research by ' + role,usage=TokenUsage(status='unknown'))
def factory(now):
    return build_agent_team(now,settings=settings,provider_factory=lambda role, config:Provider(role))
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
