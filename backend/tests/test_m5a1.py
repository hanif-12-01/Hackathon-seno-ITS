"""Post-M5A integrity: authoritative inventory, lifecycle, real process restarts."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
import pytest
from sqlalchemy import inspect
from test_api_workflow import (client, clean_database, cleanup_test_file, TestingSessionLocal,
    get_demo_case_payload, test_engine)
from test_dispatch import approved, manifest, send
from app.models.db_models import InventorySnapshot, AllocationPlan, Approval, DistributionCase, Reservation


def completed(client):
    cid, pid, ships = approved(client)
    for warehouse, actual in [('A',6000),('B',1950)]:
        response=send(client,ships['WH-'+warehouse],manifest(warehouse,actual))
        assert response.status_code==200,response.text
    cancellation=dict(actor_id='SIMULATION-dispatcher',transaction_ref='M5A1-CANCEL',
        unshipped_cancelled_quantity_kg=50,cancellation_reason='Tidak diberangkatkan')
    response=client.post(f"/shipments/{ships['WH-B']}/cancel-reservation",json=cancellation)
    assert response.status_code==200,response.text
    return cid,pid,ships,cancellation


def test_inventory_source_of_truth_after_dispatch(client):
    cid,pid,ships,_=completed(client)
    # Retain a genuine pre-dispatch historical observation, not authoritative latest.
    with TestingSessionLocal() as db:
        current=db.query(InventorySnapshot).filter_by(warehouse_id='WH-B').one()
        historical=InventorySnapshot(**{c.name:getattr(current,c.name) for c in InventorySnapshot.__table__.columns})
        historical.id='HISTORICAL-B'
        historical.snapshot_time-=timedelta(hours=1)
        historical.physical_stock=22000
        db.add(historical)
        db.commit()
    new=client.post('/cases',json=get_demo_case_payload(demand=5000))
    assert new.status_code==201,new.text
    new_id=new.json()['case_id']
    states={w:client.get(f'/warehouses/{w}/capacity').json() for w in ships}
    assert states['WH-B']['physical_stock']==20050,states
    assert states['WH-B']['safe_capacity']==2050
    assert states['WH-A']['physical_stock']==29000
    assert states['WH-A']['remaining_dispatch_capacity']==0
    optimized=client.post(f'/cases/{new_id}/optimize')
    assert optimized.status_code==200,optimized.text
    lines={line['warehouse_id']:line['allocated_kg'] for line in optimized.json()['lines']}
    assert lines=={'WH-A':0,'WH-B':2050}
    with TestingSessionLocal() as db:
        assert db.get(InventorySnapshot,'HISTORICAL-B').physical_stock==22000
        assert db.get(AllocationPlan,pid).planned_cost==10200000
        assert all(r.reserved_stock_kg==0 for r in db.query(Reservation).filter_by(plan_id=pid))
    print('M5A1_INVENTORY='+json.dumps(states))


@pytest.mark.parametrize('invalid',['stale','unverified'])
def test_new_case_does_not_refresh_inventory_verification(client,invalid):
    completed(client)
    with TestingSessionLocal() as db:
        snapshot=db.query(InventorySnapshot).filter_by(warehouse_id='WH-B').one()
        if invalid=='stale': snapshot.snapshot_time=datetime.utcnow()-timedelta(days=2)
        else: snapshot.is_verified=False
        db.commit()
        observation=snapshot.snapshot_time
    new=client.post('/cases',json=get_demo_case_payload()).json()['case_id']
    assert not client.post(f'/cases/{new}/validate').json()['is_ready']
    assert client.post(f'/cases/{new}/optimize').status_code==422
    with TestingSessionLocal() as db:
        snapshot=db.query(InventorySnapshot).filter_by(case_id=new,warehouse_id='WH-B').one()
        assert snapshot.snapshot_time==observation
        assert snapshot.physical_stock==20050


def test_state_separation_and_invalid_transition(client):
    cid,pid,ships,_=completed(client)
    before=client.get(f'/plans/{pid}').json()
    assert before['approval_status']=='APPROVED'
    assert before['fulfillment_status']=='FULL_FULFILLMENT' # Solver allocation history, not receipt proof.
    assert sum(s['in_transit_quantity_kg'] for s in before['shipments'])==7950
    assert client.get(f'/cases/{cid}').json()['status']=='DISPATCHED'
    assert send(client,ships['WH-B'],manifest(key='illegal-second')).status_code==409
    assert client.get(f'/plans/{pid}').json()==before
    assert client.post(f'/plans/{pid}/approve',json={'actor_id':'SIMULATION','decision':'REJECT'}).status_code==409
    with TestingSessionLocal() as db:
        assert db.query(Approval).filter_by(plan_id=pid).count()==1
        assert db.get(DistributionCase,cid).status=='DISPATCHED'
        assert db.get(AllocationPlan,pid).version==1
        assert not db.get(AllocationPlan,pid).is_replanning
    print('M5A1_STATE=DISPATCHED; approval APPROVED; in_transit 7950; demand receipt unverified; receiving/replan DEFERRED')


def dump_database(path):
    with sqlite3.connect(path) as db:
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t:sorted(db.execute('SELECT * FROM '+t).fetchall(),key=repr) for t in tables}


def backup(path):
    with test_engine.connect() as source,sqlite3.connect(path) as destination:
        source.connection.driver_connection.backup(destination)


PROCESS = '''
import json,sys
from app.main import app
from app.core.database import engine
from fastapi.testclient import TestClient
requests=json.loads(sys.argv[1])
with TestClient(app) as client:
    results=[{'status':r.status_code,'body':r.json()} for method,url,payload in requests
        for r in [client.request(method,url,json=payload)]]
engine.dispose()
print(json.dumps(results))
'''


def restart(path, requests=(), success=True):
    env=dict(os.environ,PANGANNADI_DATABASE_URL='sqlite:///'+str(path))
    result=subprocess.run([sys.executable,'-c',PROCESS,json.dumps(requests)],env=env,
        cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=40)
    if not success:
        assert result.returncode!=0
        assert 'ambigu' in result.stderr,result.stderr
        return result.stderr
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


def test_restart_replay_and_repeated_startup(client,tmp_path):
    _,_,ships,cancellation=completed(client)
    path=tmp_path/'restart.sqlite3'
    backup(path)
    before=dump_database(path)
    for i in range(3):
        responses=restart(path,[('POST',f"/shipments/{ships['WH-B']}/dispatch",manifest()),
            ('POST',f"/shipments/{ships['WH-B']}/cancel-reservation",cancellation),
            ('POST',f"/shipments/{ships['WH-B']}/dispatch",manifest(actual=1900)),
            ('POST',f"/shipments/{ships['WH-B']}/cancel-reservation",dict(cancellation,unshipped_cancelled_quantity_kg=49))])
        assert [r['status'] for r in responses]==[200,200,409,409]
        assert dump_database(path)==before
        if i==0: first=responses
        else: assert responses==first
    print('M5A1_RESTART=3 fresh Python processes; dispatch/cancel replay 200/200; changed payload 409/409; all DB rows identical')
    if os.environ.get('M5A1_EVIDENCE_DIR'):
        directory=Path(os.environ['M5A1_EVIDENCE_DIR'])
        with sqlite3.connect(path) as source,sqlite3.connect(directory/'m5a1_restart_20261027.sqlite3') as destination:
            source.backup(destination)
        (directory/'m5a1_restart_rows_20261027.json').write_text(json.dumps(before,indent=2),encoding='utf-8')


@pytest.mark.parametrize('ambiguous',[False,True])
def test_legacy_startup_backfill_and_failure_rollback(client,tmp_path,ambiguous):
    approved(client)
    path=tmp_path/'legacy.sqlite3'
    backup(path)
    with sqlite3.connect(path) as db:
        for table in ('shipment_events','shipments','dispatch_usage'):
            db.execute('DROP TABLE '+table)
        if ambiguous: db.execute('UPDATE reservations SET reserved_stock_kg=10')
    before=dump_database(path)
    if ambiguous:
        restart(path,success=False)
        assert dump_database(path)==before
        print('M5A1_MIGRATION=ambiguous legacy rejected; all original tables/rows identical; no partial migration')
    else:
        restart(path)
        after=dump_database(path)
        assert all(after[t]==rows for t,rows in before.items())
        assert len(after['shipments'])==2
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT sum(actual_dispatched_quantity_kg) FROM shipments').fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM shipment_events').fetchone()[0]==0
        for _ in range(2):
            restart(path)
            assert dump_database(path)==after
        print('M5A1_MIGRATION=eligible legacy backfill 2 READY shipments; no actual dispatch; 3 startups preserve data')
