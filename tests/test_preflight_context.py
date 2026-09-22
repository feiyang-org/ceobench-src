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
