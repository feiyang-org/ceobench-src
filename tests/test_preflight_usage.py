import copy
import json

import httpx
import pytest
from anthropic import Anthropic, AnthropicBedrock
from openai import OpenAI

from saas_bench.config import BenchmarkConfig
from saas_bench.customer_llm import CustomerSimulator
from saas_bench.database import init_database
from saas_bench.model_usage import FIELDS, ModelUsage, cost_usd, usage_values


def reply(api, usage=True):
    if api == 'chat':
        result = dict(id='chat1', object='chat.completion', created=1, model='test-model',
                      choices=[dict(index=0, finish_reason='stop', message=dict(role='assistant', content='hello'))])
        values = dict(prompt_tokens=10, completion_tokens=2, prompt_tokens_details={'cached_tokens': 3},
                      completion_tokens_details={'reasoning_tokens': 1})
    elif api == 'responses':
        result = dict(id='resp1', object='response', created_at=1, status='completed', model='test-model',
                      output=[dict(id='msg1', type='message', role='assistant', status='completed',
                                   content=[dict(type='output_text', text='hello', annotations=[])])])
        values = dict(input_tokens=10, output_tokens=2, total_tokens=12,
                      input_tokens_details={'cached_tokens': 3}, output_tokens_details={'reasoning_tokens': 1})
    else:
        result = dict(id='msg1', type='message', model='test-model', role='assistant',
                      content=[dict(type='text', text='hello')], stop_reason='end_turn', stop_sequence=None)
        values = dict(input_tokens=5, output_tokens=2, cache_read_input_tokens=3, cache_creation_input_tokens=2)
    if usage:
        result['usage'] = values
    return result


@pytest.mark.parametrize('provider,api', [('deepseek', 'chat'), ('opencode', 'chat'), ('openai', 'responses'),
                                         ('anthropic', 'messages'), ('bedrock', 'messages')])
def test_real_sdk_requests_internal_retries_and_simulator_usage(tmp_path, monkeypatch, provider, api):
    monkeypatch.setenv('CEOBENCH_SIMULATOR_USAGE_LOG', str(tmp_path / 'simulator.jsonl'))
    monkeypatch.setattr('time.sleep', lambda _: None)
    requests = []
    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(500, json={'error': {'type': 'server_error', 'message': 'try again'}})
        return httpx.Response(200, json=reply(api))
    http = httpx.Client(transport=httpx.MockTransport(handle))
    if provider == 'bedrock':
        client = AnthropicBedrock(aws_access_key='dummy', aws_secret_key='private-test-secret',
                                 aws_region='us-east-2', http_client=http, max_retries=1)
    elif provider == 'anthropic':
        client = Anthropic(api_key='private-test-secret', http_client=http, max_retries=1)
    else:
        client = OpenAI(api_key='private-test-secret', http_client=http, max_retries=1)
    sim = CustomerSimulator(client if api != 'messages' else None, init_database(':memory:'), BenchmarkConfig())
    if api == 'messages':
        setattr(sim, '_bedrock_client' if provider == 'bedrock' else '_anthropic_client', sim.usage_recorder.attach(client))
    assert sim.complete_text(provider=provider, model='test-model', user='full input', system='instructions', max_tokens=30) == ('hello', 10, 2)
    entries = [json.loads(line) for line in (tmp_path / 'simulator.jsonl').read_text().splitlines()]
    assert 'private-test-secret' not in json.dumps(entries)
    attempts = [r for r in entries if r['event'] == 'http_request']
    assert len(attempts) == 2 and [r['sdk_retry'] for r in attempts] == ['0', '1']
    assert len({r['call_id'] for r in entries}) == 1
    assert 'full input' in json.dumps(entries[0]['request'])
    assert [r['status'] for r in entries if r['event'] == 'http_response'] == [500, 200]
    assert entries[-1]['response']['usage'] == reply(api)['usage']
    assert sim.usage_recorder.summary['known']['input_tokens'] == 10
    assert sim.usage_recorder.summary['missing_cost'] == 1
    assert sim.usage_recorder.summary['http_attempts'] == 2
    assert sim.usage_recorder.summary['failed_attempts_without_usage'] == 1
    client.close()


def test_missing_usage_cache_prices_and_restored_subtotals(tmp_path):
    recorder = ModelUsage(tmp_path / 'agent.jsonl', 'agent', {'test-model': dict(input=1, output=2, cache_read=0.1, cache_write=1.5)})
    recorder.call('messages', {'model': 'test-model'}, lambda: reply('messages'), outer_attempt=1)
    assert recorder.summary['known_cost_usd'] == pytest.approx(0.0123)
    restored = ModelUsage(tmp_path / 'clone.jsonl', 'agent', recorder.pricing)
    restored.summary = copy.deepcopy(recorder.summary)
    partial = {'model': 'test-model', 'usage': {'completion_tokens': 4}}
    restored.call('chat', {}, lambda: partial, outer_attempt=2)
    restored.call('responses', {}, lambda: {}, outer_attempt=3)
    assert restored.summary['known']['input_tokens'] == 10
    assert restored.summary['known']['output_tokens'] == 6
    assert restored.summary['missing']['input_tokens'] == 2
    assert restored.summary['missing']['output_tokens'] == 1
    assert restored.summary['missing_cost'] == 2
    assert recorder.summary['calls'] == 1
    assert usage_values({}, 'chat') == dict.fromkeys(FIELDS)
    assert cost_usd(usage_values(reply('chat'), 'chat'), 'chat', None) is None
    assert usage_values({'usage': {'prompt_tokens': 8, 'prompt_cache_hit_tokens': 3}}, 'chat')['cached_tokens'] == 3


def test_go_cache_fields_and_sourced_time_bounded_prices(tmp_path):
    from datetime import datetime, timezone
    from saas_bench.model_usage import load_pricing
    response = reply('chat')
    response['usage']['prompt_tokens_details']['cache_write_tokens'] = 0
    usage = usage_values(response, 'chat')
    assert usage['cache_creation_tokens'] == 0
    response['usage']['prompt_tokens_details']['cache_creation_input_tokens'] = 2
    assert usage_values(response, 'chat')['cache_creation_tokens'] == 2
    rates = dict(input=.00015, output=.0006, cache_read=.000003,
                 valid_from='2026-09-22T10:00:00+00:00', valid_until='2026-09-23T01:00:00+00:00')
    path = tmp_path / 'pricing.json'
    path.write_text(json.dumps(dict(source='https://opencode.ai/docs/go/', basis='subscription quota, USD/1k', rates={'test-model': rates})))
    assert load_pricing(path)['rates']['test-model'] == rates
    assert cost_usd(usage, 'chat', rates, datetime(2026, 9, 22, 11, tzinfo=timezone.utc)) == pytest.approx(.000002259)
    assert cost_usd(usage, 'chat', rates, datetime(2026, 9, 23, 1, tzinfo=timezone.utc)) is None
    for invalid in (-1, float('inf'), True):
        rates['input'] = invalid
        path.write_text(json.dumps(dict(source='documented', basis='USD/1k', rates={'test-model': rates})))
        with pytest.raises(ValueError, match='finite nonnegative'):
            load_pricing(path)


def test_connection_retry_and_interrupted_anthropic_stream(tmp_path, monkeypatch):
    monkeypatch.setattr('time.sleep', lambda _: None)
    attempts = []
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            message = reply('messages')
            message['content'] = []
            yield ('event: message_start\ndata: ' + json.dumps({'type': 'message_start', 'message': message}) + '\n\n').encode()
            raise httpx.ReadError('offline interruption')
    def handle(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ConnectError('offline connection error')
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Interrupted())
    recorder = ModelUsage(tmp_path / 'agent.jsonl', 'agent')
    client = recorder.attach(Anthropic(api_key='test-secret', max_retries=1,
                                      http_client=httpx.Client(transport=httpx.MockTransport(handle))))
    request = dict(model='test-model', messages=[{'role': 'user', 'content': 'hello'}], max_tokens=30)
    def invoke():
        with client.messages.stream(**request) as stream:
            return stream.get_final_message()
    with pytest.raises(httpx.ReadError):
        recorder.call('messages', request, invoke, outer_attempt=1)
    entries = [json.loads(s) for s in recorder.path.read_text().splitlines()]
    assert len([r for r in entries if r['event'] == 'http_request']) == 2
    assert any(r['event'] == 'http_error' and r['error'] == 'ConnectError' for r in entries)
    partial = next(r for r in entries if r['event'] == 'http_response')
    assert 'message_start' in partial['body'] and partial['error'] == 'ReadError'
    assert recorder.summary['errors'] == 1
    assert recorder.summary['known'] == dict.fromkeys(FIELDS)
    assert recorder.summary['missing'] == dict.fromkeys(FIELDS, 1)
    client.close()


def test_agent_outer_retry_records_full_request_and_missing_usage(tmp_path, monkeypatch):
    from saas_bench.agents.bash_agent.agent import BashAgent
    monkeypatch.setattr('time.sleep', lambda _: None)
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(503, json={'error': {'message': 'offline retry', 'type': 'server_error'}})
        body = reply('chat', usage=False)
        body['choices'][0]['message']['tool_calls'] = [dict(id='call1', type='function',
            function=dict(name='read_file', arguments='{"path":"MEMORY.md"}'))]
        return httpx.Response(200, json=body)
    client = OpenAI(api_key='test-secret', max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    recorder = ModelUsage(tmp_path / 'agent.jsonl', 'agent')
    agent = BashAgent([], client, system_prompt='original instructions', workspace_path=tmp_path, usage_recorder=recorder)
    assert agent.act('dashboard', 0, False, {'day': 7}).tool == 'read_file'
    entries = [json.loads(s) for s in recorder.path.read_text().splitlines()]
    calls = [r for r in entries if r['event'] == 'request']
    assert [r['outer_attempt'] for r in calls] == [1, 2]
    assert all('original instructions' in json.dumps(r['request']['messages']) for r in calls)
    assert agent.last_input_tokens is None
    assert recorder.summary['missing']['input_tokens'] == 2
    assert recorder.summary['known']['input_tokens'] is None
    assert recorder.summary['errors'] == 1
    client.close()
