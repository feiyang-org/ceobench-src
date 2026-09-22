import threading
from types import SimpleNamespace

import pytest

from saas_bench.api_server import NovaMindAPIServer
from saas_bench.db_protection import AsyncSaver
from saas_bench.run_state import checkpoint_directory, copy_workspace


def test_async_failure_is_not_a_successful_drain(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('injected disk failure')
    monkeypatch.setattr('saas_bench.db_protection.encrypt_plain_atomic', fail)
    saver = AsyncSaver(tmp_path / 'world.nmdb', key='test-only')
    plain = tmp_path / 'plain'
    plain.write_text('data')
    saver.submit(plain)
    try:
        with pytest.raises(RuntimeError, match='save failed'):
            saver.drain(timeout=2)
    finally:
        saver.shutdown(wait=False)


def test_async_drain_waits_and_times_out(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    def delayed(*args, **kwargs):
        started.set()
        release.wait(2)
    monkeypatch.setattr('saas_bench.db_protection.encrypt_plain_atomic', delayed)
    saver = AsyncSaver(tmp_path / 'world.nmdb', key='test-only')
    plain = tmp_path / 'plain'
    plain.write_text('data')
    saver.submit(plain)
    try:
        assert started.wait(2)
        assert not saver.drain(timeout=0.01)
        release.set()
        assert saver.drain(timeout=2)
    finally:
        release.set()
        saver.shutdown()


def test_checkpoint_rejects_inflight_unknown_and_wrong_day(tmp_path):
    server = NovaMindAPIServer(SimpleNamespace(workspace_path=tmp_path, current_day=7))
    server.checkpoint_callback = lambda: {'success': True, 'day': 7}
    assert server.checkpoint(7)['day'] == 7
    with pytest.raises(ValueError, match='day mismatch'):
        server.checkpoint(0)
    with server._advance_lock:
        with pytest.raises(RuntimeError, match='in-flight'):
            server.checkpoint(7)
    server._operation_failed = True
    with pytest.raises(RuntimeError, match='unknown'):
        server.checkpoint(7)


def test_legacy_and_nonportable_workspace_are_rejected(tmp_path):
    with pytest.raises(ValueError, match='legacy'):
        checkpoint_directory(tmp_path, {'day': 7})
    workspace = tmp_path / 'source'
    workspace.mkdir()
    (workspace / 'escape').symlink_to(tmp_path)
    with pytest.raises(ValueError, match='symlink'):
        copy_workspace(workspace, tmp_path / 'copy')
