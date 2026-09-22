import json
from types import SimpleNamespace

import pytest

from saas_bench.agents.bash_agent.agent import BashAgent, Message


def agent(tmp_path, anthropic=False):
    value = BashAgent.__new__(BashAgent)
    value.conversation = []
    value._pending_tool_calls = []
    value.current_day = 7
    value.turns_today = 2
    value.total_turns = 2
    value._last_observation = ''
    value._snapshot_path = tmp_path / 'conversation.json'
    value.workspace_path = tmp_path
    value.system_prompt = 'Original prompt'
    value.use_anthropic = anthropic
    value._skip_next_refresh = False
    value.max_turns_per_day = 0
    value._call_llm = lambda: 'action'
    return value


@pytest.mark.parametrize('shape', ['chat', 'responses', 'anthropic'])
def test_completed_tool_roundtrip_and_week_refresh(tmp_path, shape):
    first = agent(tmp_path, shape == 'anthropic')
    call = {'type': 'function_call', 'call_id': 'call1', 'name': 'bash', 'arguments': '{}'}
    first.conversation = [Message('assistant', [call] if shape != 'chat' else '',
                                  tool_calls=[{'id': 'call1'}] if shape == 'chat' else None)]
    first._pending_tool_calls = [{'id': 'call1', 'name': 'bash'}]
    first.record_tool_result('completed')
    first._save_conversation_snapshot(strict=True)
    second = agent(tmp_path, shape == 'anthropic')
    assert second.load_conversation_snapshot(second._snapshot_path)
    before = [second._serialize_message(m) for m in second.conversation]
    assert second.act('completed', 0, False, {'day': 7}) == 'action'
    assert [second._serialize_message(m) for m in second.conversation] == before
    (tmp_path / 'MEMORY.md').write_text('remember this')
    second.act('new dashboard', 0, False, {'day': 14})
    assert all(m.role != 'assistant' for m in second.conversation)
    assert 'remember this' in second._get_system_prompt_with_memory()


def test_pending_tool_is_not_silently_replayed(tmp_path):
    first = agent(tmp_path)
    first._pending_tool_calls = [{'id': 'unknown', 'name': 'bash'}]
    first._save_conversation_snapshot()
    assert not agent(tmp_path).load_conversation_snapshot(first._snapshot_path)
    assert first.check_day_advanced('=== Week 2 Dashboard (Day 14) ===\nresult')


def test_memory_keeps_original_limit(tmp_path):
    value = agent(tmp_path)
    (tmp_path / 'MEMORY.md').write_text('a' * 40000 + 'not included')
    prompt = value._get_system_prompt_with_memory()
    assert 'a' * 40000 in prompt and 'not included' not in prompt


@pytest.mark.parametrize('api', ['chat', 'responses', 'messages'])
def test_real_sdk_context_matches_continuous_after_restore(tmp_path, api):
    import httpx
    from openai import OpenAI
    from anthropic import Anthropic
    from test_preflight_usage import reply
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        body = reply(api)
        if api == 'chat':
            body['choices'][0]['message']['tool_calls'] = [dict(id='call1', type='function',
                function=dict(name='read_file', arguments='{"path":"MEMORY.md"}'))]
            return httpx.Response(200, json=body)
        if api == 'responses':
            body['output'] = [dict(type='function_call', id='fc1', call_id='call1', name='read_file',
                                  arguments='{"path":"MEMORY.md"}', status='completed')]
            return httpx.Response(200, json=body)
        body['content'] = []
        events = [dict(type='message_start', message=body),
                  dict(type='content_block_start', index=0, content_block=dict(type='tool_use', id='call1', name='read_file', input={})),
                  dict(type='content_block_delta', index=0, delta=dict(type='input_json_delta', partial_json='{"path":"MEMORY.md"}')),
                  dict(type='content_block_stop', index=0),
                  dict(type='message_delta', delta={'stop_reason': 'tool_use', 'stop_sequence': None}, usage={'output_tokens': 2}),
                  dict(type='message_stop')]
        stream = ''.join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, content=stream, headers={'content-type': 'text/event-stream'})
    client = (Anthropic if api == 'messages' else OpenAI)(api_key='offline-only', max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    def new_agent():
        value = BashAgent([], client, system_prompt='Original instructions', workspace_path=tmp_path,
                          reasoning_effort='low' if api == 'responses' else None)
        value._snapshot_path = tmp_path / 'context.json'
        return value
    first = new_agent()
    assert first.act('dashboard', 0, False, {'day': 7}).tool == 'read_file'
    first.record_tool_result('contents A')
    first._save_conversation_snapshot(strict=True)
    second = new_agent()
    assert second.load_conversation_snapshot(second._snapshot_path)
    first.act('contents A', 0, False, {'day': 7})
    second.act('contents A', 0, False, {'day': 7})
    assert captured[1] == captured[2]
    assert 'call1' in json.dumps(captured[2]) and 'contents A' in json.dumps(captured[2])
    second.record_tool_result('contents A')
    (tmp_path / 'MEMORY.md').write_text('persistent note')
    second.act('new week', 0, False, {'day': 14})
    assert 'persistent note' in json.dumps(captured[-1])
    assert 'call1' not in json.dumps(captured[-1])
    assert first.usage_recorder.summary['http_attempts'] == 2
    assert second.usage_recorder.summary['http_attempts'] == 2
    client.close()
