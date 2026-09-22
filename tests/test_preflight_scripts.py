from pathlib import Path
from types import SimpleNamespace
import urllib.request
import json

from saas_bench.api_server import NovaMindAPIServer


def test_registered_snapshot_executes_and_can_call_server(tmp_path):
    tools = SimpleNamespace(workspace_path=tmp_path, current_day=0)
    server = NovaMindAPIServer(tools)
    server.start()
    try:
        source = tmp_path / 'metric.py'
        source.write_text("print('A')")
        script = source.read_text() + '\nimport urllib.request, os\nprint(urllib.request.urlopen("http://127.0.0.1:" + os.environ["NOVAMIND_API_PORT"] + "/health").status)'
        request = urllib.request.Request(f'http://127.0.0.1:{server.port}/daily-scripts',
            json.dumps({'name': source.name, 'content': script}).encode(), {'Content-Type': 'application/json'})
        assert json.load(urllib.request.urlopen(request))['success']
        source.write_text("print('B')")
        assert server._run_daily_scripts_internal()['metric.py'] == 'A\n200\n'
        server.set_daily_scripts({'metric.py': source.read_text(), 'bad': 'raise ValueError("failure")'})
        outputs = server._run_daily_scripts_internal()
        assert outputs['metric.py'] == 'B\n'
        assert 'failure' in outputs['bad']
        assert len(server.last_script_results) == 2
        server.set_daily_scripts({})
        assert server._run_daily_scripts_internal() == {}
    finally:
        server.stop()
