import json

import pytest

from saas_bench.config import ScenarioPack
from saas_bench.shocks import ShockManager


def test_every_stream_and_process_state_resume(make_initialized_sim):
    conn, sim, _ = make_initialized_sim()
    sim.shock_manager = ShockManager(conn, sim.rng, ScenarioPack(name='test', description='test'))
    streams = [sim.rng, sim._macro_rng, sim._competitor_rng, sim._competitor_post_noise_rng,
               sim._competitor_template_rng, sim._quality_rng, sim._customer_quality_noise_rng,
               sim._customer_pick_rng, sim.shock_manager.rng, *sim._group_rngs.values()]
    for rng in streams:
        rng.random(11)
    sim.current_day = 35
    sim.shutdown_mode = True
    sim._customer_quality_noise[123] = 0.91
    sim.save_rng_states()
    expected = [rng.random(7).tolist() for rng in streams]
    expected_template = sim._generate_competitor_post_template('competitor', 'minor')
    sim.current_day = 0
    sim.shutdown_mode = False
    assert sim.restore_rng_states()
    assert sim.current_day == 35 and sim.shutdown_mode
    assert sim._customer_quality_noise[123] == 0.91
    assert [rng.random(7).tolist() for rng in streams] == expected
    assert sim._generate_competitor_post_template('competitor', 'minor') == expected_template
    states = json.loads(conn.execute("SELECT state_json FROM _rng_states WHERE name='all'").fetchone()[0])
    del states['_competitor_post_noise_rng']
    conn.execute("UPDATE _rng_states SET state_json=?", (json.dumps(states),))
    with pytest.raises(ValueError, match='Incomplete'):
        sim.restore_rng_states()
