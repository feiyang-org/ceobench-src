import json
import platform

import pytest

from saas_bench.run_state import artifact_hashes, verify_build, write_json


def test_artifact_drift_and_atomic_manifest(tmp_path):
    (tmp_path / 'docs' / 'novamind_api').mkdir(parents=True)
    (tmp_path / 'novamind-operation').write_text('bundle')
    sdk = tmp_path / 'docs' / 'novamind_api' / 'client.py'
    sdk.write_text('sdk')
    original = dict(python=platform.python_version(), artifacts=artifact_hashes(tmp_path))
    write_json(tmp_path / 'build.json', original)
    assert verify_build(tmp_path) == original
    with pytest.raises(ValueError):
        write_json(tmp_path / 'build.json', {'bad': float('nan')})
    assert json.loads((tmp_path / 'build.json').read_text()) == original
    sdk.write_text('changed')
    with pytest.raises(ValueError, match='artifacts'):
        verify_build(tmp_path)


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
