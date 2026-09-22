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
