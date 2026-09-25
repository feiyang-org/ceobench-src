"""Shared fixtures for source-level CEOBench regression tests."""

import pytest
import socket
from numpy.random import default_rng

from saas_bench.config import BenchmarkConfig
from saas_bench.database import init_database
from saas_bench.simulation import Simulator
from saas_bench.tools import AgentTools


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch):
    connect = socket.socket.connect
    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1', 'localhost'):
            raise RuntimeError('Offline tests prohibit external connections')
        return connect(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', local_only)


@pytest.fixture
def make_initialized_sim(tmp_path):
    def _make_initialized_sim(*, config=None, seed=123):
        config = config or BenchmarkConfig(seed=seed)
        conn = init_database(tmp_path / "ceobench.db")
        sim = Simulator(conn, config, default_rng(seed))
        sim.initialize()
        return conn, sim, config

    return _make_initialized_sim


@pytest.fixture
def make_agent_tools(tmp_path):
    def _make_agent_tools(conn, config, *, day=0, seed=123):
        return AgentTools(
            conn,
            current_day=day,
            workspace_path=tmp_path / "workspace",
            rng=default_rng(seed),
            config=config,
            seed=seed,
        )

    return _make_agent_tools
