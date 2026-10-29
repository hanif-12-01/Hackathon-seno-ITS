import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
import pytest
from sqlalchemy import inspect
from test_api_workflow import client, clean_database, cleanup_test_file, TestingSessionLocal, test_engine
from test_dispatch import approved, manifest, send
from app.models.db_models import (Receipt, Reconciliation, ReceivingEvent, ReceiptShipment,
    DistributionCase, AllocationPlan, Shipment, InventorySnapshot)
from app.models.schemas import ReceiptRequest, ReconcileRequest
from app.services.receiving_service import ReceivingService
from app.services.audit_service import AuditService


def dispatched(client):
    cid, pid, ships = approved(client)
    assert send(client, ships['WH-A'], manifest('A', 6000)).status_code == 200
    assert send(client, ships['WH-B'], manifest(cancel=50)).status_code == 200
    payload = dict(inspector_id='SIMULATION-inspector', transaction_ref='RECEIVE-1', plan_id=pid,
        shipment_ids=list(ships.values()), total_dispatched_kg=7950, arrived_weight_kg=7820,
        rejected_weight_kg=110, inspection_notes='Selisih objektif, tanpa atribusi penyebab.')
    return cid, pid, ships, payload


def receive(client, cid, payload):
    return client.post(f'/cases/{cid}/receipts', json=payload)


def final_payload(receipt):
    return dict(actor_id='SIMULATION-finalizer', transaction_ref='FINAL-1', receipt_id=receipt['receipt_id'])


def rows(path):
    with sqlite3.connect(path) as db:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n: db.execute(f'SELECT * FROM "{n}" ORDER BY rowid').fetchall() for n in names}


def test_receiving_simulation_inventory_history_persistence(client, tmp_path):
    cid, pid, ships, payload = dispatched(client)
    before = client.get(f'/plans/{pid}').json()
    states = {w: client.get(f'/warehouses/{w}/capacity').json() for w in ships}
    response = receive(client, cid, payload)
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert (receipt['sound_weight_kg'], receipt['transit_discrepancy_kg']) == (7710, 130)
    assert receipt['receipt_status'] == 'RECORDED'
    assert client.get(f'/cases/{cid}').json()['status'] == 'RECEIVED'
    assert client.get(f'/cases/{cid}/reconciliation').status_code == 404
    request = final_payload(receipt)
    response = client.post(f'/cases/{cid}/reconcile', json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result['target_demand_kg'], result['total_dispatched_kg'], result['total_arrived_kg'],
        result['total_rejected_kg'], result['total_sound_kg'], result['transit_discrepancy_kg'],
        result['unmet_demand_kg']) == (8000,7950,7820,110,7710,130,290)
    assert result['case_status'] == 'REPLAN_AVAILABLE' and result['replan_ready']
    assert client.get(f'/cases/{cid}/reconciliation').json() == result
    assert client.get(f'/cases/{cid}/receipts').json()['receipt_status'] == 'FINALIZED'
    after = client.get(f'/plans/{pid}').json()
    assert {k:v for k,v in before.items() if k!='shipments'} == {k:v for k,v in after.items() if k!='shipments'}
    assert sum(s['in_transit_quantity_kg'] for s in after['shipments']) == 0
    assert states == {w: client.get(f'/warehouses/{w}/capacity').json() for w in ships}
    assert [states[w]['physical_stock'] for w in ('WH-A','WH-B')] == [29000,20050]
    assert [states[w]['actual_dispatched_today'] for w in ('WH-A','WH-B')] == [6000,1950]
    with TestingSessionLocal() as db:
        assert db.query(Receipt).count() == db.query(Reconciliation).count() == 1
        assert db.query(ReceiptShipment).count() == db.query(ReceivingEvent).count() == 2
        assert db.query(AllocationPlan).count() == 1
    path = os.environ.get('M5B_EVIDENCE_DB', str(tmp_path/'simulation.sqlite3'))
    with test_engine.connect() as source, sqlite3.connect(path) as destination:
        source.connection.driver_connection.backup(destination)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT total_sound_kg,unmet_demand_kg FROM reconciliations').fetchone() == (7710,290)
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
    evidence = dict(receipt=receipt, reconciliation=result, warehouses=states, finalization_request=request,
        receiving_request=payload, case_id=cid, plan_id=pid)
    if os.environ.get('M5B_EVIDENCE_DB'):
        from pathlib import Path
        Path(path).with_suffix('.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print('M5B_ACTUAL_OUTPUT='+json.dumps(evidence))


@pytest.mark.parametrize('field,value',[('arrived_weight_kg',-1),('rejected_weight_kg',-1),
    ('arrived_weight_kg',7820.5),('rejected_weight_kg',0.1),('rejected_weight_kg',7821),
    ('arrived_weight_kg','NaN'),('transaction_ref',' '),('inspector_id',' ')])
def test_receiving_validation(client,field,value):
    cid,_,_,payload=dispatched(client)
    assert receive(client,cid,dict(payload,**{field:value})).status_code==422
    with TestingSessionLocal() as db:
        assert db.query(Receipt).count()==db.query(ReceivingEvent).count()==0
        assert db.get(DistributionCase,cid).status=='DISPATCHED'


@pytest.mark.parametrize('mode',['over','total','subset','duplicate','other-plan','missing-plan','missing-case','undispatched','unapproved','reservation','bad-line'])
def test_receiving_references(client,mode):
    cid,pid,ships,payload=dispatched(client)
    expected=409
    if mode=='over': payload['arrived_weight_kg']=7951
    if mode=='total': payload['total_dispatched_kg']=8000
    if mode=='subset': payload['shipment_ids']=[ships['WH-A']]
    if mode=='duplicate': payload['shipment_ids']=[ships['WH-A']]*2; expected=422
    if mode=='missing-plan': payload['plan_id']='missing'; expected=404
    if mode=='missing-case': cid='missing'; expected=404
    with TestingSessionLocal() as db:
        if mode=='other-plan': db.get(AllocationPlan,pid).case_id='other'
        if mode=='unapproved': db.get(AllocationPlan,pid).approval_status='PENDING_APPROVAL'
        if mode=='undispatched': db.get(Shipment,ships['WH-B']).manifest_status='READY_TO_SHIP'
        if mode=='bad-line': db.get(Shipment,ships['WH-B']).allocation_line_id='missing'
        if mode=='reservation':
            from app.models.db_models import Reservation
            db.query(Reservation).filter_by(warehouse_id='WH-B').one().reserved_stock_kg=50
        db.commit()
    assert receive(client,cid,payload).status_code==expected
    with TestingSessionLocal() as db:
        assert db.query(Receipt).count()==0


def test_receiving_retries_conflicts_after_finalization(client):
    cid,_,_,payload=dispatched(client)
    first=receive(client,cid,payload)
    assert first.status_code==201
    assert receive(client,cid,dict(payload,shipment_ids=list(reversed(payload['shipment_ids'])))).json()==first.json()
    assert receive(client,cid,dict(payload,arrived_weight_kg=7800)).status_code==409
    assert receive(client,cid,dict(payload,transaction_ref='RECEIVE-2')).status_code==409
    req=final_payload(first.json())
    final=client.post(f'/cases/{cid}/reconcile',json=req)
    assert final.status_code==200
    assert client.post(f'/cases/{cid}/reconcile',json=req).json()==final.json()
    assert client.post(f'/cases/{cid}/reconcile',json=dict(req,actor_id='different')).status_code==409
    assert client.post(f'/cases/{cid}/reconcile',json=dict(req,transaction_ref='FINAL-2')).status_code==409
    assert receive(client,cid,payload).json()==first.json()
    assert receive(client,cid,dict(payload,transaction_ref='new')).status_code==409
    with TestingSessionLocal() as db:
        assert db.query(ReceivingEvent).count()==2


@pytest.mark.parametrize('different',[False,True])
@pytest.mark.parametrize('action',['RECEIVE','FINALIZE'])
def test_receiving_concurrent(client,different,action):
    cid,_,_,payload=dispatched(client)
    if action=='FINALIZE': payload=final_payload(receive(client,cid,payload).json())
    barrier=Barrier(2)
    def execute(i):
        req=dict(payload,transaction_ref=payload['transaction_ref']+str(i) if different else payload['transaction_ref'])
        barrier.wait()
        endpoint='receipts' if action=='RECEIVE' else 'reconcile'
        return client.post(f'/cases/{cid}/{endpoint}',json=req)
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(execute,[0,1]))
    success=201 if action=='RECEIVE' else 200
    assert sorted(r.status_code for r in results)==sorted([success,409 if different else success])
    if not different: assert results[0].json()==results[1].json()
    with TestingSessionLocal() as db:
        assert db.query(Receipt).count()==1
        assert db.query(Reconciliation).count()==(action=='FINALIZE')


@pytest.mark.parametrize('action',['RECEIVE','FINALIZE'])
def test_receiving_rollback(client,monkeypatch,action):
    cid,_,ships,payload=dispatched(client)
    if action=='FINALIZE': payload=final_payload(receive(client,cid,payload).json())
    with test_engine.connect() as conn:
        before={name:conn.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() for name in inspect(conn).get_table_names()}
    def fail(*args,**kwargs): raise RuntimeError('injected receiving audit failure after flush')
    monkeypatch.setattr(AuditService,'record_log',fail)
    req=ReceiptRequest(**payload) if action=='RECEIVE' else ReconcileRequest(**payload)
    with TestingSessionLocal() as db,pytest.raises(RuntimeError,match='injected'):
        ReceivingService.execute(db,cid,req,action)
    with test_engine.connect() as conn:
        after={name:conn.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() for name in inspect(conn).get_table_names()}
    assert before==after


@pytest.mark.parametrize('arrived,rejected,demand,expired,remaining,status',[
    (0,0,8000,False,8000,'REPLAN_AVAILABLE'),(7950,7950,8000,False,8000,'REPLAN_AVAILABLE'),
    (7950,0,7950,False,0,'RECONCILED'),(7950,0,7900,False,0,'RECONCILED'),
    (7820,110,8000,True,290,'RECONCILED'),(7950,0,0,False,0,'RECONCILED')])
def test_reconciliation_boundaries(client,arrived,rejected,demand,expired,remaining,status):
    cid,_,_,payload=dispatched(client)
    payload.update(arrived_weight_kg=arrived,rejected_weight_kg=rejected)
    receipt=receive(client,cid,payload).json()
    with TestingSessionLocal() as db:
        case=db.get(DistributionCase,cid)
        case.verified_demand=demand
        if expired: case.created_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(hours=49)
        db.commit()
    result=client.post(f'/cases/{cid}/reconcile',json=final_payload(receipt))
    assert result.status_code==200,result.text
    assert result.json()['unmet_demand_kg']==remaining
    assert result.json()['case_status']==status


def test_receiving_restart_and_additive_startup(client,tmp_path):
    cid,_,_,payload=dispatched(client)
    receipt=receive(client,cid,payload).json()
    req=final_payload(receipt)
    path=tmp_path/'restart.sqlite3'
    with test_engine.connect() as source,sqlite3.connect(path) as destination:
        source.connection.driver_connection.backup(destination)
    # Real M5A DB: remove only empty M5B tables in this disposable copy, then startup migration.
    legacy=tmp_path/'m5a.sqlite3'
    with sqlite3.connect(path) as source,sqlite3.connect(legacy) as destination:
        source.backup(destination)
        for name in ('receiving_events','reconciliations','receipt_shipments','receipts'):
            destination.execute(f'DROP TABLE {name}')
    old=rows(legacy)
    env=dict(os.environ,PANGANNADI_DATABASE_URL='sqlite:///'+legacy.as_posix())
    for _ in range(3):
        run=subprocess.run([sys.executable,'-c','import app.main; from app.core.database import engine; engine.dispose()'],env=env,capture_output=True,text=True)
        assert run.returncode==0,run.stdout+run.stderr
        current=rows(legacy)
        assert all(current[n]==values for n,values in old.items())
        assert all(current[n]==[] for n in ('receiving_events','reconciliations','receipt_shipments','receipts'))
    env=dict(os.environ,PANGANNADI_DATABASE_URL='sqlite:///'+path.as_posix())
    code='''import json,sys
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import engine
cid,payload,req=json.loads(sys.argv[1])
with TestClient(app) as client:
    assert client.post(f'/cases/{cid}/receipts',json=payload).status_code==201
    result=client.post(f'/cases/{cid}/reconcile',json=req)
    assert result.status_code==200,result.text
    assert result.json()['unmet_demand_kg']==290
    assert client.post(f'/cases/{cid}/reconcile',json=dict(req,actor_id='different')).status_code==409
engine.dispose()
'''
    stable=None
    for _ in range(3):
        run=subprocess.run([sys.executable,'-c',code,json.dumps([cid,payload,req])],env=env,capture_output=True,text=True)
        assert run.returncode==0,run.stdout+run.stderr
        if stable is None: stable=rows(path)
        else: assert rows(path)==stable
    if os.environ.get('M5B_EVIDENCE_DB'):
        from pathlib import Path
        target=Path(os.environ['M5B_EVIDENCE_DB']).with_name('m5b_restart_20261028.sqlite3')
        with sqlite3.connect(path) as source,sqlite3.connect(target) as destination: source.backup(destination)
    print('M5B_RESTART=3 fresh processes; recorded receipt retry; finalization retry/conflict; full table equality; M5A migration 3 startups preserved history')


def test_global_key_collision(client):
    cid,_,_,payload=dispatched(client)
    assert receive(client,cid,dict(payload,transaction_ref='SIM-DISPATCH-A')).status_code==409
    receipt=receive(client,cid,payload).json()
    req=final_payload(receipt)
    assert client.post(f'/cases/{cid}/reconcile',json=dict(req,transaction_ref=payload['transaction_ref'])).status_code==409
    # A receiving key must also be unavailable to a later shipment mutation.
    from app.models.db_models import Reservation
    from app.models.schemas import CancellationRequest
    from app.services.shipment_service import ShipmentService, ShipmentConflict
    with TestingSessionLocal() as db:
        ship=db.query(Shipment).filter_by(warehouse_id='WH-A').one()
        sid=ship.id
        db.rollback()
        with pytest.raises(ShipmentConflict,match='transaction_ref'):
            ShipmentService.execute(db,sid,CancellationRequest(actor_id='actor',transaction_ref=payload['transaction_ref'],
                unshipped_cancelled_quantity_kg=1,cancellation_reason='collision'),'CANCEL')


@pytest.mark.parametrize('mode',['unverified','wrong-receipt','missing-receipt','wrong-case-state'])
def test_finalization_guards(client,mode):
    cid,_,_,payload=dispatched(client)
    receipt=receive(client,cid,payload).json()
    req=final_payload(receipt)
    with TestingSessionLocal() as db:
        if mode=='unverified': db.get(DistributionCase,cid).is_demand_verified=False
        if mode=='wrong-receipt': db.get(Receipt,receipt['receipt_id']).case_id='other'
        if mode=='wrong-case-state': db.get(DistributionCase,cid).status='DISPATCHED'
        db.commit()
    if mode=='missing-receipt': req['receipt_id']='missing'
    assert client.post(f'/cases/{cid}/reconcile',json=req).status_code==(404 if mode=='missing-receipt' else 409)
    with TestingSessionLocal() as db:
        assert db.query(Reconciliation).count()==0
        assert all(s.manifest_status=='DISPATCHED' for s in db.query(Shipment))


def test_receiving_migration_atomic_rollback(client,tmp_path,monkeypatch):
    from sqlalchemy import create_engine
    from app.core import database
    from app.models.db_models import Reconciliation
    cid,_,_,_=dispatched(client)
    path=tmp_path/'migration_rollback.sqlite3'
    with test_engine.connect() as source,sqlite3.connect(path) as destination:
        source.connection.driver_connection.backup(destination)
        for name in ('receiving_events','reconciliations','receipt_shipments','receipts'):
            destination.execute(f'DROP TABLE {name}')
    before=rows(path)
    engine=create_engine('sqlite:///'+path.as_posix())
    monkeypatch.setattr(database,'engine',engine)
    def fail(*args,**kwargs): raise RuntimeError('injected migration failure')
    monkeypatch.setattr(Reconciliation.__table__,'create',fail)
    with pytest.raises(RuntimeError,match='injected migration'):
        database.upgrade_receiving_schema()
    engine.dispose()
    assert rows(path)==before
