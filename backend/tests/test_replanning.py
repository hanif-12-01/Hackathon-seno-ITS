import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import inspect, create_engine
from sqlalchemy.exc import IntegrityError
from test_api_workflow import client, clean_database, cleanup_test_file, TestingSessionLocal, test_engine, get_demo_case_payload
from test_receiving import dispatched, receive, final_payload, rows
from app.models.db_models import (AllocationPlan, AllocationLine, Reservation, ReplanningEvent,
    InventorySnapshot, DistributionCase, Receipt, Reconciliation, Approval, Shipment, AuditLog)
from app.models.schemas import ReplanRequest, ApproveRequest
from app.services.replanning_service import ReplanningService
from app.services.approval_service import ApprovalService
from app.services.audit_service import AuditService
from app.services.optimizer import OptimizationEngine
from app.services.capacity import operational_day


def finalized(client, zero=False):
    if zero:
        from test_dispatch import manifest, send
        cid=client.post('/cases',json=get_demo_case_payload()).json()['case_id']
        assert client.post(f'/cases/{cid}/validate').json()['is_ready']
        pid=client.post(f'/cases/{cid}/optimize').json()['plan_id']
        assert decide(client,pid).status_code==200
        ships={s['warehouse_id']:s['shipment_id'] for s in client.get(f'/plans/{pid}').json()['shipments']}
        assert send(client,ships['WH-A'],manifest('A',6000)).status_code==200
        assert send(client,ships['WH-B'],manifest(actual=2000)).status_code==200
        payload=dict(inspector_id='inspector',transaction_ref='receive-zero',plan_id=pid,
            shipment_ids=list(ships.values()),total_dispatched_kg=8000,arrived_weight_kg=8000,rejected_weight_kg=0)
    else:
        cid,pid,ships,payload=dispatched(client)
        assert client.post(f'/cases/{cid}/validate').json()['is_ready']
    receipt=receive(client,cid,payload)
    assert receipt.status_code==201,receipt.text
    final=client.post(f'/cases/{cid}/reconcile',json=final_payload(receipt.json()))
    assert final.status_code==200,final.text
    req=dict(actor_id='SIMULATION-planner',transaction_ref='REPLAN-1',source_reconciliation_id=final.json()['reconciliation_id'])
    return cid,pid,req


def generate(client,cid,req):
    return client.post(f'/cases/{cid}/replan',json=req)


def decide(client,pid,decision='APPROVE'):
    return client.post(f'/plans/{pid}/approve',json=dict(actor_id='SIMULATION-approver',decision=decision,notes='M6 decision'))


def snapshot():
    with test_engine.connect() as conn:
        return {n:[tuple(r) for r in conn.exec_driver_sql(f'SELECT * FROM "{n}" ORDER BY rowid')] for n in inspect(conn).get_table_names()}


def immutable_v1(pid):
    with TestingSessionLocal() as db:
        models=[AllocationPlan,AllocationLine,Approval,Shipment,Reservation]
        return {m.__tablename__:[{c.name:getattr(r,c.name) for c in m.__table__.columns} for r in
            (db.query(m).filter_by(id=pid) if m is AllocationPlan else db.query(m).filter_by(plan_id=pid)).all()] for m in models}


def capacity_states(client):
    return {w:client.get(f'/warehouses/{w}/capacity').json() for w in ('WH-A','WH-B')}


def test_m6_01_02_11_17_closed_loop(client,tmp_path):
    cid,v1,req=finalized(client)
    before=immutable_v1(v1)
    states=capacity_states(client)
    response=generate(client,cid,req)
    assert response.status_code==200,response.text
    result=response.json(); v2=result['plan_id']
    assert (result['version'],result['plan_status'],result['remaining_demand_kg'],result['allocated_kg'],result['unmet_demand_kg'],result['planned_cost'])==(2,'PENDING_APPROVAL',290,290,0,435000)
    assert result['allocations']=={'WH-A':0,'WH-B':290}
    assert result['solver_status']=='OPTIMAL' and result['fulfillment_status']=='FULL_FULFILLMENT'
    assert immutable_v1(v1)==before and capacity_states(client)==states
    with TestingSessionLocal() as db:
        assert db.query(Reservation).filter_by(plan_id=v2).count()==0
        assert db.query(Shipment).filter_by(plan_id=v2).count()==0
    detail=client.get(f'/plans/{v2}').json()
    assert detail['decision_input']['remaining_demand_kg']==290
    assert detail['operational_date']==operational_day()
    assert len(client.get(f'/cases/{cid}').json()['plans'])==2
    approved=decide(client,v2)
    assert approved.status_code==200,approved.text
    assert decide(client,v2).json()==approved.json()
    after=capacity_states(client)
    assert after['WH-B']['physical_stock']==20050 and after['WH-B']['actual_dispatched_today']==1950
    assert after['WH-B']['active_stock_reservations']==after['WH-B']['active_dispatch_reservations_today']==290
    assert after['WH-B']['remaining_dispatch_capacity']==after['WH-B']['safe_capacity']==1760
    assert immutable_v1(v1)==before
    assert client.get(f'/cases/{cid}').json()['status']=='RECOVERY_RESERVED'
    ship=client.get(f'/plans/{v2}').json()['shipments'][0]
    assert ship['manifest_status']=='READY_TO_SHIP' and ship['actual_dispatched_quantity_kg']==0
    assert client.post(f"/shipments/{ship['shipment_id']}/dispatch",json=dict(actor_id='actor',transaction_ref='no-dispatch',planned_quantity_kg=290,actual_dispatched_quantity_kg=290)).status_code==409
    history=client.get(f'/cases/{cid}/replanning-history').json()
    assert len(history)==1 and history[0]['result']==result
    path=Path(os.environ.get('M6_EVIDENCE_DB',str(tmp_path/'simulation.sqlite3')))
    with test_engine.connect() as source,sqlite3.connect(path) as destination: source.connection.driver_connection.backup(destination)
    evidence=dict(case_id=cid,v1_before=before,v1_after=immutable_v1(v1),v2_pending=result,
        v2_approved=client.get(f'/plans/{v2}').json(),capacity_before=states,capacity_after=after,
        history=history,request=req,approval_response=approved.json(),audit=client.get(f'/cases/{cid}/audit').json())
    if os.environ.get('M6_EVIDENCE_DB'): path.with_suffix('.json').write_text(json.dumps(evidence,default=str,indent=2),encoding='utf-8')
    print('M6_ACTUAL='+json.dumps(evidence,default=str))


def test_m6_03_pending_receipt(client):
    cid,_,_,payload=dispatched(client)
    assert receive(client,cid,payload).status_code==201
    req=dict(actor_id='actor',transaction_ref='key',source_reconciliation_id='missing')
    assert generate(client,cid,req).status_code==409
    assert not client.get(f'/cases/{cid}/recovery-eligibility').json()['eligible']


def test_m6_04_zero_remaining(client):
    cid,_,req=finalized(client,zero=True)
    result=generate(client,cid,req)
    assert result.status_code==200,result.text
    assert result.json()['evaluation_status']=='NO_RECOVERY_NEEDED'
    assert result.json()['plan_id'] is None
    with TestingSessionLocal() as db:
        assert db.query(AllocationPlan).count()==1
        assert db.query(Reservation).filter_by(status='ACTIVE').count()==0


def external(client,amount):
    with TestingSessionLocal() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        db.query(InventorySnapshot).filter_by(warehouse_id='WH-B').one().external_reservations=amount
        db.commit()


@pytest.mark.parametrize('mode',['external','internal','none'])
def test_m6_05_06_reservations_partial_no_capacity(client,mode):
    cid,_,req=finalized(client)
    if mode=='external': external(client,1800)
    if mode=='internal':
        response=client.post('/cases',json=get_demo_case_payload(demand=1800))
        other=response.json()['case_id']; pid=client.post(f'/cases/{other}/optimize').json()['plan_id']
        assert decide(client,pid).status_code==200
    if mode=='none': external(client,2050)
    response=generate(client,cid,req)
    assert response.status_code==200,response.text
    result=response.json()
    assert result['solver_status']=='OPTIMAL'
    assert result['allocations']=={'WH-A':0,'WH-B':0 if mode=='none' else 250}
    assert result['unmet_demand_kg']==(290 if mode=='none' else 40)
    assert result['planned_cost']==(0 if mode=='none' else 375000)
    assert result['fulfillment_status']==('NO_ALLOCATION' if mode=='none' else 'PARTIAL_FULFILLMENT')
    if mode=='none': assert result['plan_id'] is None and result['evaluation_status']=='NO_ALLOCATION'


@pytest.mark.parametrize('mode',['stale','unverified','fraction','cost','deadline','day','demand','source'])
def test_m6_07_readiness(client,mode):
    cid,_,req=finalized(client)
    with TestingSessionLocal() as db:
        s=db.query(InventorySnapshot).filter_by(warehouse_id='WH-B').one()
        if mode=='stale': s.snapshot_time=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=2)
        if mode=='unverified': s.is_verified=False
        if mode=='fraction': s.external_reservations=0.5
        if mode=='cost': s.cost_per_kg=1500.001
        if mode=='deadline': db.get(DistributionCase,cid).created_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(hours=49)
        if mode=='day': db.query(Shipment).first().operational_date='2000-01-01'
        if mode=='demand': db.get(DistributionCase,cid).verified_demand=8001
        if mode=='source': req['source_reconciliation_id']='wrong'
        db.commit()
    assert generate(client,cid,req).status_code==409
    with TestingSessionLocal() as db: assert db.query(AllocationPlan).count()==1


def test_m6_08_duplicate_validation(client):
    cid,_,req=finalized(client)
    first=generate(client,cid,req)
    assert first.status_code==200
    assert generate(client,cid,req).json()==first.json()
    assert generate(client,cid,dict(req,actor_id='different')).status_code==409
    assert generate(client,cid,dict(req,transaction_ref='new')).status_code==409
    assert generate(client,cid,dict(req,remaining_demand_kg=290)).status_code==422
    assert generate(client,cid,dict(req,actor_id=' ')).status_code==422
    assert not client.get(f'/cases/{cid}/recovery-eligibility').json()['eligible']


@pytest.mark.parametrize('mode',['same','different','approve','opposite'])
def test_m6_09_13_14_concurrency(client,mode):
    cid,_,req=finalized(client)
    pid=generate(client,cid,req).json()['plan_id'] if mode in ('approve','opposite') else None
    barrier=Barrier(2)
    def run(i):
        barrier.wait()
        if pid: return decide(client,pid,'REJECT' if mode=='opposite' and i else 'APPROVE')
        return generate(client,cid,dict(req,transaction_ref='other' if mode=='different' and i else req['transaction_ref']))
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(run,[0,1]))
    assert sorted(r.status_code for r in results)==([200,409] if mode in ('different','opposite') else [200,200])
    with TestingSessionLocal() as db:
        assert db.query(AllocationPlan).filter_by(version=2).count()==1
        assert db.query(ReplanningEvent).count()==1
        if pid:
            assert db.query(Approval).filter_by(plan_id=pid).count()==1
            assert db.query(Reservation).filter_by(plan_id=pid).count()<=1
    print('M6_CONCURRENCY='+mode+':'+str([r.status_code for r in results]))


def test_m6_12_approval_conflict_and_reject(client):
    cid,v1,req=finalized(client); before=immutable_v1(v1)
    pid=generate(client,cid,req).json()['plan_id']
    external(client,1800)
    assert decide(client,pid).status_code==409
    with TestingSessionLocal() as db:
        assert db.query(Reservation).filter_by(plan_id=pid).count()==0
        assert db.get(AllocationPlan,pid).approval_status=='PENDING_APPROVAL'
    assert decide(client,pid,'REJECT').status_code==200
    assert decide(client,pid).status_code==409
    assert immutable_v1(v1)==before


@pytest.mark.parametrize('action',['generate','approve'])
def test_m6_15_rollback(client,monkeypatch,action):
    cid,_,req=finalized(client)
    pid=generate(client,cid,req).json()['plan_id'] if action=='approve' else None
    before=snapshot()
    def fail(*args,**kwargs): raise RuntimeError('injected M6 persistence failure')
    monkeypatch.setattr(AuditService,'record_log',fail)
    with TestingSessionLocal() as db,pytest.raises(RuntimeError,match='injected M6'):
        if action=='generate': ReplanningService.generate(db,cid,ReplanRequest(**req))
        else: ApprovalService.approve_plan(db,pid,ApproveRequest(actor_id='actor'))
    assert snapshot()==before


@pytest.mark.parametrize('status',['UNKNOWN','INFEASIBLE','MODEL_INVALID','FEASIBLE'])
def test_m6_16_solver_semantics(client,monkeypatch,status):
    cid,_,req=finalized(client)
    original=OptimizationEngine.solve_allocation
    def solve(*args,**kwargs):
        result=original(*args,**kwargs)
        result['solver_status']=status
        result['claim_category']='SOLVER_FEASIBLE' if status=='FEASIBLE' else 'NO_OPTIMALITY_CLAIM'
        return result
    monkeypatch.setattr(OptimizationEngine,'solve_allocation',solve)
    response=generate(client,cid,req)
    assert response.status_code==200,response.text
    if status=='FEASIBLE': assert response.json()['plan_status']=='PENDING_APPROVAL'
    else:
        assert response.json()['evaluation_status']=='SOLVER_NO_SOLUTION'
        assert response.json()['allocated_kg']==0 and response.json()['unmet_demand_kg']==290
        assert response.json()['plan_id'] is None
        with TestingSessionLocal() as db: assert db.query(AllocationPlan).count()==1


def test_m6_10_restart_migration(client,tmp_path):
    cid,_,req=finalized(client)
    expected=generate(client,cid,req).json(); pid=expected['plan_id']
    path=tmp_path/'restart.sqlite3'
    with test_engine.connect() as source,sqlite3.connect(path) as destination: source.connection.driver_connection.backup(destination)
    code='''import json,sys
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import engine
cid,req,expected=json.loads(sys.argv[1])
with TestClient(app) as c:
    r=c.post(f'/cases/{cid}/replan',json=req)
    assert r.status_code==200,r.text
    assert r.json()==expected
    assert c.post(f'/cases/{cid}/replan',json=dict(req,actor_id='different')).status_code==409
    r=c.post(f"/plans/{expected['plan_id']}/approve",json={'actor_id':'actor','decision':'APPROVE'})
    assert r.status_code==200,r.text
engine.dispose()
'''
    stable=None
    for _ in range(3):
        run=subprocess.run([sys.executable,'-c',code,json.dumps([cid,req,expected])],env=dict(os.environ,PANGANNADI_DATABASE_URL='sqlite:///'+path.as_posix()),capture_output=True,text=True)
        assert run.returncode==0,run.stdout+run.stderr
        if stable is None: stable=rows(path)
        else: assert rows(path)==stable
    if os.environ.get('M6_EVIDENCE_DB'):
        target=Path(os.environ['M6_EVIDENCE_DB']).with_name('m6_restart_20261028.sqlite3')
        with sqlite3.connect(path) as source,sqlite3.connect(target) as destination: source.backup(destination)
    print('M6_RESTART=3 fresh app processes; generate/approve replay; conflict409; full table equality')


def test_m6_version_unique_and_migration(client,tmp_path,monkeypatch):
    from app.core import database
    cid,_,req=finalized(client)
    result=generate(client,cid,req).json()
    with TestingSessionLocal() as db:
        existing=db.get(AllocationPlan,result['plan_id'])
        values={c.name:getattr(existing,c.name) for c in AllocationPlan.__table__.columns}
        values['id']='duplicate'
        db.add(AllocationPlan(**values))
        with pytest.raises(IntegrityError): db.commit()
        db.rollback()
    path=tmp_path/'legacy.sqlite3'
    with test_engine.connect() as source,sqlite3.connect(path) as destination:
        source.connection.driver_connection.backup(destination)
        destination.execute('DROP TABLE replanning_events')
    before=rows(path)
    engine=create_engine('sqlite:///'+path.as_posix())
    monkeypatch.setattr(database,'engine',engine)
    for _ in range(3): database.upgrade_replanning_schema()
    after=rows(path)
    assert all(after[n]==v for n,v in before.items())
    engine.dispose()


@pytest.mark.parametrize('mode',['stale','deadline','day','source','allocation'])
def test_m6_approval_revalidates(client,mode):
    cid,_,req=finalized(client)
    pid=generate(client,cid,req).json()['plan_id']
    with TestingSessionLocal() as db:
        if mode=='stale': db.query(InventorySnapshot).filter_by(warehouse_id='WH-B').one().is_verified=False
        if mode=='deadline': db.get(DistributionCase,cid).created_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(hours=49)
        if mode=='day': db.query(ReplanningEvent).one().operational_date='2000-01-01'
        if mode=='source': db.get(Receipt,db.query(Reconciliation).one().receipt_id).receipt_status='RECORDED'
        if mode=='allocation':
            plan=db.get(AllocationPlan,pid)
            lines={l.warehouse_id:l for l in plan.lines}
            lines['WH-B'].allocated_kg=289
            lines['WH-A'].allocated_kg=1
        db.commit()
    assert decide(client,pid).status_code==409
    with TestingSessionLocal() as db:
        assert db.query(Reservation).filter_by(plan_id=pid).count()==0
        assert db.query(Approval).filter_by(plan_id=pid).count()==0


def test_m6_nonconstant_remaining(client):
    cid,_,_,payload=dispatched(client)
    payload.update(arrived_weight_kg=7900,rejected_weight_kg=0)
    receipt=receive(client,cid,payload).json()
    recon=client.post(f'/cases/{cid}/reconcile',json=final_payload(receipt)).json()
    result=generate(client,cid,dict(actor_id='actor',transaction_ref='nonconstant',source_reconciliation_id=recon['reconciliation_id']))
    assert result.status_code==200,result.text
    assert result.json()['remaining_demand_kg']==100
    assert result.json()['allocations']=={'WH-A':0,'WH-B':100}
    assert result.json()['planned_cost']==150000


def test_m6_migration_failure_rollback(client,tmp_path,monkeypatch):
    from app.core import database
    cid,_,_=finalized(client)
    path=tmp_path/'rollback.sqlite3'
    with test_engine.connect() as source,sqlite3.connect(path) as destination:
        source.connection.driver_connection.backup(destination)
        destination.execute('DROP TABLE replanning_events')
        # Remove new uniqueness to emulate pre-M6 schema using a disposable copy.
        destination.execute('DROP INDEX IF EXISTS uq_plan_case_version')
    before=rows(path)
    engine=create_engine('sqlite:///'+path.as_posix())
    monkeypatch.setattr(database,'engine',engine)
    def fail(*args,**kwargs): raise RuntimeError('injected M6 migration failure')
    monkeypatch.setattr(ReplanningEvent.__table__,'create',fail)
    with pytest.raises(RuntimeError,match='injected M6 migration'):
        database.upgrade_replanning_schema()
    engine.dispose()
    assert rows(path)==before


def test_m6_cross_ledger_keys(client):
    cid,_,req=finalized(client)
    assert generate(client,cid,dict(req,transaction_ref='SIM-DISPATCH-A')).status_code==409
    assert generate(client,cid,dict(req,transaction_ref='FINAL-1')).status_code==409
    assert generate(client,cid,req).status_code==200
    from app.models.schemas import CancellationRequest, ReconcileRequest
    from app.services.shipment_service import ShipmentService,ShipmentConflict
    from app.services.receiving_service import ReceivingService,ReceivingConflict
    with TestingSessionLocal() as db:
        sid=db.query(Shipment).first().id
        rid=db.query(Receipt).one().id
        db.rollback()
        with pytest.raises(ShipmentConflict,match='transaction_ref'):
            ShipmentService.execute(db,sid,CancellationRequest(actor_id='actor',transaction_ref=req['transaction_ref'],
                unshipped_cancelled_quantity_kg=1,cancellation_reason='test'),'CANCEL')
        with pytest.raises(ReceivingConflict,match='transaction_ref'):
            ReceivingService.execute(db,cid,ReconcileRequest(actor_id='actor',transaction_ref=req['transaction_ref'],receipt_id=rid),'FINALIZE')
