import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from saas_bench.agents.bash_agent.run_test import BashAgentRunner
from saas_bench.db_protection import load_session_db
from saas_bench.run_state import checkpoint_directory, tree_hash

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope='module')
def packed_public(tmp_path_factory):
    provided = os.environ.get('CEOBENCH_TEST_PUBLIC')
    if provided:
        return Path(provided)
    spec = importlib.util.spec_from_file_location('build_public', ROOT / 'scripts/build_public.py')
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    build.PUBLIC_DIR = tmp_path_factory.mktemp('packed-public')
    build.build()
    return build.PUBLIC_DIR


@pytest.fixture
def offline_runner(tmp_path, monkeypatch, packed_public):
    monkeypatch.setenv('NOVAMIND_PUBLIC_DIR', str(packed_public))
    monkeypatch.setenv('PYTHONHASHSEED', '0')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'offline-only')
    monkeypatch.setenv('CEOBENCH_SIMULATOR_LLM_PROVIDER', 'deepseek')
    monkeypatch.setenv('CEOBENCH_SIMULATOR_LLM_MODEL', 'test-model')
    monkeypatch.delenv('CEOBENCH_DASHBOARD_URL', raising=False)
    monkeypatch.delenv('BOSSBENCH_LLM_REPLAY_DB', raising=False)
    monkeypatch.delenv('ORACLE_MODE', raising=False)
    monkeypatch.setattr('saas_bench.agents.bash_agent.run_test.load_env_file', lambda _: {})
    from saas_bench.config import BenchmarkConfig
    def config(**kwargs):
        return BenchmarkConfig(**dict(kwargs, drift_grace_period_days=0,
                                      competitor_event_late_cutoff_days=0, competitor_event_mean_interval=3))
    monkeypatch.setattr('saas_bench.agents.bash_agent.run_test.BenchmarkConfig', config)
    popen = subprocess.Popen
    def launch(args, *other, **kwargs):
        if len(args) > 1 and str(args[1]).endswith('novamind-operation') and kwargs.get('env', {}).get('NOVAMIND_SERVER_MODE') == '1':
            args = [args[0], str(ROOT / 'tests/preflight_server.py'), *args[1:]]
        return popen(args, *other, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', launch)
    runners = []
    def create(restore=None):
        runner = BashAgentRunner(model='test-model', provider='deepseek', api_key='offline-only',
                                total_days=42, workspace_base=tmp_path, continue_from=restore,
                                run_kind=os.environ.get('CEOBENCH_TEST_KIND', 'engineering'))
        runners.append(runner)
        runner.setup()
        if restore:
            runner._restore_from_checkpoint(runner._load_checkpoint())
        return runner
    yield create
    for runner in runners:
        runner._stop_server()
        runner.client.close()


def advance(runner):
    return runner._http_post('/next-week', {'rationale': 'fixed offline action',
        'predictions': {h: {'point': 100000, 'lower': -100000, 'upper': 1000000}
                        for h in ('cash_1wk', 'cash_4wk', 'cash_12wk', 'cash_26wk')}}, timeout=120)


def business_state(runner):
    directory = checkpoint_directory(runner.workspace_dir, runner._load_checkpoint())
    conn = load_session_db(directory / 'world.nmdb')
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
              if not row[0].startswith('sqlite_')]
    result = {}
    for table in tables:
        columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")') if row[1] != 'submitted_at']
        select = ','.join('"' + c + '"' for c in columns)
        result[table] = sorted([list(row) for row in conn.execute(f'SELECT {select} FROM "{table}"')], key=str)
    conn.close()
    return result


def clone(source, destination):
    checkpoint = source._load_checkpoint()
    directory = checkpoint_directory(source.workspace_dir, checkpoint)
    destination.mkdir()
    for name in ('manifest.json', 'checkpoint.json', 'config.json'):
        shutil.copy2(source.workspace_dir / name, destination / name)
    target = destination / 'checkpoints' / checkpoint['snapshot_id']
    target.parent.mkdir()
    shutil.copytree(directory, target)
    return destination


@pytest.mark.parametrize('split_day', [21, 35])
def test_packed_continuous_restore_and_independent_clones(offline_runner, tmp_path, split_day):
    first = offline_runner()
    workspace = first.agent_workspace
    (workspace / 'MEMORY.md').write_text('retain the weekly metric')
    metric = workspace / 'metric.py'
    metric.write_text("import novamind_api as nm\nprint('version A', nm.query('SELECT COUNT(*) AS n FROM ledger'))")
    assert first._http_post('/daily-scripts', {'name': 'metric', 'content': metric.read_text()})['success']
    metric.write_text("print('version B')")
    for _ in range(split_day // 7):
        receipt = advance(first)
        assert receipt['success'], receipt
        assert 'version A' in receipt['dashboard'] and '[stderr]' not in receipt['dashboard']
    assert first._get_game_status()['day'] == split_day
    first.agent._refresh_context(f'day {split_day}', split_day)
    first.agent.current_day = split_day
    first.agent.record_tool_result('completed metric registration')
    first._git_commit_workspace('offline checkpoint')
    first._save_checkpoint(split_day)
    source_snapshot = checkpoint_directory(first.workspace_dir, first._load_checkpoint())
    checkpoint_hash = tree_hash(source_snapshot)
    left = offline_runner(clone(first, tmp_path / 'left'))
    right = offline_runner(clone(first, tmp_path / 'right'))
    assert len({r._server_proc.pid for r in (first, left, right)}) == 3
    assert len({r._server_port for r in (first, left, right)}) == 3
    for restored in (left, right):
        assert restored.agent._last_observation == 'completed metric registration'
        assert restored.agent._observation_recorded
        assert (restored.agent_workspace / 'MEMORY.md').read_text() == 'retain the weekly metric'
        assert restored._git('rev-parse', 'HEAD').stdout == first._git('rev-parse', 'HEAD').stdout
        assert restored._http_get('/daily-scripts') == first._http_get('/daily-scripts')
        assert (restored.logs_dir / 'simulator_requests.jsonl').read_bytes() == (first.logs_dir / 'simulator_requests.jsonl').read_bytes()
    for _ in range((42 - split_day) // 7):
        expected = advance(first)
        for restored in (left, right):
            assert advance(restored) == expected
    for runner in (first, left, right):
        runner._save_checkpoint(42)
    expected = business_state(first)
    for restored in (left, right):
        assert business_state(restored) == expected
        summary = json.loads((restored.workspace_dir / 'usage_summary.json').read_text())
        assert summary['simulator'] == json.loads((first.workspace_dir / 'usage_summary.json').read_text())['simulator']
    assert expected['api_costs'] and expected['competitor_events']
    assert expected['_hidden_leads_per_1k_snapshot']
    scoring = subprocess.run([sys.executable, str(ROOT / 'scripts/score_predictions.py'), str(first.workspace_dir)],
                             capture_output=True, text=True, check=True)
    assert json.loads(scoring.stdout)['four_week_count'] == 3
    before = business_state(right)
    (left.agent_workspace / 'MEMORY.md').write_text('left only')
    assert left._http_post('/daily-scripts', {'name': 'metric', 'content': "print('version B')"})['success']
    receipt = advance(left)
    assert 'version B' in receipt['dashboard'] and 'version A' not in receipt['dashboard']
    left._save_checkpoint(49)
    assert business_state(right) == before
    assert 'version A' in json.dumps(before['_registered_scripts'])
    assert (right.agent_workspace / 'MEMORY.md').read_text() == 'retain the weekly metric'
    assert checkpoint_hash == tree_hash(source_snapshot)


def test_failed_publication_and_unknown_operation_keep_previous_snapshot(offline_runner, monkeypatch):
    runner = offline_runner()
    runner._save_checkpoint(0)
    pointer = (runner.workspace_dir / 'checkpoint.json').read_bytes()
    from saas_bench import run_state
    replace = run_state.os.replace
    def fail_publish(source, destination):
        if Path(destination) == runner.workspace_dir / 'checkpoint.json':
            raise OSError('injected pointer publication failure')
        return replace(source, destination)
    monkeypatch.setattr(run_state.os, 'replace', fail_publish)
    with pytest.raises(OSError, match='publication'):
        runner._save_checkpoint(0)
    assert (runner.workspace_dir / 'checkpoint.json').read_bytes() == pointer
    assert checkpoint_directory(runner.workspace_dir, runner._load_checkpoint()).is_dir()
    runner._begin_operation('tool', 0)
    with pytest.raises(ValueError, match='unknown'):
        runner._load_checkpoint()
    # Analysis can still inspect the last complete generation directly.
    assert checkpoint_directory(runner.workspace_dir, json.loads(pointer)).is_dir()


def test_checkpoint_http_does_not_accept_paths_and_predictions_reject_nonfinite(offline_runner, tmp_path):
    import urllib.error
    runner = offline_runner()
    with pytest.raises(urllib.error.HTTPError) as error:
        runner._http_post('/checkpoint', {'expected_day': 0, 'path': str(tmp_path / 'injected')})
    assert error.value.code == 400 and not (tmp_path / 'injected').exists()
    for bad in (float('nan'), float('inf'), None):
        with pytest.raises(urllib.error.HTTPError) as error:
            runner._http_post('/next-week', {'rationale': 'test', 'predictions': {
                h: {'point': bad, 'lower': 0, 'upper': 100} for h in ('cash_1wk', 'cash_4wk', 'cash_12wk', 'cash_26wk')}})
        assert error.value.code == 400
    assert runner._get_game_status()['day'] == 0


def test_full_harness_uses_packed_cli_and_fake_agent_requests(offline_runner, monkeypatch):
    import httpx
    from openai import OpenAI
    from test_preflight_usage import reply
    runner = offline_runner()
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        if len(requests) > 6:
            raise KeyboardInterrupt('Harness failed to advance the week')
        body = reply('chat')
        command = "./novamind-operation next-week 'fixed offline action'" + ' 100000 -100000 1000000' * 4
        body['choices'][0]['message']['tool_calls'] = [dict(id=f'week-{len(requests)}', type='function',
            function=dict(name='bash', arguments=json.dumps({'command': command})))]
        return httpx.Response(200, json=body)
    runner.client.close()
    runner.client = OpenAI(api_key='offline-only', base_url='https://api.deepseek.com', max_retries=0,
                           http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    runner.agent.client = runner.agent.usage_recorder.attach(runner.client)
    monkeypatch.setattr(runner, 'setup', lambda: None)
    result = runner.run(verbose=False)
    assert result['days_run'] == 42 and result['outcome'] == 'completed'
    assert runner._server_proc is None
    assert len(requests) == 6
    assert all(not any(m['role'] == 'tool' for m in request['messages']) for request in requests)
    checkpoint = runner._load_checkpoint()
    assert checkpoint['total_turns'] == 6
    summary = json.loads((runner.workspace_dir / 'usage_summary.json').read_text())
    assert summary['agent']['calls'] == 6 and summary['agent']['known']['input_tokens'] == 60
    assert summary['simulator']['calls'] > 0


def test_harness_timeout_stops_branch_without_publishing_unknown_state(offline_runner, monkeypatch):
    from saas_bench.environment import Action
    runner = offline_runner()
    runner._save_checkpoint(0)
    previous = (runner.workspace_dir / 'checkpoint.json').read_bytes()
    monkeypatch.setattr(runner, 'setup', lambda: None)
    runner.tool_executor.bash_timeout = 0.2
    monkeypatch.setattr(runner.agent, 'act', lambda *args: Action(tool='bash',
        arguments={'command': 'python -c "import time; time.sleep(30)"'}))
    with pytest.raises(RuntimeError, match='unknown'):
        runner.run(verbose=False)
    assert runner._server_proc is None
    assert (runner.workspace_dir / 'checkpoint.json').read_bytes() == previous
    assert 'unknown' in json.loads((runner.workspace_dir / 'branch_stop.json').read_text())['reason']
    with pytest.raises(ValueError, match='unknown'):
        runner._load_checkpoint()
