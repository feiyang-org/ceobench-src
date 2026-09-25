import json
import platform

import pytest

from saas_bench.run_state import artifact_hashes, verify_build, write_json, runtime_versions, tree_hash, file_hash


def test_artifact_drift_and_atomic_manifest(tmp_path):
    (tmp_path / 'docs' / 'novamind_api').mkdir(parents=True)
    (tmp_path / 'novamind-operation').write_text('bundle')
    sdk = tmp_path / 'docs' / 'novamind_api' / 'client.py'
    sdk.write_text('sdk')
    original = dict(python=platform.python_version(), artifacts=artifact_hashes(tmp_path), runtime=runtime_versions())
    write_json(tmp_path / 'build.json', original)
    assert verify_build(tmp_path) == original
    with pytest.raises(ValueError):
        write_json(tmp_path / 'build.json', {'bad': float('nan')})
    assert json.loads((tmp_path / 'build.json').read_text()) == original
    sdk.write_text('changed')
    with pytest.raises(ValueError, match='artifacts'):
        verify_build(tmp_path)


def test_build_rejects_python_sdk_and_source_drift(tmp_path):
    public = tmp_path / 'public'
    (public / 'docs' / 'novamind_api').mkdir(parents=True)
    (public / 'novamind-operation').write_text('bundle')
    (tmp_path / 'src').mkdir()
    (tmp_path / 'scripts').mkdir()
    source = tmp_path / 'src' / 'engine.py'
    source.write_text('original')
    builder = tmp_path / 'scripts' / 'build_public.py'
    builder.write_text('builder')
    baseline = dict(python=platform.python_version(), artifacts=artifact_hashes(public), runtime=runtime_versions(),
                    source_sha256=tree_hash(tmp_path / 'src'), builder_sha256=file_hash(builder))
    write_json(public / 'build.json', baseline)
    assert verify_build(public, tmp_path) == baseline
    write_json(public / 'build.json', dict(baseline, python='0.0.0'))
    with pytest.raises(ValueError, match='Python'):
        verify_build(public, tmp_path)
    write_json(public / 'build.json', dict(baseline, runtime={}))
    with pytest.raises(ValueError, match='SDK'):
        verify_build(public, tmp_path)
    write_json(public / 'build.json', baseline)
    source.write_text('changed')
    with pytest.raises(ValueError, match='source'):
        verify_build(public, tmp_path)


def test_frozen_simulator_configuration_rejects_environment_drift(tmp_path, monkeypatch):
    from dataclasses import asdict
    from saas_bench.config import BenchmarkConfig
    from saas_bench.server_entry import _session_config
    config = BenchmarkConfig()
    config.social_post_llm_provider = config.enterprise_llm_provider = 'deepseek'
    config.social_post_llm_model = config.enterprise_llm_model = 'test-model'
    path = tmp_path / 'manifest.json'
    write_json(path, {'benchmark_config': asdict(config)})
    monkeypatch.setenv('CEOBENCH_RUN_MANIFEST', str(path))
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.delenv('CEOBENCH_SIMULATOR_LLM_PROVIDER', raising=False)
    monkeypatch.delenv('CEOBENCH_SIMULATOR_LLM_MODEL', raising=False)
    assert json.loads(json.dumps(asdict(_session_config(42, 7, 100)))) == json.loads(json.dumps(asdict(config)))
    monkeypatch.setenv('CEOBENCH_SIMULATOR_LLM_MODEL', 'different-model')
    with pytest.raises(ValueError, match='frozen'):
        _session_config(42, 7, 100)


def test_opencode_routes_both_roles_without_deepseek_credentials(tmp_path, monkeypatch):
    from saas_bench.agents.bash_agent.run_test import BashAgentRunner
    from saas_bench.config import BenchmarkConfig
    from saas_bench.server_entry import _apply_simulator_llm_config, _create_simulator_openai_client
    monkeypatch.setattr('saas_bench.agents.bash_agent.run_test.load_env_file', lambda _: {})
    monkeypatch.setenv('OPENCODE_API_KEY', 'go-only-test-key')
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    for suffix in ('PROVIDER', 'MODEL'):
        monkeypatch.delenv('CEOBENCH_SIMULATOR_LLM_' + suffix, raising=False)
    runner = BashAgentRunner(provider='opencode', model='deepseek-v4.1-flash', workspace_base=tmp_path)
    env = runner._server_environment()
    assert env['CEOBENCH_SIMULATOR_LLM_PROVIDER'] == 'opencode'
    assert env['CEOBENCH_SIMULATOR_LLM_MODEL'] == 'deepseek-v4.1-flash'
    for name in ('CEOBENCH_SIMULATOR_LLM_PROVIDER', 'CEOBENCH_SIMULATOR_LLM_MODEL', 'CEOBENCH_MODEL_SESSION'):
        monkeypatch.setenv(name, env[name])
    config = BenchmarkConfig()
    _apply_simulator_llm_config(config)
    client = _create_simulator_openai_client(config)
    assert str(client.base_url) == str(runner.client.base_url) == 'https://opencode.ai/zen/go/v1/'
    assert client.api_key == 'go-only-test-key'
    assert client.default_headers['User-Agent'] == runner.client.default_headers['User-Agent'] == 'CEO-Bench/1.0'
    assert client.default_headers['x-opencode-session'] == runner.client.default_headers['x-opencode-session'] + ':simulator'
    assert runner._server_environment()['CEOBENCH_MODEL_SESSION'] == env['CEOBENCH_MODEL_SESSION']
    config.enterprise_llm_provider = 'deepseek'
    with pytest.raises(ValueError, match='one endpoint'):
        _create_simulator_openai_client(config)
    runner.client.close()
    client.close()


def test_registered_pricing_is_frozen_and_survives_source_file_removal(tmp_path, monkeypatch):
    from saas_bench.agents.bash_agent.run_test import BashAgentRunner
    monkeypatch.setattr('saas_bench.agents.bash_agent.run_test.load_env_file', lambda _: {})
    monkeypatch.setattr('saas_bench.run_state.verify_build', lambda *a, **k: {})
    monkeypatch.setenv('OPENCODE_API_KEY', 'offline-only')
    path = tmp_path / 'price.json'
    data = dict(source='https://opencode.ai/docs/go/', basis='USD/1k quota', rates={'test-model': {'input': .001}})
    path.write_text(json.dumps(data))
    first = BashAgentRunner(provider='opencode', model='test-model', workspace_base=tmp_path, pricing_file=path)
    first._prepare_manifest()
    assert json.loads((first.workspace_dir / 'manifest.json').read_text())['pricing'] == data
    path.unlink()
    restored = BashAgentRunner(continue_from=first.workspace_dir)
    restored._prepare_manifest()
    assert restored._pricing == data['rates']
    data['rates']['test-model']['input'] = 9
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='pricing configuration mismatch'):
        BashAgentRunner(continue_from=first.workspace_dir, pricing_file=path)
    first.client.close()
    restored.client.close()
