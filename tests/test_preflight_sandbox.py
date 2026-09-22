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
