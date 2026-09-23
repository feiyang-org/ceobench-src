"""Event/version examples and model-input source checks. No provider calls or runtime hooks.

Run: uv run python scripts/check_evidence_records.py --output docs/evidence-recording
Read back: uv run python scripts/check_evidence_records.py --verify docs/evidence-recording
"""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shlex
import socket
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import urllib.request
from unittest.mock import patch

import httpx
from anthropic import Anthropic
from openai import OpenAI

from saas_bench.agents.bash_agent.agent import BashAgent
from saas_bench.agents.bash_agent.tools import BashAgentToolExecutor, get_bash_agent_tool_descriptions
from saas_bench.api_server import NovaMindAPIServer
from saas_bench.database import init_database
from saas_bench.environment import build_weekly_dashboard
from saas_bench.model_usage import ModelUsage


ROOT = Path(__file__).resolve().parents[1]
FORMAT = 'ceobench.evidence-records.v1'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode('utf-8')).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def content(bundle, text):
    checksum = digest(text)
    bundle['blobs'][checksum] = dict(encoding='utf-8', size_bytes=len(text.encode('utf-8')), text=text)
    return checksum


def version(bundle, version_id, object_id, text, **metadata):
    bundle['versions'][version_id] = dict(object_id=object_id, blob_sha256=content(bundle, text), **metadata)
    return version_id


def source_text(bundle, version_id):
    return bundle['blobs'][bundle['versions'][version_id]['blob_sha256']]['text']


def event_example(work):
    bundle = dict(format=FORMAT, evidence_class='constructed_offline_example', run_id='evidence-example',
                  branches={'prefix': None, 'git': {'parent': 'prefix', 'fork_event': 'evidence-example/prefix/5'},
                            'pf': {'parent': 'prefix', 'fork_event': 'evidence-example/prefix/5'}},
                  events=[], versions={}, blobs={}, relations=[], git_mappings=[])
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    server = NovaMindAPIServer(SimpleNamespace(workspace_path=work), conn=conn)
    executor = BashAgentToolExecutor(work)
    sql = "SELECT 7 AS day, 'alpha' AS label, NULL AS note, 1.5 AS amount UNION ALL SELECT 7, 'alpha', NULL, 1.5"
    query = dict(data_source='evidence-example/public', sql=sql, bound_parameters=None)
    query_id = 'query:' + digest(json.dumps(query, sort_keys=True))

    def add(kind, request, branch='prefix', inputs=()):
        seq = 1 + sum(e['branch_id'] == branch for e in bundle['events'])
        identifier = f'evidence-example/{branch}/{seq}'
        entry = dict(event_id=identifier, run_id='evidence-example', branch_id=branch, seq=seq, author='ceo',
                     kind=kind, started_at=now(), sim_day=7, status='succeeded', request=request,
                     inputs=list(inputs), outputs=[], capture_gaps=[])
        bundle['events'].append(entry)
        return entry

    def produced(entry, label, obj, text, layer, previous=None, content_time=None):
        entry['completed_at'] = now()
        identifier = version(bundle, entry['event_id'] + ':' + label, obj, text,
                             created_by_event=entry['event_id'], previous_version=previous,
                             acquired_at=entry['completed_at'],
                             content_time=content_time or {'status': 'unknown', 'reason': 'not_declared'},
                             capture={'layer': layer, 'extent': 'full', 'source_truncated': False})
        entry['outputs'].append(identifier)
        return identifier

    server.start()
    try:
        first = add('sql_query', dict(query, query_id=query_id))
        def query_once():
            req = urllib.request.Request(f'http://127.0.0.1:{server.port}/query',
                data=json.dumps({'sql': sql}).encode(), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req) as response:
                require(response.status == 200, 'query failed')
                return response.read().decode('utf-8')
        receipt = query_once()
        q1 = produced(first, 'public_response', query_id, receipt, 'server_public_response',
                      content_time={'status': 'known', 'sim_day': 7, 'basis': 'explicit_sql_literal'})
        saved = add('write_file', {'path': 'evidence.json'}, inputs=[q1])
        saved['tool_return'] = executor.execute('write_file', {'path': 'evidence.json', 'content': receipt})
        f1 = produced(saved, 'file', 'file:evidence.json', (work / 'evidence.json').read_text(), 'file_bytes')
        bundle['relations'].append(dict(source=f1, target=q1, kind='exact_copy',
                                        origin='observed_fixture_operation', event_id=saved['event_id']))
        # Only this temporary example repository is committed; never the user's checkout.
        def git(*args):
            return subprocess.check_output(['git', '-C', str(work), *args], stderr=subprocess.DEVNULL).decode().strip()
        git('init', '-q')
        git('add', 'evidence.json')
        git('-c', 'user.name=Evidence fixture', '-c', 'user.email=evidence@example.invalid',
            '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Constructed common-prefix evidence')
        commit = git('rev-parse', 'HEAD')
        git_bytes = subprocess.check_output(['git', '-C', str(work), 'show', f'{commit}:evidence.json'])
        require(git_bytes == receipt.encode(), 'Git bytes differ')
        bundle['git_mappings'].append(dict(version_id=q1, path='evidence.json', commit=commit,
                                          blob_sha256=digest(git_bytes), verification='git_show_bytes_equal'))
        declared = add('registration_fixture', {'text': 'Use the day 7 query as historical evidence.',
                        'reference': q1, 'purpose': 'historical_only'}, inputs=[q1])
        declared['capture_gaps'] = ['Constructed Agent declaration; create/revise tools are implemented in stage 3.']
        note = produced(declared, 'text', 'declaration:note-1', declared['request']['text'], 'registered_text')
        bundle['relations'].append(dict(source=note, target=q1, kind='declared_reference',
                                        origin='agent_declaration_fixture', event_id=declared['event_id']))
        modified = add('edit_file', {'path': 'evidence.json', 'old_string': '"row_count": 2',
                                    'new_string': '"row_count": 99'}, inputs=[f1])
        modified['tool_return'] = executor.execute('edit_file', modified['request'])
        f2 = produced(modified, 'file', 'file:evidence.json', (work / 'evidence.json').read_text(), 'file_bytes', f1)
        again = add('sql_query', dict(query, query_id=query_id))
        q2 = produced(again, 'public_response', query_id, query_once(), 'server_public_response',
                      content_time={'status': 'known', 'sim_day': 7, 'basis': 'explicit_sql_literal'})
        for branch in ('git', 'pf'):
            read = add('inherited_reference_fixture', {'reference': q1}, branch, inputs=[q1])
            read['completed_at'] = now()
            read['capture_gaps'] = ['Identifier resolution example; no online fork is executed.']
        bundle['checks'] = dict(repeated_queries=[q1, q2], file_versions=[f1, f2], declared_text=note)
        return bundle
    finally:
        server.stop()
        conn.close()


def model_reply(api, index, bash_command):
    call_id = f'tool-{index}'
    name = 'bash' if index == 3 else 'read_file'
    args = json.dumps({'command': bash_command} if name == 'bash' else
                      {'path': 'evidence.txt', 'offset': 2, 'limit': 1})
    if api == 'chat':
        return httpx.Response(200, json=dict(id=f'chat-{index}', object='chat.completion', created=1,
            model='offline-visibility-fixture', choices=[dict(index=0, finish_reason='tool_calls', message=dict(
            role='assistant', content='', tool_calls=[dict(id=call_id, type='function',
            function=dict(name=name, arguments=args))]))]))
    if api == 'responses':
        return httpx.Response(200, json=dict(id=f'resp-{index}', object='response', created_at=1,
            status='completed', model='offline-visibility-fixture', output=[dict(type='function_call', id=f'fc-{index}',
            call_id=call_id, name=name, arguments=args, status='completed')]))
    start = dict(id=f'msg-{index}', type='message', model='offline-visibility-fixture', role='assistant',
                 content=[], stop_reason=None, stop_sequence=None, usage={'input_tokens': 0, 'output_tokens': 0})
    events = [dict(type='message_start', message=start), dict(type='content_block_start', index=0,
              content_block=dict(type='tool_use', id=call_id, name=name, input={})),
              dict(type='content_block_delta', index=0, delta={'type': 'input_json_delta', 'partial_json': args}),
              dict(type='content_block_stop', index=0), dict(type='message_delta',
              delta={'stop_reason': 'tool_use', 'stop_sequence': None}, usage={'output_tokens': 0}),
              dict(type='message_stop')]
    return httpx.Response(200, content=''.join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events),
                          headers={'content-type': 'text/event-stream'})


def text_fields(body):
    """Only model-input fields; tool schemas and model-generated call arguments are excluded."""
    if 'instructions' in body:
        yield '/instructions', 'system', body['instructions']
    for i, block in enumerate(body.get('system', [])):
        yield f'/system/{i}/text', 'system', block['text']
    key = 'input' if 'input' in body else 'messages'
    for i, msg in enumerate(body.get(key, [])):
        base = f'/{key}/{i}'
        role = msg.get('role', 'tool' if msg.get('type') == 'function_call_output' else 'assistant')
        if msg.get('type') == 'function_call_output':
            yield base + '/output', role, msg['output']
        elif isinstance(msg.get('content'), str):
            yield base + '/content', role, msg['content']
        elif isinstance(msg.get('content'), list):
            for j, block in enumerate(msg['content']):
                for name in ('text', 'content'):
                    if isinstance(block.get(name), str):
                        yield f'{base}/content/{j}/{name}', role, block[name]


def visibility_example(work):
    bundle = dict(format=FORMAT, evidence_class='real_sdk_serialization_with_offline_transport',
                  provider_calls=0, versions={}, blobs={}, requests=[], deliveries=[], checks={})
    def src(name, text, layer):
        return version(bundle, name, name.split('@')[0], text, acquired_at=now(), layer=layer,
                       origin='explicit_fixture_source')
    file_a = 'not returned line one\nFILE_A_证据🙂\nnot returned line three\n'
    src('file@a', file_a, 'file_bytes')
    src('file@b', file_a.replace('FILE_A', 'FILE_B'), 'file_bytes')
    for letter in 'abc':
        src('memory@' + letter, f'  MEMORY_{letter.upper()}_来源🙂\n', 'file_bytes')
    long_memory = ' \n' + 'LONG_MEMORY_🙂' + 'm' * (40000 - len('LONG_MEMORY_🙂')) + 'MEMORY_HIDDEN_TAIL' + '\n '
    src('memory@long', long_memory, 'file_bytes')
    src('queued@unsent', 'QUEUED_BUT_NEVER_SENT', 'tool_return')
    src('bash@stdout', 'BASH_HEAD_🙂' + 'x' * 40000 + 'BASH_TAIL_END\n', 'stdout_fixture')
    code = 'print(' + repr(source_text(bundle, 'bash@stdout')[:-1]) + ')'
    executor = BashAgentToolExecutor(work)
    (work / 'emit.py').write_text(code)
    bash_command = shlex.quote(sys.executable) + ' emit.py'
    bash_result = executor.execute('bash', {'command': bash_command})
    src('bash@return', bash_result, 'tool_return')
    stdout = source_text(bundle, 'bash@stdout')
    require(bash_result.startswith(stdout[:15000]) and bash_result.endswith(stdout[-15000:]), 'Bash projection changed')
    require('output truncated' in bash_result, 'Bash truncation marker missing')
    conn = init_database(work / 'dashboard.db')
    script_output = 'SCRIPT_VISIBLE_证据🙂' + 's' * 700 + 'SCRIPT_HIDDEN_TAIL'
    server = NovaMindAPIServer(SimpleNamespace(workspace_path=work), conn=conn)
    code_a = 'print(' + repr(script_output) + ')'
    (work / 'weekly.py').write_text(code_a)
    server.set_daily_scripts({'weekly.py': code_a})
    (work / 'weekly.py').write_text("print('UNREGISTERED_SOURCE_B')")
    outputs = server._run_daily_scripts_internal()
    require(outputs['weekly.py'] == script_output + '\n', 'registered snapshot was not executed')
    src('script@registered', code_a, 'registered_script')
    src('script@output', outputs['weekly.py'], 'script_tool_return')
    for day in (0, 7, 14, 21):
        src(f'dashboard@{day}', build_weekly_dashboard(conn, day, calc_outputs=outputs), 'dashboard')
    conn.close()
    (work / 'evidence.txt').write_text(file_a)
    file_result = executor.execute('read_file', {'path': 'evidence.txt', 'offset': 2, 'limit': 1})
    src('file@return', file_result, 'tool_return')
    require(file_result == '     2\tFILE_A_证据🙂', 'file range changed')

    for api in ('chat', 'responses', 'messages'):
        case = work / api
        case.mkdir()
        (case / 'emit.py').write_text(code)
        recorder = ModelUsage(case / 'requests.jsonl', 'agent')
        wires = []
        def handle(request):
            wires.append(request.content.decode('utf-8'))
            return model_reply(api, len(wires), bash_command)
        client = (Anthropic if api == 'messages' else OpenAI)(api_key='offline-only', max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        def new_agent():
            a = BashAgent(get_bash_agent_tool_descriptions(), client, model='offline-visibility-fixture',
                          system_prompt='Offline model-input visibility fixture.', workspace_path=case,
                          reasoning_effort='low' if api == 'responses' else None, usage_recorder=recorder)
            a._snapshot_path = case / 'conversation.json'
            return a
        def memory(identifier):
            (case / 'MEMORY.md').write_text(source_text(bundle, identifier))
        # Bindings are known from the fixture's execution, never inferred from arbitrary matching text.
        history = []
        def request(a, scenario, day, observation, memory_id, dashboard_id, include_history=True, restored=False):
            before = len(wires)
            action = a.act(observation, 0, False, {'day': day})
            require(len(wires) == before + 1, 'expected one SDK attempt')
            log = [json.loads(line) for line in recorder.path.read_text().splitlines()]
            logical = [r for r in log if r['event'] == 'request'][-1]
            sent = [r for r in log if r['event'] == 'http_request'][-1]
            wire = wires[-1]
            require(json.loads(wire) == sent['body'], 'ModelUsage and transport differ')
            entry = dict(api=api, reader='ceo', transport='httpx.MockTransport', scenario=scenario,
                         request_id=sent['attempt_id'], call_id=sent['call_id'],
                         attempt_id=sent['attempt_id'], context_id=f'{api}/week-{day}', restored=restored,
                         acquired_at=sent['timestamp'], logical_request=logical['request'],
                         wire_blob=content(bundle, wire), occurrences=[], absent=[])
            if scenario in ('week_start', 'same_week_after_source_change'):
                result = BashAgentToolExecutor(case).execute(action.tool, action.arguments)
                require(result == (file_result if scenario == 'week_start' else bash_result), 'actual tool return differs')
            fields = list(text_fields(sent['body']))
            bindings = [(dashboard_id, 0, len(source_text(bundle, dashboard_id)), 'dashboard'),
                        ('script@output', 0, 500, 'dashboard_script_prefix')]
            if memory_id:
                raw = source_text(bundle, memory_id)
                left = len(raw) - len(raw.lstrip())
                bindings.append((memory_id, left, left + min(40000, len(raw.strip())), 'memory_strip_prefix'))
            else:
                entry['absent'].append(dict(version_id='memory@a', text='MEMORY_A_来源🙂'))
            if include_history:
                bindings.extend(history)
            for identifier, start, end, delivery in bindings:
                fragment = source_text(bundle, identifier)[start:end]
                matches = [(p, role, text.index(fragment)) for p, role, text in fields if fragment in text]
                require(len(matches) == 1, f'ambiguous fixture binding {api}/{scenario}/{identifier}')
                pointer, role, offset = matches[0]
                entry['occurrences'].append(dict(version_id=identifier, source_range=[start, end],
                    json_pointer=pointer, role=role, request_range=[offset, offset + len(fragment)],
                    delivery_id=f'{api}/{delivery}', origin='explicit_fixture_binding',
                    full_source=start == 0 and end == len(source_text(bundle, identifier))))
            for identifier, token in [('script@output', 'SCRIPT_HIDDEN_TAIL'), ('file@b', 'FILE_B_证据🙂'),
                                       ('queued@unsent', 'QUEUED_BUT_NEVER_SENT')]:
                entry['absent'].append(dict(version_id=identifier, text=token))
            if not include_history:
                entry['absent'].extend([dict(version_id='file@a', text='FILE_A_证据🙂'),
                                        dict(version_id='bash@return', text='BASH_HEAD_🙂')])
            if memory_id == 'memory@long':
                entry['absent'].append(dict(version_id=memory_id, text='MEMORY_HIDDEN_TAIL'))
            for other in ('memory@a', 'memory@b', 'memory@c'):
                if other != memory_id:
                    entry['absent'].append(dict(version_id=other, text=source_text(bundle, other).strip()))
            bundle['requests'].append(entry)
            return entry
        try:
            memory('memory@a')
            (case / 'evidence.txt').write_text(file_a)
            day0 = new_agent()
            request(day0, 'day0', 0, source_text(bundle, 'dashboard@0'),
                    None if api == 'chat' else 'memory@a', 'dashboard@0', False)
            a = new_agent()
            request(a, 'week_start', 7, source_text(bundle, 'dashboard@7'), 'memory@a', 'dashboard@7', False)
            a.record_tool_result(file_result)
            history.extend([('file@return', 0, len(file_result), 'file_read'),
                            ('file@a', len(file_a.splitlines()[0]) + 1,
                             len(file_a.splitlines()[0]) + 1 + len('FILE_A_证据🙂'), 'file_read')])
            (case / 'evidence.txt').write_text(source_text(bundle, 'file@b'))
            memory('memory@b')
            current_memory = 'memory@a' if api == 'chat' else 'memory@b'
            request(a, 'same_week_after_source_change', 7, file_result, current_memory, 'dashboard@7')
            a.record_tool_result(bash_result)
            history.extend([('bash@return', 0, len(bash_result), 'bash'),
                            ('bash@stdout', 0, 15000, 'bash'),
                            ('bash@stdout', len(stdout) - 15000, len(stdout), 'bash')])
            a._save_conversation_snapshot(strict=True)
            snapshot_blob = content(bundle, a._snapshot_path.read_text())
            b = new_agent()
            require(b.load_conversation_snapshot(b._snapshot_path), 'snapshot restore failed')
            continuous = request(a, 'continuous', 7, bash_result, current_memory, 'dashboard@7')
            restored = request(b, 'same_week_restore', 7, bash_result, current_memory, 'dashboard@7', restored=True)
            restored['snapshot_blob'] = snapshot_blob
            require(continuous['wire_blob'] == restored['wire_blob'], 'restore request bytes differ')
            b.record_tool_result(source_text(bundle, 'queued@unsent'))
            b._save_conversation_snapshot(strict=True)
            bundle['deliveries'].append(dict(delivery_id=f'{api}/queued', version_id='queued@unsent',
                state='in_conversation_never_sent', context_id=f'{api}/week-7',
                snapshot_blob=content(bundle, b._snapshot_path.read_text())))
            memory('memory@c')
            request(b, 'new_week', 14, source_text(bundle, 'dashboard@14'), 'memory@c', 'dashboard@14', False)
            b.record_tool_result('completed')
            memory('memory@long')
            request(b, 'memory_truncation', 21, source_text(bundle, 'dashboard@21'), 'memory@long', 'dashboard@21', False)
        finally:
            client.close()
    bundle['checks'] = dict(apis=3, requests=len(bundle['requests']), provider_calls=0,
        day0_chat_missing_system_memory=True, same_week_memory_policy=True, restore_bytes_equal=True,
        week_reset=True, unsent_result=True, file_range=True, bash_truncation=True,
        script_snapshot_and_dashboard_prefix=True, memory_strip_and_truncation=True)
    return bundle


def validate_blobs(bundle):
    require(bundle['format'] == FORMAT, 'format mismatch')
    for checksum, blob in bundle['blobs'].items():
        require(digest(blob['text']) == checksum, 'blob checksum mismatch')
        require(len(blob['text'].encode('utf-8')) == blob['size_bytes'], 'blob size mismatch')
    for identifier in bundle['versions']:
        source_text(bundle, identifier)


def validate_events(bundle):
    validate_blobs(bundle)
    events = {e['event_id']: e for e in bundle['events']}
    require(len(events) == len(bundle['events']), 'duplicate event')
    counters = {}
    def available(identifier, branch, cutoff=None):
        producer = events[bundle['versions'][identifier]['created_by_event']]
        if producer['branch_id'] == branch:
            return cutoff is None or producer['seq'] <= cutoff
        parent = bundle['branches'][branch]
        return bool(parent) and available(identifier, parent['parent'], events[parent['fork_event']]['seq'])
    for event in bundle['events']:
        branch = event['branch_id']
        counters[branch] = counters.get(branch, 0) + 1
        require(event['seq'] == counters[branch], 'non-monotonic sequence')
        require(event['event_id'] == f"{bundle['run_id']}/{branch}/{event['seq']}", 'event identity mismatch')
        require(event['author'] == 'ceo', 'author mismatch')
        for identifier in event['inputs']:
            require(available(identifier, branch, event['seq'] - 1), 'reference outside branch/cutoff')
        for identifier in event['outputs']:
            require(bundle['versions'][identifier]['created_by_event'] == event['event_id'], 'producer mismatch')
    for edge in bundle['relations']:
        require(edge['source'] in bundle['versions'] and edge['target'] in bundle['versions'], 'dangling relation')
        require(edge['origin'] in ('observed_fixture_operation', 'agent_declaration_fixture'), 'unknown relation origin')
    q1, q2 = bundle['checks']['repeated_queries']
    require(q1 != q2 and source_text(bundle, q1) == source_text(bundle, q2), 'execution/content identity conflated')
    receipt = json.loads(source_text(bundle, q1))
    require(receipt['row_count'] == 2 and receipt['rows'][0] == receipt['rows'][1], 'duplicate rows lost')
    require(receipt['rows'][0]['note'] is None and type(receipt['rows'][0]['amount']) is float, 'SQL types lost')
    f1, f2 = bundle['checks']['file_versions']
    require(source_text(bundle, f1) == source_text(bundle, q1) and source_text(bundle, f2) != source_text(bundle, f1), 'file version mismatch')
    require(bundle['versions'][f2]['previous_version'] == f1, 'previous version lost')
    for mapping in bundle['git_mappings']:
        require(mapping['blob_sha256'] == bundle['versions'][mapping['version_id']]['blob_sha256'], 'Git mapping mismatch')


def validate_visibility(bundle):
    validate_blobs(bundle)
    require(bundle['provider_calls'] == 0, 'offline fixture mislabeled')
    attempts = set()
    for request in bundle['requests']:
        require(request['attempt_id'] not in attempts, 'duplicate HTTP attempt')
        attempts.add(request['attempt_id'])
        body = json.loads(bundle['blobs'][request['wire_blob']]['text'])
        fields = {p: (role, text) for p, role, text in text_fields(body)}
        logical_fields = dict((p, text) for p, _, text in text_fields(request['logical_request']))
        require(logical_fields == {p: text for p, (_, text) in fields.items()}, 'logical/wire input differs')
        for occurrence in request['occurrences']:
            source = source_text(bundle, occurrence['version_id'])
            start, end = occurrence['source_range']
            left, right = occurrence['request_range']
            role, target = fields[occurrence['json_pointer']]
            require(0 <= start < end <= len(source) and 0 <= left < right <= len(target), 'invalid range')
            require(role == occurrence['role'] and source[start:end] == target[left:right], 'source/request range mismatch')
            require(occurrence['full_source'] == (start == 0 and end == len(source)), 'false full-source claim')
            require(occurrence['origin'] == 'explicit_fixture_binding', 'unsupported attribution')
        for absence in request['absent']:
            require(absence['text'] in source_text(bundle, absence['version_id']), 'absence probe not in source')
            require(all(absence['text'] not in text for _, text in fields.values()), 'excluded source is visible')
    for api in ('chat', 'responses', 'messages'):
        cases = {r['scenario']: r for r in bundle['requests'] if r['api'] == api}
        require(len(cases) == 7, 'missing request scenario')
        require(cases['continuous']['wire_blob'] == cases['same_week_restore']['wire_blob'], 'restore bytes differ')
        snapshot = json.loads(bundle['blobs'][cases['same_week_restore']['snapshot_blob']]['text'])
        require(not snapshot['pending_tool_calls'] and snapshot['current_day'] == 7, 'invalid restore boundary')
        for name, request in cases.items():
            day = 0 if name == 'day0' else 14 if name == 'new_week' else 21 if name == 'memory_truncation' else 7
            expected = [f'dashboard@{day}', 'script@output']
            memory = ('memory@long' if day == 21 else 'memory@c' if day == 14 else
                      'memory@b' if api != 'chat' and name in ('same_week_after_source_change', 'continuous', 'same_week_restore')
                      else 'memory@a')
            if not (api == 'chat' and day == 0):
                expected.append(memory)
            else:
                body = json.loads(bundle['blobs'][request['wire_blob']]['text'])
                require(all(m['role'] != 'system' for m in body['messages']), 'day0 Chat behavior changed')
            if name in ('same_week_after_source_change', 'continuous', 'same_week_restore'):
                expected.extend(['file@return', 'file@a'])
            if name in ('continuous', 'same_week_restore'):
                expected.extend(['bash@return', 'bash@stdout', 'bash@stdout'])
            require(sorted(o['version_id'] for o in request['occurrences']) == sorted(expected), 'missing or extra source mapping')
        for name in ('new_week', 'memory_truncation'):
            require(not any(o['version_id'].startswith(('file@', 'bash@')) for o in cases[name]['occurrences']), 'stale mapping after reset')
        require(not any(o['version_id'] == 'queued@unsent' for r in cases.values() for o in r['occurrences']), 'unsent mapped as sent')
    for delivery in bundle['deliveries']:
        snapshot = json.loads(bundle['blobs'][delivery['snapshot_blob']]['text'])
        require(source_text(bundle, delivery['version_id']) in json.dumps(snapshot), 'unsent result absent from snapshot')


def negative_checks(events, visibility):
    rejected = []
    for name in ('tampered_blob', 'wrong_range', 'false_full', 'stale_context', 'missing_mapping', 'fork_cutoff'):
        specimen = copy.deepcopy(events if name == 'fork_cutoff' else visibility)
        if name == 'tampered_blob':
            next(iter(specimen['blobs'].values()))['text'] += 'changed'
        elif name == 'wrong_range':
            specimen['requests'][0]['occurrences'][0]['source_range'][0] += 1
        elif name == 'false_full':
            specimen['requests'][0]['occurrences'][1]['full_source'] = True
        elif name == 'stale_context':
            old = next(r for r in specimen['requests'] if r['scenario'] == 'continuous')
            new = next(r for r in specimen['requests'] if r['scenario'] == 'new_week')
            new['occurrences'].append(next(o for o in old['occurrences'] if o['version_id'] == 'file@a'))
        elif name == 'missing_mapping':
            specimen['requests'][0]['occurrences'].pop()
        else:
            specimen['branches']['pf']['fork_event'] = 'evidence-example/prefix/1'
            specimen['events'][-1]['inputs'] = [specimen['checks']['file_versions'][1]]
        try:
            (validate_events if name == 'fork_cutoff' else validate_visibility)(specimen)
        except (ValueError, KeyError, IndexError):
            rejected.append(name)
        else:
            raise ValueError('negative control accepted: ' + name)
    return rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--output', type=Path)
    choice.add_argument('--verify', type=Path)
    args = parser.parse_args()
    if args.verify:
        events = json.loads((args.verify / 'event-version-example.json').read_text())
        visibility = json.loads((args.verify / 'model-request-source-map.json').read_text())
    else:
        require(not args.output.exists(), 'output exists; choose a new directory or --verify it')
        original_connect = socket.socket.connect
        def local_only(sock, address):
            require(not isinstance(address, tuple) or address[0] in ('127.0.0.1', '::1', 'localhost'), 'external network blocked')
            return original_connect(sock, address)
        with tempfile.TemporaryDirectory(prefix='ceobench-evidence-check-') as tmp, patch.object(socket.socket, 'connect', local_only):
            work = Path(tmp)
            events = event_example(work)
            visibility = visibility_example(work)
        paths = ['scripts/check_evidence_records.py', 'src/saas_bench/agents/bash_agent/agent.py',
                 'src/saas_bench/agents/bash_agent/tools.py', 'src/saas_bench/model_usage.py',
                 'src/saas_bench/api_server.py', 'src/saas_bench/environment.py', 'src/saas_bench/database.py']
        provenance = dict(checkout_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
                          source_sha256={p: digest((ROOT / p).read_bytes()) for p in paths}, python=sys.version,
                          sdk_versions={p: importlib.metadata.version(p) for p in ('openai', 'anthropic', 'httpx')})
        events['provenance'] = visibility['provenance'] = provenance
    validate_events(events)
    validate_visibility(visibility)
    negatives = negative_checks(events, visibility)
    summary = dict(status='passed', events=len(events['events']), evidence_versions=len(events['versions']),
                   requests=len(visibility['requests']), occurrences=sum(len(r['occurrences']) for r in visibility['requests']),
                   negative_controls=negatives, provider_calls=0)
    if args.output:
        args.output.mkdir(parents=True)
        for name, value in [('event-version-example', events), ('model-request-source-map', visibility), ('validation-results', summary)]:
            (args.output / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
