"""Test-only packed server: deterministic HTTP model replies, no external sockets."""
import hashlib
import json
import os
import runpy
import socket
import sys
import time
from pathlib import Path
import httpx


def fake_request(self, request):
    body = json.loads(request.read())
    text = 'Offline response ' + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    if '/messages' in request.url.path or '/invoke' in request.url.path:
        response = dict(id='offline', type='message', model=body.get('model', 'test-model'), role='assistant',
                        content=[dict(type='text', text=text)], stop_reason='end_turn',
                        usage=dict(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0))
    elif '/responses' in request.url.path:
        response = dict(id='offline', object='response', created_at=1, model=body['model'], status='completed',
                        output=[dict(type='message', id='offline-message', role='assistant', status='completed',
                                     content=[dict(type='output_text', text=text, annotations=[])])],
                        usage=dict(input_tokens=10, output_tokens=5, total_tokens=15))
    else:
        response = dict(id='offline', object='chat.completion', created=1, model=body['model'],
                        choices=[dict(index=0, finish_reason='stop', message=dict(role='assistant', content=text))],
                        usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15))
    return httpx.Response(200, json=response)


if __name__ == '__main__':
    assert os.environ.get('PYTHONHASHSEED') == '0'
    original_connect = socket.socket.connect
    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1', 'localhost'):
            raise RuntimeError('Offline test blocked external connection: ' + str(address[0]))
        return original_connect(sock, address)
    socket.socket.connect = local_only
    httpx.HTTPTransport.handle_request = fake_request
    pause_path = os.environ.get('CEOBENCH_TEST_PAUSE_RESPONSE_PATH')
    if pause_path:
        sys.path.insert(0, sys.argv[1])  # Import the native zipapp module that runpy will use.
        from saas_bench.api_server import _APIHandler
        original_send = _APIHandler._send_json
        def paused_send(self, data, status=200):
            if self.path == pause_path and data.get('success') is True:
                marker = Path(os.environ['CEOBENCH_TEST_RESPONSE_MARKER'])
                release = Path(os.environ['CEOBENCH_TEST_RESPONSE_RELEASE'])
                marker.write_text('world action finished; public response not sent')
                deadline = time.monotonic() + 20
                while not release.exists():
                    if time.monotonic() > deadline:
                        raise TimeoutError('Test did not release the response')
                    time.sleep(.01)
            return original_send(self, data, status)
        _APIHandler._send_json = paused_send
    sys.argv = sys.argv[1:]
    runpy.run_path(sys.argv[0], run_name='__main__')
