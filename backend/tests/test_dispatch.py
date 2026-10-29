import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from test_api_workflow import (client, clean_database, cleanup_test_file, TestingSessionLocal,
    get_demo_case_payload, app, test_engine)
from app.models.db_models import (Shipment, ShipmentEvent, DispatchUsage, InventorySnapshot,
    Reservation, AllocationPlan, DistributionCase, AuditLog)
from app.models.schemas import DispatchRequest
from app.services.shipment_service import ShipmentService
from app.services.audit_service import AuditService
from app.services.capacity import operational_day


def approved(client):
    cid = client.post('/cases', json=get_demo_case_payload()).json()['case_id']
    pid = client.post(f'/cases/{cid}/optimize').json()['plan_id']
    response = client.post(f'/plans/{pid}/approve', json={'actor_id': 'SIMULATION-approver'})
    assert response.status_code == 200, response.text
    rows = client.get(f'/plans/{pid}').json()['shipments']
    return cid, pid, {r['warehouse_id']: r['shipment_id'] for r in rows}


def manifest(warehouse='B', actual=1950, cancel=0, key=None):
    return dict(actor_id='SIMULATION-dispatcher', planned_quantity_kg=6000 if warehouse=='A' else 2000,
        actual_dispatched_quantity_kg=actual, unshipped_cancelled_quantity_kg=cancel,
        cancellation_reason='Tidak diberangkatkan; pembatalan eksplisit' if cancel else None,
        transaction_ref=key or f'SIM-DISPATCH-{warehouse}')


def send(client, sid, payload):
    return client.post(f'/shipments/{sid}/dispatch', json=payload)


def test_dispatch_01_02_08_10_simulation(client):
    cid, pid, ships = approved(client)
    assert send(client, ships['WH-A'], manifest('A', 6000)).status_code == 200
    result = send(client, ships['WH-B'], manifest()).json()
    assert result['remaining_reserved_stock_kg'] == result['remaining_reserved_dispatch_kg'] == 50
    assert result['warehouse_state']['safe_capacity'] == 2000
    assert result['warehouse_state']['remaining_dispatch_capacity'] == 2000
    payload = dict(actor_id='SIMULATION-dispatcher', transaction_ref='SIM-CANCEL-B',
        unshipped_cancelled_quantity_kg=50, cancellation_reason='50 kg tidak diberangkatkan')
    response = client.post(f"/shipments/{ships['WH-B']}/cancel-reservation", json=payload)
    assert response.status_code == 200, response.text
    assert client.post(f"/shipments/{ships['WH-B']}/cancel-reservation", json=payload).json() == response.json()
    states = {w: client.get(f'/warehouses/{w}/capacity').json() for w in ships}
    assert [states['WH-A'][k] for k in ('physical_stock','safe_capacity','actual_dispatched_today','remaining_dispatch_capacity')] == [29000,4000,6000,0]
    assert [states['WH-B'][k] for k in ('physical_stock','safe_capacity','actual_dispatched_today','remaining_dispatch_capacity')] == [20050,2050,1950,2050]
    plan = client.get(f'/plans/{pid}').json()
    assert (plan['planned_cost'],plan['estimated_dispatched_cost']) == (10200000,10125000)
    assert plan['planned_cost']-plan['estimated_dispatched_cost']==75000
    with TestingSessionLocal() as db:
        assert db.query(Shipment).count()==2
        assert sum(s.actual_dispatched_quantity_kg for s in db.query(Shipment))==7950
        assert sum(s.unshipped_cancelled_quantity_kg for s in db.query(Shipment))==50
        assert sum(r.reserved_stock_kg+r.reserved_dispatch_kg for r in db.query(Reservation))==0
        assert db.get(DistributionCase,cid).status=='DISPATCHED'
        assert db.query(AuditLog).filter_by(action_type='DISPATCH_CONFIRMED').count()==2
        assert db.query(AuditLog).filter_by(action_type='UNSHIPPED_RESERVATION_CANCELLED').count()==1
        audit=json.loads(db.query(AuditLog).filter_by(action_type='UNSHIPPED_RESERVATION_CANCELLED').one().details)
        assert audit['reservation_cancelled_kg']==50
        assert audit['actor_mode']=='SIMULATION_UNAUTHENTICATED'
    if os.environ.get('M5A_EVIDENCE_DB'):
        with test_engine.connect() as source, sqlite3.connect(os.environ['M5A_EVIDENCE_DB']) as destination:
            source.connection.driver_connection.backup(destination)
    print('M5A_ACTUAL_OUTPUT='+json.dumps({'warehouses':states,'shipment':{
        'planned_quantity':sum(s['planned_quantity_kg'] for s in plan['shipments']),
        'dispatched_quantity':sum(s['actual_dispatched_quantity_kg'] for s in plan['shipments']),
        'cancelled_quantity':sum(s['unshipped_cancelled_quantity_kg'] for s in plan['shipments']),
        'planned_cost':plan['planned_cost'],'estimated_dispatched_cost':plan['estimated_dispatched_cost']}}))


def test_dispatch_03_idempotent_and_payload_conflict(client):
    _,_,ships=approved(client)
    payload=manifest(cancel=50)
    first=send(client,ships['WH-B'],payload)
    assert first.status_code==200,first.text
    assert send(client,ships['WH-B'],payload).json()==first.json()
    assert send(client,ships['WH-B'],dict(payload,actual_dispatched_quantity_kg=1900)).status_code==409
    assert send(client,ships['WH-B'],dict(payload,transaction_ref='new-key')).status_code==409
    assert send(client,ships['WH-A'],dict(payload,planned_quantity_kg=6000)).status_code==409
    with TestingSessionLocal() as db:
        assert db.query(ShipmentEvent).count()==1
        assert db.get(DispatchUsage,('WH-B',operational_day())).dispatched_kg==1950


@pytest.mark.parametrize('mode',['over','unapproved','depleted','quota','external'])
def test_dispatch_04_05_11_rejections(client,mode):
    _,pid,ships=approved(client)
    with TestingSessionLocal() as db:
        if mode=='unapproved': db.get(AllocationPlan,pid).approval_status='PENDING_APPROVAL'
        s=db.query(InventorySnapshot).filter_by(warehouse_id='WH-B').one()
        if mode=='depleted': s.physical_stock=18000
        if mode=='quota': s.actual_dispatched_today=3000
        if mode=='external': s.external_reservations=3000
        db.commit()
        before=s.physical_stock
    assert send(client,ships['WH-B'],manifest(actual=2001 if mode=='over' else 1950)).status_code==409
    with TestingSessionLocal() as db:
        assert db.query(InventorySnapshot).filter_by(warehouse_id='WH-B').one().physical_stock==before
        assert db.query(Reservation).filter_by(warehouse_id='WH-B').one().reserved_stock_kg==2000
        assert db.query(ShipmentEvent).count()==0
        assert db.query(DispatchUsage).count()==0


def test_dispatch_06_rollback(client,monkeypatch):
    _,_,ships=approved(client)
    def fail(*args,**kwargs): raise RuntimeError('injected audit failure after stock flush')
    monkeypatch.setattr(AuditService,'record_log',fail)
    with TestingSessionLocal() as db, pytest.raises(RuntimeError,match='injected'):
        ShipmentService.execute(db,ships['WH-B'],DispatchRequest(**manifest(cancel=50)))
    with TestingSessionLocal() as db:
        assert db.query(InventorySnapshot).filter_by(warehouse_id='WH-B').one().physical_stock==22000
        assert db.query(Reservation).filter_by(warehouse_id='WH-B').one().reserved_stock_kg==2000
        assert db.query(DispatchUsage).count()==db.query(ShipmentEvent).count()==0
        assert db.get(Shipment,ships['WH-B']).manifest_status=='READY_TO_SHIP'
        assert db.query(AuditLog).filter_by(action_type='DISPATCH_CONFIRMED').count()==0


@pytest.mark.parametrize('different',[False,True])
def test_dispatch_07_concurrent_separate_connections(client,different):
    _,_,ships=approved(client)
    barrier=Barrier(2)
    def worker(index):
        # Each service call receives a separate Session/SQLite connection.
        with TestingSessionLocal() as db:
            barrier.wait(timeout=10)
            from app.services.shipment_service import ShipmentConflict
            try:
                ShipmentService.execute(db,ships['WH-B'],DispatchRequest(**manifest(key=f'key-{index}' if different else 'same')))
                return 200
            except ShipmentConflict: return 409
    with ThreadPoolExecutor(2) as pool:
        statuses=sorted(pool.map(worker,[0,1]))
    assert statuses==([200,409] if different else [200,200])
    with TestingSessionLocal() as db:
        assert db.query(InventorySnapshot).filter_by(warehouse_id='WH-B').one().physical_stock==20050
        assert db.get(DispatchUsage,('WH-B',operational_day())).dispatched_kg==1950
        assert db.query(ShipmentEvent).count()==1


def test_dispatch_09_wib_and_new_snapshot_do_not_reset_usage(client):
    assert operational_day(datetime(2026,10,27,16,59))=='2026-10-27'
    assert operational_day(datetime(2026,10,27,17,0))=='2026-10-28'
    _,_,ships=approved(client)
    assert send(client,ships['WH-A'],manifest('A',6000)).status_code==200
    new=client.post('/cases',json=get_demo_case_payload(demand=290)).json()['case_id']
    assert client.post(f'/cases/{new}/validate').json()['remaining_dispatch_capacities']['WH-A']==0
    with TestingSessionLocal() as db:
        db.query(Reservation).filter_by(warehouse_id='WH-B').one().operational_date='2020-01-01'
        db.commit()
    assert send(client,ships['WH-B'],manifest()).status_code==409


def test_cancellation_limits_and_before_dispatch(client):
    _,_,ships=approved(client)
    url=f"/shipments/{ships['WH-B']}/cancel-reservation"
    payload=dict(actor_id='SIMULATION',transaction_ref='cancel-first',unshipped_cancelled_quantity_kg=50,cancellation_reason='Non-dispatch')
    assert client.post(url,json=dict(payload,unshipped_cancelled_quantity_kg=2001)).status_code==409
    assert client.post(url,json=dict(payload,cancellation_reason='')).status_code==422
    assert client.post(url,json=payload).status_code==200
    assert send(client,ships['WH-B'],manifest()).status_code==200
    assert client.post(url,json=dict(payload,transaction_ref='cancel-used')).status_code==409


@pytest.mark.parametrize('value',[-1,0,1950.5,'NaN'])
def test_invalid_dispatch_quantities(client,value):
    _,_,ships=approved(client)
    assert send(client,ships['WH-B'],manifest(actual=value)).status_code==422


def test_dispatch_migration_existing_database(client,tmp_path,monkeypatch):
    from app.core import database
    _,_,ships=approved(client)
    legacy=create_engine('sqlite:///'+str(tmp_path/'legacy_m5a.db'))
    # Representative actual pre-M5A DB, retaining all eight old tables and rows.
    with test_engine.connect() as source, legacy.connect() as destination:
        source.connection.driver_connection.backup(destination.connection.driver_connection)
    with legacy.begin() as conn:
        for table in ('shipment_events','shipments','dispatch_usage'):
            conn.exec_driver_sql('DROP TABLE '+table)
    monkeypatch.setattr(database,'engine',legacy)
    monkeypatch.setattr(database,'SessionLocal',sessionmaker(bind=legacy,autoflush=False))
    database.upgrade_dispatch_schema()
    database.upgrade_dispatch_schema()
    with sessionmaker(bind=legacy)() as db:
        assert db.query(Shipment).count()==2
        assert db.query(Reservation).count()==2
        assert db.query(InventorySnapshot).filter_by(warehouse_id='WH-B').one().physical_stock==22000
    assert {'shipments','shipment_events','dispatch_usage'} <= set(inspect(legacy).get_table_names())
    legacy.dispose()


def test_concurrent_cancellation_and_dispatch(client):
    _,_,ships=approved(client)
    barrier=Barrier(2)
    def worker(cancel):
        with TestClient(app) as c:
            barrier.wait(timeout=10)
            if cancel:
                return c.post(f"/shipments/{ships['WH-B']}/cancel-reservation",json=dict(
                    actor_id='SIMULATION',transaction_ref='parallel-cancel',
                    unshipped_cancelled_quantity_kg=50,cancellation_reason='Not dispatched')).status_code
            return send(c,ships['WH-B'],manifest()).status_code
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(worker,[False,True]))==[200,200]
    with TestingSessionLocal() as db:
        reservation=db.query(Reservation).filter_by(warehouse_id='WH-B').one()
        assert reservation.reserved_stock_kg==reservation.reserved_dispatch_kg==0
        assert db.get(Shipment,ships['WH-B']).unshipped_cancelled_quantity_kg==50
        assert db.query(InventorySnapshot).filter_by(warehouse_id='WH-B').one().physical_stock==20050


def test_migration_ambiguous_legacy_rolls_back(client,tmp_path,monkeypatch):
    from app.core import database
    approved(client)
    legacy=create_engine('sqlite:///'+str(tmp_path/'ambiguous.db'))
    with test_engine.connect() as source, legacy.connect() as destination:
        source.connection.driver_connection.backup(destination.connection.driver_connection)
    with legacy.begin() as conn:
        for table in ('shipment_events','shipments','dispatch_usage'):
            conn.exec_driver_sql('DROP TABLE '+table)
        conn.exec_driver_sql('UPDATE reservations SET reserved_stock_kg=10')
    monkeypatch.setattr(database,'engine',legacy)
    monkeypatch.setattr(database,'SessionLocal',sessionmaker(bind=legacy,autoflush=False))
    with pytest.raises(RuntimeError,match='ambigu'):
        database.upgrade_dispatch_schema()
    assert 'shipments' not in inspect(legacy).get_table_names()
    with legacy.connect() as conn:
        assert conn.exec_driver_sql('SELECT count(*) FROM reservations').scalar()==2
    legacy.dispose()
