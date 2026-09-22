"""Test-only packed server: deterministic HTTP model replies, no external sockets."""
import hashlib
import json
import os
import runpy
import socket
import sys
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
    sys.argv = sys.argv[1:]
    runpy.run_path(sys.argv[0], run_name='__main__')
