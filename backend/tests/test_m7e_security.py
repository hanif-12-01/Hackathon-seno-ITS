import base64
import os
import subprocess
import sys
from http.client import HTTPConnection

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import DemoPerimeter, password_hash


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setenv('PANGANNADI_OPERATOR_USERNAME', 'test-operator')
    monkeypatch.setenv('PANGANNADI_OPERATOR_PASSWORD_HASH', password_hash('test-only-password'))
    app = FastAPI()

    @app.api_route('/probe', methods=['GET', 'POST', 'PATCH', 'DELETE'])
    def probe():
        return {'ok': True}

    app.add_middleware(DemoPerimeter, origins=['https://demo.example.test'], production=True)
    return app


def auth(password='test-only-password', username='test-operator'):
    return 'Basic ' + base64.b64encode(f'{username}:{password}'.encode()).decode()


def test_all_routes_denied_without_credentials(secured):
    with TestClient(secured) as client:
        for path in ('/probe', '/docs', '/openapi.json', '/health', '/ready', '/cases', '/missing'):
            response = client.get(path)
            assert response.status_code == 401
            assert response.headers['www-authenticate'].startswith('Basic ')
            assert response.headers['cache-control'] == 'no-store'
        for value in ('Bearer abc', 'Basic !!!', 'Basic ' + 'x' * 5000, auth('wrong'), auth(username='wrong')):
            assert client.get('/probe', headers={'Authorization': value}).status_code == 401
        assert client.get('/probe', headers=[('Authorization', auth()), ('Authorization', auth())]).status_code == 401
        assert client.get('/probe', headers={'Authorization': auth()}).json() == {'ok': True}


def test_csrf_and_no_cors_bypass(secured):
    with TestClient(secured) as client:
        for method in ('POST', 'PATCH', 'DELETE'):
            for origin in (None, 'null', 'https://evil.example.test', 'https://demo.example.test.evil', 'https://demo.example.test/'):
                headers = {'Authorization': auth()}
                if origin is not None:
                    headers['Origin'] = origin
                assert client.request(method, '/probe', headers=headers).status_code == 403
            response = client.request(method, '/probe', headers={'Authorization': auth(), 'Origin': 'https://demo.example.test'})
            assert response.status_code == 200
            assert response.headers['cache-control'] == 'no-store'
        assert client.options('/probe', headers={'Origin': 'https://evil.example.test', 'Access-Control-Request-Method': 'POST'}).status_code == 401
        assert client.post('/probe', headers=[('Authorization', auth()), ('Origin', 'https://demo.example.test'), ('Origin', 'https://evil.example.test')]).status_code == 403


def test_fail_closed_and_local_unchanged(monkeypatch):
    monkeypatch.delenv('PANGANNADI_OPERATOR_USERNAME', raising=False)
    monkeypatch.delenv('PANGANNADI_OPERATOR_PASSWORD_HASH', raising=False)
    app = FastAPI()
    with pytest.raises(ValueError, match='Demo perimeter requires'):
        DemoPerimeter(app, ['https://demo.example.test'], production=True)
    assert not DemoPerimeter(app, []).enabled
    monkeypatch.setenv('PANGANNADI_OPERATOR_USERNAME', 'operator')
    for invalid in ('', 'plaintext', 'pbkdf2_sha256$1$00$00', 'pbkdf2_sha256$600000$' + '00' * 16 + '$' + '00' * 31):
        monkeypatch.setenv('PANGANNADI_OPERATOR_PASSWORD_HASH', invalid)
        with pytest.raises(ValueError):
            DemoPerimeter(app, ['https://demo.example.test'])
    monkeypatch.setenv('PANGANNADI_OPERATOR_PASSWORD_HASH', password_hash('test-only-password'))
    with pytest.raises(ValueError):
        DemoPerimeter(app, [], production=True)


def test_real_http_startup_and_business_routes(tmp_path):
    database = tmp_path / 'm7e.sqlite3'
    env = dict(os.environ, PANGANNADI_ENV='production', PANGANNADI_CORS_ORIGINS='https://demo.example.test',
               PANGANNADI_DATABASE_URL='sqlite:///' + database.as_posix(),
               PANGANNADI_OPERATOR_USERNAME='test-operator',
               PANGANNADI_OPERATOR_PASSWORD_HASH=password_hash('test-only-password'))
    env.pop('PANGANNADI_OPERATOR_PASSWORD_HASH', None)
    missing = subprocess.run([sys.executable, '-B', '-c', 'import app.main'], env=env, capture_output=True, text=True)
    assert missing.returncode != 0 and 'Demo perimeter requires' in missing.stderr
    env['PANGANNADI_OPERATOR_PASSWORD_HASH'] = password_hash('test-only-password')
    code = '''import socket, uvicorn
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1], flush=True)
uvicorn.Server(uvicorn.Config("app.main:app", log_level="error", access_log=False)).run(sockets=[sock])
'''
    process = subprocess.Popen([sys.executable, '-B', '-c', code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        import time
        port = int(process.stdout.readline().strip())
        for _ in range(200):
            try:
                connection = HTTPConnection('127.0.0.1', port, timeout=2)
                connection.request('GET', '/health')
                response = connection.getresponse()
                assert response.status == 401
                response.read()
                connection.close()
                break
            except (OSError, TimeoutError):
                time.sleep(0.1)
        else:
            pytest.fail('isolated server failed to start')
        for path in ('/health', '/ready', '/cases', '/docs', '/openapi.json', '/forecasting/status'):
            connection = HTTPConnection('127.0.0.1', port, timeout=10)
            connection.request('GET', path)
            response = connection.getresponse()
            assert response.status == 401, path
            response.read()
            connection.close()
        connection = HTTPConnection('127.0.0.1', port, timeout=10)
        connection.request('GET', '/cases', headers={'Authorization': auth()})
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == b'[]'
        connection.close()
        connection = HTTPConnection('127.0.0.1', port, timeout=10)
        connection.request('POST', '/cases', body='{}', headers={'Authorization': auth(), 'Content-Type': 'application/json'})
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        connection.close()
        connection = HTTPConnection('127.0.0.1', port, timeout=10)
        connection.request('POST', '/cases', body='{}', headers={'Authorization': auth(), 'Origin': 'https://demo.example.test', 'Content-Type': 'application/json'})
        response = connection.getresponse()
        assert response.status == 422
        response.read()
        connection.close()
    finally:
        process.terminate()
        process.communicate(timeout=15)
