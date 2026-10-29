import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker
from app.core.database import Base, get_db
from app.main import app
from app.models.db_models import Shipment, AllocationPlan, DistributionCase, ShipmentCheckpoint
from app.models.schemas import CheckpointRequest
from app.services.shipment_service import record_checkpoint, ShipmentConflict
from app.services.audit_service import AuditService


@pytest.fixture
def local(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'checkpoint.sqlite3').as_posix(), connect_args={'check_same_thread': False, 'timeout': 10})
    @event.listens_for(engine, 'connect')
    def pragmas(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    previous = app.dependency_overrides.copy()
    def override():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = override
    with TestClient(app) as client:
        body = dict(destination_region='Checkpoint simulation', verified_demand=100, is_demand_verified=True, created_by='simulation', warehouses=[dict(warehouse_id='P1', physical_stock=1000, local_demand=0, safety_stock=0, daily_dispatch_capacity=1000, cost_per_kg=1)])
        cid = client.post('/cases', json=body).json()['case_id']
        pid = client.post(f'/cases/{cid}/optimize').json()['plan_id']
        assert client.post(f'/plans/{pid}/approve', json={'actor_id': 'simulation'}).status_code == 200
        sid = client.get(f'/plans/{pid}').json()['shipments'][0]['shipment_id']
        yield client, sessions, engine, cid, pid, sid
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    engine.dispose()


def body(key='checkpoint', **changes):
    return dict(actor_id='simulation', transaction_ref=key, event_type='IN_TRANSIT', note=None, **changes)


def dispatch(client, sid):
    result = client.post(f'/shipments/{sid}/dispatch', json=dict(actor_id='simulation', transaction_ref='departure', planned_quantity_kg=100, actual_dispatched_quantity_kg=100))
    assert result.status_code == 200, result.text


def snapshot(engine, exclude=()):
    with engine.connect() as conn:
        return {name: [tuple(row) for row in conn.execute(text(f'SELECT * FROM "{name}" ORDER BY 1'))] for name in inspect(conn).get_table_names() if name not in exclude}


def test_checkpoint_history_replay_proof_and_no_business_effect(local):
    client, sessions, engine, cid, pid, sid = local
    url = f'/shipments/{sid}/checkpoints'
    assert client.get(url).json() == []
    assert client.post(url, json=body()).status_code == 409
    dispatch(client, sid)
    before = snapshot(engine, ('shipment_checkpoints', 'audit_logs'))
    results = []
    for index, kind in enumerate(('IN_TRANSIT', 'AT_CHECKPOINT', 'DELAY_REPORTED')):
        payload = body(str(index)); payload.update(event_type=kind, note='Manual observation')
        response = client.post(url, json=payload)
        assert response.status_code == 201, response.text
        result = response.json(); results.append(result)
        assert result['timestamp'].endswith('+00:00')
        assert result['actor_mode'] == 'SIMULATION_UNAUTHENTICATED'
        assert client.post(url, json=payload).json() == result
        assert client.get('/operations/' + str(index)).json() == dict(endpoint=url, payload=payload, result=result)
        assert client.post(url, json=dict(payload, note='changed')).status_code == 409
    assert client.get(url).json() == results
    assert [r['checkpoint_id'] for r in results] == sorted(r['checkpoint_id'] for r in results)
    assert snapshot(engine, ('shipment_checkpoints', 'audit_logs')) == before
    with engine.connect() as conn:
        assert conn.exec_driver_sql('PRAGMA foreign_key_check').all() == []
        assert conn.exec_driver_sql('PRAGMA integrity_check').scalar() == 'ok'
        assert conn.exec_driver_sql("SELECT count(*) FROM audit_logs WHERE action_type='SHIPMENT_CHECKPOINT_RECORDED'").scalar() == 3
    env = os.environ.copy(); env['PANGANNADI_DATABASE_URL'] = str(engine.url)
    code = 'from fastapi.testclient import TestClient; from app.main import app; import json; c=TestClient(app); print(json.dumps(c.get(' + repr(url) + ').json()))'
    before_restart = snapshot(engine)
    for _ in range(2):
        result = subprocess.run([sys.executable, '-B', '-c', code], env=env, capture_output=True, text=True, check=True)
        assert json.loads(result.stdout) == results
    assert snapshot(engine) == before_restart


@pytest.mark.parametrize('changes', [dict(event_type='ARRIVED'), dict(event_type='GPS'), dict(note='x'*2001), dict(actor_id=' '), dict(actor_id='x'*101), dict(transaction_ref=' '), dict(transaction_ref='x'*101), dict(latitude=1), dict(event_type=None)])
def test_checkpoint_schema_422(local, changes):
    client, _, engine, _, _, sid = local
    before = snapshot(engine)
    assert client.post(f'/shipments/{sid}/checkpoints', json={**body(), **changes}).status_code == 422
    assert snapshot(engine) == before


@pytest.mark.parametrize('mode', ['CLOSED', 'READY_TO_SHIP', 'CANCELLED', 'zero', 'timestamp', 'uncommitted', 'recovery', 'RECEIVED', 'REPLAN_AVAILABLE', 'RECOVERY_RESERVED'])
def test_checkpoint_state_409(local, mode):
    client, sessions, engine, cid, pid, sid = local
    dispatch(client, sid)
    with sessions() as db:
        shipment = db.get(Shipment, sid)
        if mode in ('CLOSED', 'READY_TO_SHIP', 'CANCELLED'): shipment.manifest_status = mode
        elif mode == 'zero': shipment.actual_dispatched_quantity_kg = 0
        elif mode == 'timestamp': shipment.dispatch_timestamp = None
        elif mode == 'uncommitted': shipment.transaction_ref = 'missing-event'
        elif mode == 'recovery': db.get(AllocationPlan, pid).is_replanning = True
        else: db.get(DistributionCase, cid).status = mode
        db.commit()
    before = snapshot(engine)
    assert client.post(f'/shipments/{sid}/checkpoints', json=body()).status_code == 409
    assert snapshot(engine) == before


def test_checkpoint_missing_and_cross_shipment_conflict(local):
    client, _, _, _, _, sid = local
    assert client.get('/shipments/missing/checkpoints').status_code == 404
    assert client.post('/shipments/missing/checkpoints', json=body()).status_code == 404
    dispatch(client, sid)
    assert client.post(f'/shipments/{sid}/checkpoints', json=body()).status_code == 201
    assert client.post('/shipments/missing/checkpoints', json=body()).status_code == 409
    assert client.post(f'/shipments/{sid}/checkpoints', json=body('departure')).status_code == 409


@pytest.mark.parametrize('different', [False, True])
def test_checkpoint_concurrent_duplicate(local, different):
    client, sessions, engine, _, _, sid = local
    dispatch(client, sid)
    barrier = Barrier(2)
    def worker(index):
        payload = body(); payload['note'] = str(index) if different else None
        with sessions() as db:
            barrier.wait(timeout=10)
            try: return 201, record_checkpoint(db, sid, CheckpointRequest(**payload))
            except ShipmentConflict: return 409, None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(worker, [0, 1]))
    assert sorted(r[0] for r in results) == ([201, 409] if different else [201, 201])
    if not different: assert results[0][1] == results[1][1]
    with sessions() as db: assert db.query(ShipmentCheckpoint).count() == 1


def test_checkpoint_audit_failure_atomic(local, monkeypatch):
    client, sessions, engine, _, _, sid = local
    dispatch(client, sid)
    before = snapshot(engine)
    def fail(*args, **kwargs): raise RuntimeError('injected audit failure')
    monkeypatch.setattr(AuditService, 'record_log', fail)
    with sessions() as db, pytest.raises(RuntimeError, match='injected'):
        record_checkpoint(db, sid, CheckpointRequest(**body()))
    assert snapshot(engine) == before


def test_checkpoint_global_namespace_and_closed_replay(local):
    client, _, engine, cid, pid, sid = local
    dispatch(client, sid)
    url = f'/shipments/{sid}/checkpoints'
    saved = client.post(url, json=body()).json()
    assert client.post(f'/shipments/{sid}/dispatch', json=dict(actor_id='simulation', transaction_ref='checkpoint', planned_quantity_kg=100, actual_dispatched_quantity_kg=100)).status_code == 409
    receipt = dict(inspector_id='simulation', transaction_ref='checkpoint', plan_id=pid, shipment_ids=[sid], total_dispatched_kg=100, arrived_weight_kg=90, rejected_weight_kg=0)
    assert client.post(f'/cases/{cid}/receipts', json=receipt).status_code == 409
    receipt['transaction_ref'] = 'receipt'
    recorded = client.post(f'/cases/{cid}/receipts', json=receipt)
    assert recorded.status_code == 201, recorded.text
    final = dict(actor_id='simulation', transaction_ref='final', receipt_id=recorded.json()['receipt_id'])
    finalized = client.post(f'/cases/{cid}/reconcile', json=final)
    assert finalized.status_code == 200, finalized.text
    replan = dict(actor_id='simulation', transaction_ref='checkpoint', source_reconciliation_id=finalized.json()['reconciliation_id'])
    assert client.post(f'/cases/{cid}/replan', json=replan).status_code == 409
    replan['transaction_ref'] = 'recovery'
    assert client.post(f'/cases/{cid}/replan', json=replan).status_code == 200
    before = snapshot(engine)
    for key in ('departure', 'receipt', 'final', 'recovery'):
        assert client.post(url, json=body(key)).status_code == 409
    assert client.post(url, json=body()).json() == saved
    assert client.post(url, json=body('new')).status_code == 409
    assert snapshot(engine) == before


def test_checkpoint_migration_failure_rolls_back(local, monkeypatch):
    from app.core import database
    _, _, engine, _, _, _ = local
    ShipmentCheckpoint.__table__.drop(engine)
    before = snapshot(engine)
    monkeypatch.setattr(database, 'engine', engine)
    def fail(conn, *args, **kwargs):
        conn.exec_driver_sql('CREATE TABLE checkpoint_failure_probe (id INTEGER)')
        raise RuntimeError('injected migration failure')
    monkeypatch.setattr(ShipmentCheckpoint.__table__, 'create', fail)
    with pytest.raises(RuntimeError, match='injected'):
        database.upgrade_checkpoint_schema()
    assert snapshot(engine) == before


def test_checkpoint_concurrent_ordering(local):
    client, sessions, _, _, _, sid = local
    dispatch(client, sid)
    barrier = Barrier(2)
    def worker(index):
        with sessions() as db:
            barrier.wait(timeout=10)
            return record_checkpoint(db, sid, CheckpointRequest(**body('ordered-' + str(index))))
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(worker, [0, 1]))
    rows = client.get(f'/shipments/{sid}/checkpoints').json()
    assert rows == sorted(results, key=lambda r:r['checkpoint_id'])
    assert len(rows) == 2


def test_checkpoint_migration_additive(local, monkeypatch):
    from app.core import database
    client, _, engine, _, _, sid = local
    before = snapshot(engine)
    ShipmentCheckpoint.__table__.drop(engine)
    monkeypatch.setattr(database, 'engine', engine)
    for _ in range(3): database.upgrade_checkpoint_schema()
    assert snapshot(engine) == before
