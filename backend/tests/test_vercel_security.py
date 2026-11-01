import base64
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Mount
from app.core.security import OperatorPerimeter, bind_actor, password_hash, validate_hosted_database, audit_actor_mode
from pydantic import BaseModel

ORIGIN = 'https://stage.example.test'


def auth(username='alice', password='test-password'):
    return 'Basic ' + base64.b64encode(f'{username}:{password}'.encode()).decode()


class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.accounts = json.dumps({
            'alice': {'password_hash': password_hash('test-password'), 'role': 'operator'},
            'bob': {'password_hash': password_hash('bob-password'), 'role': 'operator'},
            'visitor': {'password_hash': password_hash('test-password'), 'role': 'public'},
        })

    def setUp(self):
        self.env = patch.dict(os.environ, {'PANGANNADI_OPERATOR_ACCOUNTS': self.accounts})
        self.env.start()
        self.addCleanup(self.env.stop)
        from sqlalchemy import create_engine
        from app.core.rate_limit import DatabaseAdmission, admission_buckets
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        engine = create_engine('sqlite:///' + (Path(directory.name) / 'admission.sqlite3').as_posix())
        self.addCleanup(engine.dispose)
        admission_buckets.create(engine)
        self.admission = DatabaseAdmission(engine)
        backend = FastAPI()

        class Payload(BaseModel):
            actor_id: str

        @backend.post('/probe')
        def probe(payload: Payload, request: Request):
            bind_actor(request, payload, 'actor_id')
            return payload.model_dump()

        @backend.get('/session')
        def session(request: Request):
            return {'principal': request.state.principal, 'role': request.state.role}

        @backend.get('/cases')
        def cases():
            return {'private': True}

        backend.add_middleware(OperatorPerimeter, origins=[ORIGIN], admission=self.admission)
        self.client = TestClient(Starlette(routes=[Mount('/api', app=backend)]))
        self.addCleanup(self.client.close)

    def test_mount_public_health_only(self):
        response = self.client.get('/api/health')
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertEqual(response.headers['cache-control'], 'no-store')
        for path in ('/api/cases', '/api/audit', '/api/forecasting/status', '/api/docs', '/api/openapi.json', '/api/ready', '/api/missing'):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        for method in ('HEAD', 'POST', 'OPTIONS'):
            self.assertEqual(self.client.request(method, '/api/health').status_code, 401)
        self.assertEqual(self.client.get('/health').status_code, 404)
        self.assertEqual(self.client.get('/api/cases', headers={'Authorization': auth()}).json(), {'private': True})

    def test_individual_accounts_roles_and_duplicate_headers(self):
        for value in ('Basic !!!', 'Bearer abc', auth(password='wrong'), auth(username='missing'), 'Basic ' + 'x' * 5000):
            response = self.client.get('/api/cases', headers={'Authorization': value})
            self.assertEqual(response.status_code, 401)
            self.assertIn('Basic', response.headers['www-authenticate'])
        self.assertEqual(self.client.get('/api/cases', headers={'Authorization': auth('visitor')}).status_code, 403)
        self.assertEqual(self.client.get('/api/cases', headers=[('Authorization', auth()), ('Authorization', auth())]).status_code, 401)
        for username, password in (('alice', 'test-password'), ('bob', 'bob-password')):
            response = self.client.post('/api/probe', json={'actor_id': 'spoofed'}, headers={'Authorization': auth(username, password), 'Origin': ORIGIN})
            self.assertEqual(response.json(), {'actor_id': username})
            self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_valid_workflow_fanout_and_failure_lock(self):
        for _ in range(11):
            self.assertEqual(self.client.get('/api/cases', headers={'Authorization': auth()}).status_code, 200)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.client.get('/api/cases', headers={'Authorization': auth()}).status_code, range(60)))
        self.assertEqual(results, [200] * 60)
        for _ in range(10):
            self.assertEqual(self.client.get('/api/cases', headers={'Authorization': auth(password='wrong')}).status_code, 401)
        with patch.object(OperatorPerimeter, 'verify', side_effect=AssertionError('blocked before hashing')):
            response = self.client.get('/api/cases', headers={'Authorization': auth()})
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response.headers)
        self.assertEqual(self.client.get('/api/cases', headers={'Authorization': auth('bob', 'bob-password')}).status_code, 200)

    def test_session_and_ui_challenge(self):
        response = self.client.get('/api/session', headers={'X-PanganNadi-UI': '1'})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('www-authenticate', response.headers)
        for username, password, role in (('alice', 'test-password', 'operator'), ('visitor', 'test-password', 'public')):
            response = self.client.get('/api/session', headers={'Authorization': auth(username, password)})
            self.assertEqual(response.json(), {'principal': username, 'role': role})
            self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_exact_origin(self):
        for origin in (None, 'null', ORIGIN + '/', ORIGIN + '.evil', 'https://evil.test'):
            headers = {'Authorization': auth()}
            if origin is not None:
                headers['Origin'] = origin
            self.assertEqual(self.client.post('/api/probe', json={'actor_id': 'fake'}, headers=headers).status_code, 403)
        self.assertEqual(self.client.post('/api/probe', json={'actor_id': 'fake'}, headers=[('Authorization', auth()), ('Origin', ORIGIN), ('Origin', ORIGIN)]).status_code, 403)
        self.assertEqual(self.client.options('/api/cases', headers={'Origin': ORIGIN, 'Access-Control-Request-Method': 'POST'}).status_code, 401)

    def test_actor_binding_and_audit_mode_attribution(self):
        headers = {'Authorization': auth('alice', 'test-password'), 'Origin': ORIGIN}
        res = self.client.post('/api/probe', json={'actor_id': 'malicious_spoofed_user'}, headers=headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {'actor_id': 'alice'})

        headers_bob = {'Authorization': auth('bob', 'bob-password'), 'Origin': ORIGIN}
        res_bob = self.client.post('/api/probe', json={'actor_id': 'attacker'}, headers=headers_bob)
        self.assertEqual(res_bob.status_code, 200)
        self.assertEqual(res_bob.json(), {'actor_id': 'bob'})

        with patch.dict(os.environ, {'VERCEL': '1'}):
            self.assertEqual(audit_actor_mode(), 'SIMULATION_OPERATOR_AUTHENTICATED')
        with patch.dict(os.environ, {'VERCEL': '0', 'PANGANNADI_HOSTED': '0'}):
            self.assertEqual(audit_actor_mode(), 'SIMULATION_UNAUTHENTICATED')

    def test_invalid_account_config_no_shared_fallback(self):
        for value in ('', '{}', '[]', '{"alice":{},"alice":{}}', json.dumps({'alice': {'password_hash': 'plain', 'role': 'operator'}}), self.accounts.replace('operator', 'reader')):
            with patch.dict(os.environ, {'PANGANNADI_OPERATOR_ACCOUNTS': value, 'PANGANNADI_OPERATOR_USERNAME': 'shared', 'PANGANNADI_OPERATOR_PASSWORD_HASH': password_hash('shared')}):
                with self.assertRaises(ValueError):
                    OperatorPerimeter(None, [ORIGIN])

    def test_hosted_database_rejects_fallback(self):
        for url in ('', 'sqlite:///local.db', 'postgresql://u:p@db/app?sslmode=require', 'postgresql+psycopg://u:p@db/app'):
            with patch.dict(os.environ, {'PANGANNADI_ENV': 'production', 'PANGANNADI_DATABASE_URL': url}):
                with self.assertRaises(ValueError):
                    validate_hosted_database()
        with patch.dict(os.environ, {'PANGANNADI_ENV': 'production', 'PANGANNADI_DATABASE_URL': 'postgresql+psycopg://u:p@db/app?sslmode=require'}):
            validate_hosted_database()

    def test_startup_security_precedes_database_import(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'untouched.sqlite3'
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PANGANNADI_HOSTED='1', PANGANNADI_ENV='production', PANGANNADI_CORS_ORIGINS=ORIGIN, PANGANNADI_OPERATOR_ACCOUNTS='', PANGANNADI_DATABASE_URL='sqlite:///' + database.as_posix())
            result = subprocess.run([sys.executable, '-B', '-c', 'import sys; from pathlib import Path; sys.path.insert(0, str(Path.cwd())); import app.main'], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('individual PANGANNADI_OPERATOR_ACCOUNTS', result.stderr)
            self.assertFalse(database.exists())
            env['PANGANNADI_OPERATOR_ACCOUNTS'] = self.accounts
            result = subprocess.run([sys.executable, '-B', '-c', 'import app.main'], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('no SQLite fallback', result.stderr)
            self.assertFalse(database.exists())

    def test_vercel_entrypoint_and_dependency_contract(self):
        import tomllib
        from types import SimpleNamespace
        root = Path(__file__).resolve().parents[2]
        config = json.loads((root / 'vercel.json').read_text())
        exclusions = config['services']['backend']['functions']['app/vercel.py']['excludeFiles']
        for pattern in ('private_assets/**', 'backend/private_assets/**', 'data/**', 'evidence/**', '.venv/**'):
            self.assertIn(pattern, exclusions)
        self.assertEqual(config['services']['backend']['entrypoint'], 'app.vercel:app')
        self.assertEqual(config['rewrites'][1], {'source': '/api/(.*)', 'destination': {'service': 'backend'}})
        project = tomllib.loads((root / 'backend' / 'pyproject.toml').read_text())
        self.assertEqual(project['project']['requires-python'], '==3.12.*')
        pins = (root / 'backend' / 'requirements-vercel.txt').read_text().splitlines()
        dependencies = project['project']['dependencies'] + project['tool']['uv']['constraint-dependencies']
        self.assertEqual(set(pins), set(dependencies))
        self.assertNotIn('xgboost', {pin.split('==')[0] for pin in dependencies})
        backend = FastAPI()
        backend.add_middleware(OperatorPerimeter, origins=[ORIGIN], admission=self.admission)
        with patch.dict(sys.modules, {'app.main': SimpleNamespace(app=backend)}), patch.dict(os.environ):
            sys.modules.pop('app.vercel', None)
            entrypoint = importlib.import_module('app.vercel')
            self.assertEqual(os.environ['PANGANNADI_HOSTED'], '1')
            with TestClient(entrypoint.app) as client:
                self.assertEqual(client.get('/api/health').json(), {'status': 'ok'})
                self.assertEqual(client.get('/api/cases').status_code, 401)
                self.assertEqual(client.get('/health').status_code, 404)
            sys.modules.pop('app.vercel', None)

    def test_authenticated_second_operator_validates_and_initiates_solver(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.api.routes import router
        from app.core.database import Base, get_db
        from app.models.db_models import AuditLog, DistributionCase

        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine('sqlite:///' + (Path(directory) / 'actors.sqlite3').as_posix(), connect_args={'check_same_thread': False})
            sessions = sessionmaker(bind=engine, autoflush=False)
            Base.metadata.create_all(engine)
            def session():
                with sessions() as db:
                    yield db
            backend = FastAPI()
            backend.include_router(router)
            backend.dependency_overrides[get_db] = session
            backend.add_middleware(OperatorPerimeter, origins=[ORIGIN], admission=self.admission)
            try:
                with patch.dict(os.environ, {'PANGANNADI_HOSTED': '1'}), TestClient(backend) as client:
                    payload = {'destination_region': 'test', 'verified_demand': 10, 'is_demand_verified': True,
                        'created_by': 'spoofed', 'warehouses': [{'warehouse_id': 'WH-TEST', 'physical_stock': 100,
                        'local_demand': 0, 'safety_stock': 0, 'daily_dispatch_capacity': 100, 'cost_per_kg': 1}]}
                    response = client.post('/cases', json=payload, headers={'Authorization': auth(), 'Origin': ORIGIN})
                    self.assertEqual(response.status_code, 201, response.text)
                    case_id = response.json()['case_id']
                    headers = {'Authorization': auth('bob', 'bob-password'), 'Origin': ORIGIN}
                    response = client.post(f'/cases/{case_id}/validate', headers=headers)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertTrue(response.json()['is_ready'])
                    response = client.post(f'/cases/{case_id}/optimize', headers=headers)
                    self.assertEqual(response.status_code, 200, response.text)
                    with sessions() as db:
                        self.assertEqual(db.get(DistributionCase, case_id).created_by, 'alice')
                        validation = db.query(AuditLog).filter_by(case_id=case_id, action_type='GATE_VALIDATED').one()
                        self.assertEqual(validation.actor_id, 'bob')
                        optimization = db.query(AuditLog).filter_by(case_id=case_id, action_type='OPTIMIZATION_EXECUTED').one()
                        self.assertEqual(optimization.actor_id, 'system-solver-cp-sat')
                        self.assertEqual(json.loads(optimization.details)['initiator_id'], 'bob')
            finally:
                engine.dispose()

    def test_actual_routes_bind_each_actor_field(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'VERCEL': '0', 'PANGANNADI_HOSTED': '0', 'PANGANNADI_ENV': 'test', 'PANGANNADI_DATABASE_URL': 'sqlite:///' + (Path(directory) / 'test.sqlite3').as_posix(), 'PANGANNADI_OPERATOR_USERNAME': '', 'PANGANNADI_OPERATOR_PASSWORD_HASH': ''}):
                from app.api import routes
                from app.core import database
                from app.models import schemas
                from types import SimpleNamespace
                request = SimpleNamespace(state=SimpleNamespace(principal='alice'))
                cases = (
                    (routes.create_case, schemas.CaseCreateRequest, 'created_by', 'CaseService.create_case'),
                    (routes.approve_plan, schemas.ApproveRequest, 'actor_id', 'ApprovalService.approve_plan'),
                    (routes.dispatch_shipment, schemas.DispatchRequest, 'actor_id', 'ShipmentService.execute'),
                    (routes.cancel_shipment_reservation, schemas.CancellationRequest, 'actor_id', 'ShipmentService.execute'),
                    (routes.receive_case, schemas.ReceiptRequest, 'inspector_id', 'ReceivingService.execute'),
                    (routes.reconcile_case, schemas.ReconcileRequest, 'actor_id', 'ReceivingService.execute'),
                    (routes.create_checkpoint, schemas.CheckpointRequest, 'actor_id', 'record_checkpoint'),
                    (routes.replan_case, schemas.ReplanRequest, 'actor_id', 'ReplanningService.generate'),
                )
                for endpoint, model, field, target in cases:
                    payload = model.model_construct(**{field: 'spoofed'})
                    with patch('app.api.routes.' + target, return_value=SimpleNamespace(id='test')):
                        kwargs = {'req': payload, 'request': request, 'db': None}
                        if endpoint is not routes.create_case:
                            kwargs['id'] = 'test'
                        endpoint(**kwargs)
                    self.assertEqual(getattr(payload, field), 'alice', endpoint.__name__)
                with patch.dict(os.environ, {'PANGANNADI_HOSTED': '1'}):
                    from fastapi import HTTPException
                    for endpoint, target in ((routes.validate_case, 'validate_gate'), (routes.optimize_case, 'optimize_case')):
                        with patch('app.api.routes.CaseService.' + target, return_value={}) as service:
                            self.assertEqual(endpoint('test', request=request, db=None), {})
                            service.assert_called_once_with(db=None, case_id='test', actor_id='alice')
                        with self.assertRaises(HTTPException) as denied:
                            endpoint('test', request=SimpleNamespace(state=SimpleNamespace()), db=None)
                        self.assertEqual(denied.exception.status_code, 401)
                with patch('app.core.database.initialize_database') as initialize:
                    if 'app.main' in sys.modules:
                        importlib.reload(sys.modules['app.main'])
                    else:
                        importlib.import_module('app.main')
                    initialize.assert_called_once_with()
                database.engine.dispose()


if __name__ == '__main__':
    unittest.main()
