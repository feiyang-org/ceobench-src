from types import SimpleNamespace

from saas_bench.api_server import NovaMindAPIServer


def test_prediction_failure_rolls_back_before_any_world_change(make_initialized_sim, tmp_path, monkeypatch):
    conn, simulator, _ = make_initialized_sim()
    initial_rng = simulator.rng.bit_generator.state
    def must_not_run(*args):
        raise AssertionError('World advanced after failed prediction write')
    simulator.step_week = must_not_run
    shocks = SimpleNamespace(check_and_generate_shocks=must_not_run)
    server = NovaMindAPIServer(SimpleNamespace(workspace_path=tmp_path, current_day=0),
                               simulator=simulator, conn=conn, shock_manager=shocks)
    def fail(conn, day, predictions, now):
        conn.execute("INSERT INTO predictions (submit_day,horizon_days,metric,predicted_value,submitted_at) VALUES (0,7,'cash',1,0)")
        raise OSError('injected failure after first row')
    monkeypatch.setattr('saas_bench.database.save_predictions', fail)
    result = server.advance_week({7: {'cash': 1}})
    assert result == {'success': False, 'error': 'prediction_save_failed', 'day': 0}
    assert conn.execute('SELECT COUNT(*) FROM predictions').fetchone()[0] == 0
    assert simulator.current_day == 0 and simulator.rng.bit_generator.state == initial_rng
    assert not server._operation_failed
