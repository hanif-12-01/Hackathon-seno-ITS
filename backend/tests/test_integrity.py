from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from test_api_workflow import (client, clean_database, cleanup_test_file, TestingSessionLocal,
                               get_demo_case_payload, app, test_engine)
from app.models.db_models import AllocationPlan, Reservation, Approval, InventorySnapshot, DistributionCase, AuditLog
from app.services.approval_service import ApprovalService
from app.models.schemas import ApproveRequest
from app.services.optimizer import OptimizationEngine, solve_recovery_plan_v2
from app.services.capacity import operational_day


def make_plan(client, payload=None):
    cid = client.post('/cases', json=payload or get_demo_case_payload()).json()['case_id']
    result = client.post(f'/cases/{cid}/optimize')
    assert result.status_code == 200, result.text
    return cid, result.json()['plan_id']


@pytest.mark.parametrize('mode', ['same', 'different', 'opposite'])
def test_parallel_decisions(client, mode):
    cid, pid = make_plan(client)
    other = make_plan(client)[1] if mode == 'different' else pid
    barrier = Barrier(2)
    def worker(plan, decision):
        with TestClient(app) as c:
            barrier.wait(timeout=10)
            return c.post(f'/plans/{plan}/approve', json={'actor_id':'regression', 'decision':decision}).status_code
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(worker, pid, 'APPROVE')
        second = pool.submit(worker, other, 'REJECT' if mode == 'opposite' else 'APPROVE')
        statuses = sorted([first.result(), second.result()])
    assert statuses == ([200, 200] if mode == 'same' else [200, 409])
    with TestingSessionLocal() as db:
        rows = db.query(Reservation).all()
        assert len(rows) <= 2
        assert sum(r.reserved_stock_kg for r in rows) <= 8000
        assert db.query(Approval).count() == 1
        plan = db.get(AllocationPlan, pid)
        if mode == 'opposite':
            assert bool(rows) == (plan.approval_status == 'APPROVED')
            assert db.query(AuditLog).filter_by(action_type='APPROVAL_CONFLICT').count() == 1


def test_stale_session_retry_and_unique_constraint(client):
    cid, pid = make_plan(client)
    with TestingSessionLocal() as old:
        cached = old.get(AllocationPlan, pid)
        assert cached.approval_status == 'PENDING_APPROVAL'
        assert client.post(f'/plans/{pid}/approve', json={'actor_id':'first'}).status_code == 200
        assert ApprovalService.approve_plan(old, pid, ApproveRequest(actor_id='retry'))['approval_status'] == 'APPROVED'
        row = old.query(Reservation).first()
        old.add(Reservation(id='duplicate', plan_id=pid, case_id=cid, warehouse_id=row.warehouse_id,
            reserved_stock_kg=1, reserved_dispatch_kg=1, operational_date=operational_day()))
        with pytest.raises(IntegrityError):
            old.commit()


def test_invalid_decision_and_no_state_regression(client):
    cid, pid = make_plan(client)
    assert client.post(f'/plans/{pid}/approve', json={'actor_id':'x','decision':'INVALID_DECISION'}).status_code == 422
    with TestingSessionLocal() as db:
        assert db.get(AllocationPlan,pid).approval_status == 'PENDING_APPROVAL'
    assert client.post(f'/plans/{pid}/approve', json={'actor_id':'x'}).status_code == 200
    client.post(f'/cases/{cid}/validate')
    with TestingSessionLocal() as db:
        assert db.get(DistributionCase,cid).status == 'APPROVED'
        assert [s.physical_stock for s in db.query(InventorySnapshot).order_by(InventorySnapshot.warehouse_id)] == [35000,22000]


def test_missing_verification_stale_and_unverified_snapshot(client):
    payload = get_demo_case_payload()
    del payload['is_demand_verified']
    cid = client.post('/cases',json=payload).json()['case_id']
    assert client.post(f'/cases/{cid}/optimize').status_code == 422
    with TestingSessionLocal() as db:
        for snapshot in db.query(InventorySnapshot): snapshot.is_verified=False
        db.commit()
    assert not client.post(f'/cases/{cid}/validate').json()['is_ready']
    payload = get_demo_case_payload()
    payload['warehouses'][0]['snapshot_time'] = (datetime.utcnow()-timedelta(days=3)).isoformat()
    cid = client.post('/cases',json=payload).json()['case_id']
    assert not client.post(f'/cases/{cid}/validate').json()['is_ready']
    assert client.post(f'/cases/{cid}/optimize').status_code == 422


@pytest.mark.parametrize('field', ['verified_demand','physical_stock','daily_dispatch_capacity'])
def test_fractional_kg_rejected(client, field):
    payload=get_demo_case_payload()
    if field=='verified_demand': payload[field]=8000.5
    else: payload['warehouses'][0][field]=1000.5
    assert client.post('/cases',json=payload).status_code == 422


def test_external_reservation_consistency(client):
    payload=get_demo_case_payload(demand=290)
    payload['warehouses'] = [payload['warehouses'][1]]
    payload['warehouses'][0].update(physical_stock=20050,actual_dispatched_today=1950,active_undispatched_reservations=1800)
    cid,pid=make_plan(client,payload)
    gate=client.post(f'/cases/{cid}/validate').json()
    assert gate['safe_capacities']['WH-B']==250
    assert gate['remaining_dispatch_capacities']['WH-B']==250
    plan=client.get(f'/plans/{pid}').json()
    assert (plan['allocated_kg'],plan['unmet_demand_kg'])==(250,40)
    assert client.post(f'/plans/{pid}/approve',json={'actor_id':'x'}).status_code==200


def test_approval_rechecks_latest_authoritative_snapshot(client):
    cid,pid=make_plan(client)
    payload=get_demo_case_payload()
    payload['warehouses'][0]['physical_stock']=25000
    client.post('/cases',json=payload)
    assert client.post(f'/plans/{pid}/approve',json={'actor_id':'x'}).status_code==409
    with TestingSessionLocal() as db:
        assert db.query(Reservation).count()==0


def test_zero_demand_and_zero_capacity_are_valid_data(client):
    for demand, physical in [(0,35000),(8000,0)]:
        payload=get_demo_case_payload(demand=demand)
        for w in payload['warehouses']: w['physical_stock']=physical
        cid,pid=make_plan(client,payload)
        assert client.post(f'/cases/{cid}/validate').json()['is_ready']
        plan=client.get(f'/plans/{pid}').json()
        assert plan['allocated_kg']==0
        assert plan['unmet_demand_kg']==demand


def test_solver_claim_priority_and_no_dispatch():
    wh=[{'id':'B','safe_available':300,'dispatch_available':300,'cost_per_kg':2_000_000.25}]
    result=OptimizationEngine.solve_allocation(290,wh)
    assert result['allocated_kg']==290
    assert result['planned_cost']==290*2_000_000.25
    unknown=OptimizationEngine.solve_allocation(290,wh,max_time_seconds=0)
    assert unknown['solver_status']=='UNKNOWN'
    assert unknown['claim_category']=='NO_OPTIMALITY_CLAIM'
    zero=solve_recovery_plan_v2([dict(wh[0],safe_available=0)],290)
    assert zero['fulfillment_status']=='NO_ALLOCATION'
    assert zero['allocated_kg']==0
    with pytest.raises(ValueError): OptimizationEngine.solve_allocation(1.5,wh)


def test_day_scoping_keeps_stock_but_not_old_dispatch_reservation(client):
    cid,pid=make_plan(client)
    client.post(f'/plans/{pid}/approve',json={'actor_id':'x'})
    with TestingSessionLocal() as db:
        for row in db.query(Reservation): row.operational_date='2020-01-01'
        db.commit()
    cid2=client.post('/cases',json=get_demo_case_payload(demand=5000)).json()['case_id']
    gate=client.post(f'/cases/{cid2}/validate').json()
    assert gate['safe_capacities']['WH-A']==4000
    assert gate['remaining_dispatch_capacities']['WH-A']==6000
    result=client.post(f'/cases/{cid2}/optimize').json()
    assert result['lines'][0]['allocated_kg']==4000


def test_additive_legacy_migration(tmp_path, monkeypatch):
    from app.core import database
    legacy=create_engine('sqlite:///'+str(tmp_path/'legacy.db'))
    with legacy.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE inventory_snapshots (id TEXT, active_stock_reservations FLOAT, active_undispatched_dispatch_reservations_today FLOAT, is_verified BOOLEAN)')
        conn.exec_driver_sql("INSERT INTO inventory_snapshots VALUES ('legacy',1800,1800,1)")
        conn.exec_driver_sql('CREATE TABLE reservations (id TEXT, plan_id TEXT, warehouse_id TEXT, created_at DATETIME)')
        conn.exec_driver_sql("INSERT INTO reservations VALUES ('r','p','w','2026-10-26 18:00:00')")
    monkeypatch.setattr(database,'engine',legacy)
    database.upgrade_integrity_schema()
    database.upgrade_integrity_schema()
    with legacy.connect() as conn:
        assert conn.exec_driver_sql('SELECT is_verified FROM inventory_snapshots').scalar()==0
        assert conn.exec_driver_sql('SELECT operational_date FROM reservations').scalar()=='2026-10-27'
        with pytest.raises(IntegrityError):
            conn.exec_driver_sql("INSERT INTO reservations VALUES ('r2','p','w','2026-10-27','2026-10-27')")
    legacy.dispose()
