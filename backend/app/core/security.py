import base64
import binascii
import hashlib
import hmac
import os

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse


ITERATIONS = 600000


def password_hash(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITERATIONS)
    return f'pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}'


class DemoPerimeter:
    def __init__(self, app, origins, production=False):
        self.app = app
        self.origins = origins
        self.username = os.environ.get('PANGANNADI_OPERATOR_USERNAME', '')
        encoded = os.environ.get('PANGANNADI_OPERATOR_PASSWORD_HASH', '')
        self.enabled = production or bool(self.username or encoded)
        if not self.enabled:
            return
        try:
            algorithm, iterations, salt, digest = encoded.split('$')
            self.salt = bytes.fromhex(salt)
            self.digest = bytes.fromhex(digest)
            if (algorithm != 'pbkdf2_sha256' or iterations != str(ITERATIONS)
                    or len(self.salt) != 16 or len(self.digest) != 32
                    or not self.username or len(self.username) > 128
                    or not self.username.isascii() or ':' in self.username
                    or any(ord(char) < 33 or ord(char) > 126 for char in self.username)
                    or not origins):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Demo perimeter requires valid PANGANNADI_OPERATOR_USERNAME, PANGANNADI_OPERATOR_PASSWORD_HASH and explicit origins') from None

    def verify(self, value):
        try:
            scheme, token = value.split(' ', 1)
            if scheme.lower() != 'basic' or len(token) > 4096:
                return False
            username, password = base64.b64decode(token, validate=True).decode('utf-8').split(':', 1)
        except (ValueError, UnicodeError, binascii.Error):
            return False
        candidate = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), self.salt, ITERATIONS)
        return hmac.compare_digest(username.encode('utf-8'), self.username.encode('ascii')) & hmac.compare_digest(candidate, self.digest)

    async def __call__(self, scope, receive, send):
        if not self.enabled or scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        headers = scope.get('headers', [])
        authorization = [value for key, value in headers if key.lower() == b'authorization']
        authenticated = len(authorization) == 1 and await run_in_threadpool(self.verify, authorization[0].decode('latin-1'))
        response = None
        if not authenticated:
            response = JSONResponse({'detail': 'Authentication required'}, status_code=401,
                                    headers={'WWW-Authenticate': 'Basic realm="PanganNadi demo", charset="UTF-8"'})
        elif scope['method'] not in ('GET', 'HEAD', 'OPTIONS'):
            origins = [value.decode('latin-1') for key, value in headers if key.lower() == b'origin']
            if len(origins) != 1 or origins[0] not in self.origins:
                response = JSONResponse({'detail': 'Explicit trusted Origin required'}, status_code=403)

        async def private_send(message):
            if message['type'] == 'http.response.start':
                message['headers'] = [(key, value) for key, value in message.get('headers', [])
                                      if key.lower() not in (b'cache-control', b'pragma')]
                message['headers'] += [(b'cache-control', b'no-store'), (b'pragma', b'no-cache')]
            await send(message)

        await (response or self.app)(scope, receive, private_send)
