import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from saas_bench.agents.bash_agent.tools import BashAgentToolExecutor


def test_formal_sandbox_rejects_missing_support(tmp_path, monkeypatch):
    executor = BashAgentToolExecutor(tmp_path, require_sandbox=True)
    monkeypatch.setattr(sys, 'platform', 'darwin')
    with pytest.raises(RuntimeError, match='Linux'):
        executor.verify_sandbox()
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr('shutil.which', lambda name: None)
    with pytest.raises(RuntimeError, match='bubblewrap'):
        executor.verify_sandbox()


@pytest.mark.parametrize('seed', ['7', '21', '0'])
def test_published_hash_seed_entry(tmp_path, seed):
    with zipfile.ZipFile(Path(__file__).parents[1] / 'public' / 'novamind-operation') as bundle:
        prefix = bundle.read('__main__.py').decode().split('if os.environ.get("NOVAMIND_SERVER_MODE")')[0]
    probe = tmp_path / 'probe.pyz'
    with zipfile.ZipFile(probe, 'w') as bundle:
        bundle.writestr('__main__.py', prefix + '\nprint(os.environ["PYTHONHASHSEED"])\n')
    result = subprocess.run([sys.executable, str(probe)], env={**os.environ, 'PYTHONHASHSEED': seed},
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == '0'


def test_file_tools_cannot_escape_to_sibling_prefix(tmp_path):
    workspace = tmp_path / 'agent'
    workspace.mkdir()
    outside = tmp_path / 'agent-other'
    outside.mkdir()
    executor = BashAgentToolExecutor(workspace)
    assert 'escapes workspace' in executor.execute('write_file', {'path': str(outside / 'bad'), 'content': 'bad'})
    assert not (outside / 'bad').exists()


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux bubblewrap acceptance runs on sheep-rog')
def test_linux_isolation_blocks_sibling_clone_and_source(tmp_path):
    workspace = tmp_path / 'agent'
    workspace.mkdir()
    sibling = tmp_path / 'other'
    sibling.write_text('private sibling')
    executor = BashAgentToolExecutor(workspace, require_sandbox=True)
    executor.verify_sandbox()
    result = executor.execute('bash', {'command': f'cat {sibling}; touch local-file; python -c "import saas_bench"'})
    assert 'private sibling' not in result
    assert 'No such file' in result and 'saas_bench' in result
    assert (workspace / 'local-file').exists()
