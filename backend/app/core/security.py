import base64
import binascii
import hashlib
import hmac
import os
import re
import json
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy.engine import make_url

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse


ITERATIONS = 600000


def password_hash(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITERATIONS)
    return f'pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}'


PREVIEW_HOST_PATTERN = re.compile(
    r"^pangannadi-adaptive(-[a-z0-9]+)?(-git-[a-z0-9-]+)?-clara3\.vercel\.app$"
)


def is_valid_preview_origin(origin: str) -> bool:
    if not origin or not isinstance(origin, str):
        return False
    parts = urlsplit(origin.strip())
    if parts.scheme != "https":
        return False
    if not parts.hostname or parts.port or parts.username or parts.password:
        return False
    if parts.path or parts.query or parts.fragment:
        return False
    if "*" in origin:
        return False
    return bool(PREVIEW_HOST_PATTERN.match(parts.hostname.lower()))


def get_vercel_system_origins() -> list[str]:
    system_origins = []
    for key in ("VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
        raw = os.environ.get(key, "").strip().strip("'\"")
        if not raw:
            continue
        host = raw.removeprefix("https://").removeprefix("http://").split("/")[0].split("?")[0].lower()
        if not host:
            continue
        candidate = f"https://{host}"
        if host == "pangannadi-adaptive.vercel.app" or is_valid_preview_origin(candidate):
            if candidate not in system_origins:
                system_origins.append(candidate)
    return system_origins


def hosted_mode():
    raw_hosted = os.environ.get('PANGANNADI_HOSTED', '')
    cleaned_hosted = raw_hosted.strip().strip('\'"')
    return os.environ.get('VERCEL') == '1' or cleaned_hosted == '1'


def audit_actor_mode():
    return "SIMULATION_OPERATOR_AUTHENTICATED" if hosted_mode() else "SIMULATION_UNAUTHENTICATED"


def trusted_origins(production):
    value = os.environ.get('PANGANNADI_CORS_ORIGINS', '' if production else 'http://localhost:5173,http://127.0.0.1:5173')
    raw_origins = [origin.strip().strip('\'"').rstrip('/') for origin in value.split(',') if origin.strip()]
    origins = [o for o in raw_origins if o]
    if hosted_mode():
        for sys_origin in get_vercel_system_origins():
            if sys_origin not in origins:
                origins.append(sys_origin)
    if production and (not origins or any(
        urlsplit(origin).scheme != 'https' or not urlsplit(origin).hostname
        or urlsplit(origin).username or urlsplit(origin).password
        or urlsplit(origin).path or urlsplit(origin).query or urlsplit(origin).fragment
        or '*' in origin for origin in origins
    )):
        raise ValueError('Production requires explicit HTTPS PANGANNADI_CORS_ORIGINS; CORS is not authentication')
    return origins


def validate_hosted_database():
    try:
        url = make_url(os.environ.get('PANGANNADI_DATABASE_URL', ''))
        valid = (os.environ.get('PANGANNADI_ENV') == 'production'
                 and url.drivername == 'postgresql+psycopg' and bool(url.host) and bool(url.database)
                 and url.query.get('sslmode') in ('require', 'verify-ca', 'verify-full'))
    except Exception:
        valid = False
    if not valid:
        raise ValueError('Hosted mode requires production and explicit PostgreSQL+psycopg URL with TLS; no SQLite fallback')


def bind_actor(request, payload, field):
    principal = getattr(request.state, 'principal', None)
    if principal is not None:
        setattr(payload, field, principal)
    elif hosted_mode():
        raise HTTPException(401, 'Authentication required')
    return payload


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate account configuration key')
        result[key] = value
    return result


class OperatorPerimeter:
    PUBLIC_READ = frozenset({('GET', '/health')})

    def __init__(self, app, origins, admission=None):
        self.app = app
        self.origins = origins
        self.admission = admission
        self.accounts = {}
        try:
            accounts = json.loads(os.environ.get('PANGANNADI_OPERATOR_ACCOUNTS', ''), object_pairs_hook=unique_object)
            if not isinstance(accounts, dict) or not accounts or not origins:
                raise ValueError()
            for username, account in accounts.items():
                if (not isinstance(username, str) or not 1 <= len(username) <= 100
                        or any(ord(char) < 33 or ord(char) > 126 or char == ':' for char in username)
                        or not isinstance(account, dict) or set(account) != {'password_hash', 'role'}
                        or account['role'] not in ('public', 'operator')):
                    raise ValueError()
                algorithm, iterations, salt, digest = account['password_hash'].split('$')
                salt, digest = bytes.fromhex(salt), bytes.fromhex(digest)
                if algorithm != 'pbkdf2_sha256' or iterations != str(ITERATIONS) or len(salt) != 16 or len(digest) != 32:
                    raise ValueError()
                self.accounts[username] = (salt, digest, account['role'])
            if not any(role == 'operator' for _, _, role in self.accounts.values()):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Hosted mode requires valid individual PANGANNADI_OPERATOR_ACCOUNTS JSON with password_hash and explicit public/operator role') from None
        self.dummy_salt = os.urandom(16)

    def verify(self, value):
        try:
            scheme, token = value.split(' ', 1)
            if scheme.lower() != 'basic' or len(token) > 4096:
                return None
            username, password = base64.b64decode(token, validate=True).decode('utf-8').split(':', 1)
        except (ValueError, UnicodeError, binascii.Error):
            return None
        salt, digest, role = self.accounts.get(username, (self.dummy_salt, bytes(32), 'public'))
        candidate = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITERATIONS)
        if hmac.compare_digest(candidate, digest) and username in self.accounts:
            return username, role
        return None

    def is_origin_trusted(self, origin: str) -> bool:
        if origin in self.origins:
            return True
        if hosted_mode() and os.environ.get("VERCEL_ENV") != "production":
            return is_valid_preview_origin(origin)
        return False

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        response = None
        path = scope['path'].removeprefix(scope.get('root_path', ''))
        if (scope['method'], path) in self.PUBLIC_READ:
            response = JSONResponse({'status': 'ok'})
        else:
            headers = scope.get('headers', [])
            authorization = [v for k, v in headers if k.lower() == b'authorization']
            value = authorization[0].decode('latin-1') if len(authorization) == 1 else ''
            username = None
            try:
                scheme, token = value.split(' ', 1)
                if scheme.lower() == 'basic' and len(token) <= 4096:
                    candidate = base64.b64decode(token, validate=True).decode('utf-8').split(':', 1)[0]
                    if candidate in self.accounts:
                        username = candidate
            except (ValueError, UnicodeError, binascii.Error):
                pass
            try:
                if self.admission is None:
                    from app.core.rate_limit import DatabaseAdmission
                    self.admission = DatabaseAdmission()
                allowed, retry = await run_in_threadpool(self.admission.consume, username)
            except Exception:
                allowed = False
                response = JSONResponse({'detail': 'Authentication temporarily unavailable'}, status_code=503,
                                        headers={'Retry-After': '60'})
            if not allowed and response is None:
                response = JSONResponse({'detail': 'Too many authentication attempts'}, status_code=429,
                                        headers={'Retry-After': str(retry)})
            principal = await run_in_threadpool(self.verify, value) if allowed else None
            if allowed and principal is None:
                try:
                    await run_in_threadpool(self.admission.record_failure, username)
                except Exception:
                    response = JSONResponse({'detail': 'Authentication temporarily unavailable'}, status_code=503,
                                            headers={'Retry-After': '60'})
            if response is not None:
                pass
            elif principal is None:
                response = JSONResponse({'detail': 'Authentication required'}, status_code=401,
                                        headers={} if (b'x-pangannadi-ui', b'1') in headers else {'WWW-Authenticate': 'Basic realm="PanganNadi operators", charset="UTF-8"'})
            elif principal[1] != 'operator' and (scope['method'], path) != ('GET', '/session'):
                response = JSONResponse({'detail': 'Operator role required'}, status_code=403)
            elif scope['method'] not in ('GET', 'HEAD', 'OPTIONS'):
                origins = [v.decode('latin-1') for k, v in headers if k.lower() == b'origin']
                if len(origins) != 1 or not self.is_origin_trusted(origins[0]):
                    response = JSONResponse({'detail': 'Explicit trusted Origin required'}, status_code=403)
            if principal is not None and response is None:
                scope.setdefault('state', {})['principal'] = principal[0]
                scope['state']['role'] = principal[1]

        async def private_send(message):
            if message['type'] == 'http.response.start':
                message['headers'] = [(k, v) for k, v in message.get('headers', [])
                                      if k.lower() not in (b'cache-control', b'pragma')]
                message['headers'] += [(b'cache-control', b'no-store'), (b'pragma', b'no-cache')]
            await send(message)

        await (response or self.app)(scope, receive, private_send)


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
