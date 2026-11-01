import hashlib
import json
import os
import subprocess
import sys
import uuid
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

if os.environ.get("PANGANNADI_PG_ACCEPTANCE") != "1":
    pytest.skip("Local disposable PostgreSQL acceptance is opt-in.", allow_module_level=True)

try:
    url = make_url(os.environ.get("PANGANNADI_DATABASE_URL", "sqlite://"))
except Exception:
    raise RuntimeError("Acceptance database URL is invalid; value withheld.") from None
if (url.drivername != "postgresql+psycopg" or url.host not in ("127.0.0.1", "localhost", "::1")
        or not url.port or not (url.database or "").startswith("pangannadi_acceptance_")
        or os.environ.get("PANGANNADI_ENV") != "test" or url.query):
    raise RuntimeError("Acceptance requires an explicit loopback disposable PostgreSQL test database.")

from app.core.database import Base, SessionLocal, engine, verify_postgres_schema
from app.core.rate_limit import DatabaseAdmission, admission_buckets
from app.models import db_models as m
from app.models.schemas import (ApproveRequest, CancellationRequest, CaseCreateRequest,
    CheckpointRequest, DispatchRequest, ReceiptRequest, ReconcileRequest, ReplanRequest)
from app.services.approval_service import ApprovalService, InsufficientCapacityConflictError
from app.services.case_service import CaseService
from app.services.receiving_service import ReceivingService
from app.services.replanning_service import ReplanningService
from app.services.shipment_service import ShipmentService, ShipmentConflict, record_checkpoint, warehouse_state


def call(fn, *args, **kwargs):
    with SessionLocal() as db:
        return fn(db, *args, **kwargs)


def state():
    with engine.connect() as conn:
        return {table.name: sorted((dict(row) for row in conn.execute(table.select()).mappings()),
            key=lambda row: json.dumps(row, default=str, sort_keys=True))
            for table in Base.metadata.sorted_tables}


def evidence(name, value):
    directory = os.environ.get("PANGANNADI_PG_EVIDENCE_DIR")
    if directory:
        encoded = json.dumps(value, default=str, sort_keys=True, indent=2).encode()
        path = Path(directory) / (name + ".json")
        path.write_bytes(encoded)
        path.with_suffix(".sha256").write_text(hashlib.sha256(encoded).hexdigest() + "\n", encoding="ascii")


@pytest.fixture(autouse=True)
def disposable_database(request):
    from app.core import database
    original = database.engine
    fresh_url = url.set(database="pangannadi_acceptance_" + uuid.uuid4().hex[:16])
    with original.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql('CREATE DATABASE "' + fresh_url.database + '"')
    env = dict(os.environ, PANGANNADI_DATABASE_URL=fresh_url.render_as_string(hide_password=False))
    result = subprocess.run([sys.executable, "provision_postgres.py", "--confirm-new-empty-database"],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True)
    assert result.returncode == 0, "Fresh database explicit provisioning failed; output withheld."
    target = create_engine(fresh_url, echo=False)
    SessionLocal.configure(bind=target)
    database.engine = target
    globals()["engine"] = target
    try:
        verify_postgres_schema(target)
        yield
        evidence(request.node.name, state())
    finally:
        target.dispose()
        SessionLocal.configure(bind=original)
        database.engine = original
        globals()["engine"] = original


def payload(demand=8000):
    return CaseCreateRequest(commodity_name="Cabai Rawit Merah", destination_region="Pasar Induk Surabaya",
        verified_demand=demand, is_demand_verified=True, deadline_hours=48, created_by="PG-planner",
        warehouses=[dict(warehouse_id="WH-A", physical_stock=35000, local_demand=20000,
            safety_stock=5000, daily_dispatch_capacity=6000, cost_per_kg=1200),
            dict(warehouse_id="WH-B", physical_stock=22000, local_demand=15000,
            safety_stock=3000, daily_dispatch_capacity=4000, cost_per_kg=1500)])


def planned(demand=8000):
    with SessionLocal() as db:
        case = CaseService.create_case(db, payload(demand))
        cid = case.id
    assert call(CaseService.validate_gate, cid)["is_ready"]
    result = call(CaseService.optimize_case, cid)
    return cid, result["plan_id"]


def approve(pid):
    return call(ApprovalService.approve_plan, pid, ApproveRequest(actor_id="PG-approver"))


def approved():
    cid, pid = planned()
    approve(pid)
    with SessionLocal() as db:
        ships = {s.warehouse_id: s.id for s in db.query(m.Shipment).filter_by(plan_id=pid)}
    return cid, pid, ships


def dispatch(sid, warehouse, key):
    return call(ShipmentService.execute, sid, DispatchRequest(actor_id="PG-dispatcher", transaction_ref=key,
        planned_quantity_kg=6000 if warehouse == "A" else 2000,
        actual_dispatched_quantity_kg=6000 if warehouse == "A" else 1950))


def cancel(sid, key="cancel-B"):
    return call(ShipmentService.execute, sid, CancellationRequest(actor_id="PG-dispatcher", transaction_ref=key,
        unshipped_cancelled_quantity_kg=50, cancellation_reason="Explicit unshipped cancellation"), action="CANCEL")


def dispatched():
    cid, pid, ships = approved()
    dispatch(ships["WH-A"], "A", "dispatch-A")
    dispatch(ships["WH-B"], "B", "dispatch-B")
    cancel(ships["WH-B"])
    req = ReceiptRequest(inspector_id="PG-inspector", transaction_ref="receive", plan_id=pid,
        shipment_ids=list(ships.values()), total_dispatched_kg=7950, arrived_weight_kg=7820,
        rejected_weight_kg=110)
    return cid, pid, ships, req


def parallel(*operations):
    barrier = Barrier(len(operations))
    def run(operation):
        with SessionLocal() as db:
            connection_id = db.execute(text("SELECT pg_backend_pid()")).scalar_one()
            db.rollback()
            barrier.wait(timeout=20)
            try:
                return connection_id, "ok", operation(db)
            except (InsufficientCapacityConflictError, ShipmentConflict) as exc:
                return connection_id, type(exc).__name__, None
    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        results = list(pool.map(run, operations))
    assert len({r[0] for r in results}) == len(operations)
    return results


@pytest.mark.parametrize("scope,total,expected", [("account", 60, 60), ("global", 72, 1)])
def test_admission_atomic_concurrent_attempts(scope, total, expected):
    target = create_engine(engine.url, pool_size=total, max_overflow=0, echo=False)
    try:
        with target.connect() as conn:
            second = int(conn.scalar(text("SELECT EXTRACT(EPOCH FROM clock_timestamp())"))) % 60
        if second > 45:
            time.sleep(61 - second)
        limiter = DatabaseAdmission(target)
        if scope == 'global':
            assert limiter.consume('seed')[0]
            with target.begin() as conn:
                conn.execute(admission_buckets.update().where(admission_buckets.c.bucket == 'global').values(attempts=599))
        barrier = Barrier(total)
        def attempt(i):
            barrier.wait(timeout=30)
            return limiter.consume("same-account" if scope == "account" else "account-" + str(i))
        with ThreadPoolExecutor(max_workers=total) as pool:
            results = list(pool.map(attempt, range(total)))
        assert sum(allowed for allowed, retry in results) == expected
        assert all(1 <= retry <= 60 for allowed, retry in results)
        with target.connect() as conn:
            rows = conn.execute(admission_buckets.select()).mappings().all()
        assert next(row["attempts"] for row in rows if row["bucket"] == "global") == (601 if scope == 'global' else total)
        assert len(rows) == 1
        evidence("limiter-" + scope, dict(total=total, allowed=expected, rows=[dict(row) for row in rows]))
    finally:
        target.dispose()


def test_admission_window_rollover_and_shared_instances():
    first, second = DatabaseAdmission(engine), DatabaseAdmission(engine)
    assert all(first.consume("account")[0] for _ in range(60))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: first.record_failure('account'), range(24)))
    assert not second.consume("account")[0]
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT attempts FROM operator_admission_buckets WHERE bucket LIKE 'failure:%'")) == 11
    with engine.begin() as conn:
        conn.execute(admission_buckets.update().values(window=-1))
    assert second.consume("account")[0]
    with engine.connect() as conn:
        rows = conn.execute(admission_buckets.select()).all()
        assert next(row.attempts for row in rows if row.bucket == 'global') == 1
        assert next(row.window for row in rows if row.bucket.startswith('failure:')) == -1


def test_admission_real_asgi_valid_fanout_and_failures(monkeypatch):
    import base64
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from unittest.mock import patch
    from app.core.security import OperatorPerimeter, password_hash
    account = json.dumps({'review-operator': {'role': 'operator', 'password_hash': password_hash('test-only-password')}})
    app = FastAPI()
    @app.get('/probe')
    def probe():
        return {'ok': True}
    limiter = DatabaseAdmission(engine)
    with engine.connect() as conn:
        window, _ = limiter.window(conn)
    real_window = limiter.window
    def fixed_test_window(conn):
        real_window(conn)  # Keep real PostgreSQL timeout setup; rollover is tested separately.
        return window, 60
    monkeypatch.setattr(limiter, 'window', fixed_test_window)
    app.add_middleware(OperatorPerimeter, origins=['https://stage.example.test'], admission=limiter)
    good = {'Authorization': 'Basic ' + base64.b64encode(b'review-operator:test-only-password').decode()}
    bad = {'Authorization': 'Basic ' + base64.b64encode(b'review-operator:incorrect').decode()}
    with patch.dict(os.environ, {'PANGANNADI_OPERATOR_ACCOUNTS': account}), TestClient(app) as client:
        assert all(client.get('/probe', headers=good).status_code == 200 for _ in range(11))
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(lambda _: client.get('/probe', headers=good).status_code, range(60)))
        assert statuses == [200] * 60
        assert all(client.get('/probe', headers=bad).status_code == 401 for _ in range(10))
        with patch.object(OperatorPerimeter, 'verify', side_effect=AssertionError('hash must not run')):
            response = client.get('/probe', headers=good)
        assert response.status_code == 429
        assert 'Retry-After' in response.headers


def test_schema_actual_constraints_and_provision_refusal():
    from provision_postgres import provision_postgres
    verify_postgres_schema()
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        actual = inspector.get_foreign_keys(table.name)
        for fk in table.foreign_key_constraints:
            assert any(set(item["constrained_columns"]) == {c.name for c in fk.columns}
                and item["referred_table"] == fk.referred_table.name for item in actual)
        uniques = inspector.get_unique_constraints(table.name)
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                assert any(set(item["column_names"]) == {c.name for c in constraint.columns} for item in uniques)
        checks = inspector.get_check_constraints(table.name)
        expected = [c for c in table.constraints if isinstance(c, CheckConstraint)
            and "typeof(" not in str(c.sqltext)]
        assert len(checks) == len(expected)
    before = state()
    with pytest.raises(RuntimeError):
        provision_postgres(engine, confirmed=True)
    assert state() == before


@pytest.mark.parametrize("kind", ["foreign-key", "check", "unique"])
def test_schema_verification_refuses_missing_constraint(kind):
    inspector = inspect(engine)
    if kind == "foreign-key":
        constraint = inspector.get_foreign_keys("shipments")[0]["name"]
    elif kind == "check":
        constraint = inspector.get_check_constraints("shipments")[0]["name"]
    else:
        constraint = next(c["name"] for c in inspector.get_unique_constraints("shipments")
            if c["column_names"] == ["plan_id", "warehouse_id"])
    with engine.begin() as conn:
        conn.exec_driver_sql('ALTER TABLE shipments DROP CONSTRAINT "' + constraint + '"')
    try:
        with pytest.raises(RuntimeError, match="incomplete"):
            verify_postgres_schema()
    finally:
        with engine.begin() as conn:
            if kind == "foreign-key":
                fk = next(c for c in inspector.get_foreign_keys("shipments") if c["name"] == constraint)
                columns = ', '.join('"' + c + '"' for c in fk["constrained_columns"])
                referred = ', '.join('"' + c + '"' for c in fk["referred_columns"])
                conn.exec_driver_sql(f'ALTER TABLE shipments ADD CONSTRAINT "{constraint}" FOREIGN KEY ({columns}) REFERENCES "{fk["referred_table"]}" ({referred})')
            elif kind == "check":
                check = next(c for c in inspector.get_check_constraints("shipments") if c["name"] == constraint)
                conn.exec_driver_sql(f'ALTER TABLE shipments ADD CONSTRAINT "{constraint}" CHECK ({check["sqltext"]})')
            else:
                conn.exec_driver_sql(f'ALTER TABLE shipments ADD CONSTRAINT "{constraint}" UNIQUE (plan_id, warehouse_id)')
    verify_postgres_schema()


@pytest.mark.parametrize("violation", ["foreign-key", "check", "unique"])
def test_database_rejects_invalid_writes(violation):
    _, pid, ships = approved()
    with SessionLocal() as db:
        if violation == "foreign-key":
            operation = lambda: db.execute(text("UPDATE shipments SET warehouse_id='absent' WHERE id=:id"), {"id": ships["WH-A"]})
        elif violation == "check":
            operation = lambda: db.execute(text("UPDATE shipments SET actual_dispatched_quantity_kg=-1 WHERE id=:id"), {"id": ships["WH-A"]})
        else:
            operation = lambda: db.execute(text("UPDATE shipments SET warehouse_id='WH-A' WHERE id=:id"), {"id": ships["WH-B"]})
        before = state()
        with pytest.raises(IntegrityError):
            operation()
            db.commit()
        db.rollback()
    assert state() == before


def test_transaction_rollback_without_mock():
    before = state()
    with SessionLocal() as db:
        db.add(m.Warehouse(id="rollback", name="rollback", location_name="local", base_cost_per_kg=1,
            daily_dispatch_capacity_kg=1))
        db.flush()
        with pytest.raises(IntegrityError):
            db.execute(text("INSERT INTO dispatch_usage VALUES ('absent', '2026-10-30', 1)"))
        db.rollback()
    assert state() == before


def test_parallel_double_approval():
    _, pid = planned()
    req = ApproveRequest(actor_id="PG-approver")
    results = parallel(*[lambda db: ApprovalService.approve_plan(db, pid, req) for _ in range(2)])
    assert [r[1] for r in results] == ["ok", "ok"]
    assert results[0][2] == results[1][2]
    with SessionLocal() as db:
        assert db.query(m.Approval).count() == 1
        assert db.query(m.Reservation).count() == db.query(m.Shipment).count() == 2


def test_parallel_competing_plan_reservations():
    _, first = planned()
    _, second = planned()
    req = ApproveRequest(actor_id="PG-approver")
    results = parallel(lambda db: ApprovalService.approve_plan(db, first, req),
        lambda db: ApprovalService.approve_plan(db, second, req))
    assert sorted(r[1] for r in results) == ["InsufficientCapacityConflictError", "ok"]
    with SessionLocal() as db:
        assert db.query(m.Approval).count() == 1
        assert sum(r.reserved_stock_kg for r in db.query(m.Reservation)) == 8000


def test_parallel_crossledger_global_key():
    _, _, ships = approved()
    dispatch(ships["WH-A"], "A", "dispatch-A")
    checkpoint = CheckpointRequest(actor_id="PG-driver", transaction_ref="global-key", event_type="IN_TRANSIT")
    departure = DispatchRequest(actor_id="PG-dispatcher", transaction_ref="global-key",
        planned_quantity_kg=2000, actual_dispatched_quantity_kg=1950)
    results = parallel(lambda db: record_checkpoint(db, ships["WH-A"], checkpoint),
        lambda db: ShipmentService.execute(db, ships["WH-B"], departure))
    assert sum(r[1] == "ok" for r in results) == 1
    with SessionLocal() as db:
        assert db.query(m.ShipmentCheckpoint).filter_by(transaction_ref="global-key").count() + db.query(
            m.ShipmentEvent).filter_by(transaction_ref="global-key").count() == 1


def test_committed_response_lost_replay():
    _, _, ships = approved()
    first = dispatch(ships["WH-A"], "A", "dispatch-A")
    dispatch(ships["WH-B"], "B", "dispatch-B")
    cancellation = cancel(ships["WH-B"])
    before = state()
    engine.dispose()
    assert dispatch(ships["WH-A"], "A", "dispatch-A") == first
    assert cancel(ships["WH-B"]) == cancellation
    assert state() == before


@pytest.mark.parametrize("different_key", [False, True])
def test_parallel_receiving_and_finalization(different_key):
    cid, _, _, req = dispatched()
    from app.services.receiving_service import ReceivingConflict
    requests = [req, req.model_copy(update={"transaction_ref": "receive-other"}) if different_key else req]
    if different_key:
        def receive(db, request):
            try:
                return ReceivingService.execute(db, cid, request)
            except ReceivingConflict:
                return None
        results = parallel(*[lambda db, request=request: receive(db, request) for request in requests])
        assert sum(r[2] is not None for r in results) == 1
        receipt = next(r[2] for r in results if r[2] is not None)
    else:
        results = parallel(*[lambda db: ReceivingService.execute(db, cid, req) for _ in range(2)])
        assert [r[1] for r in results] == ["ok", "ok"]
        assert results[0][2] == results[1][2]
        receipt = results[0][2]
    final = ReconcileRequest(actor_id="PG-finalizer", transaction_ref="finalize", receipt_id=receipt["receipt_id"])
    results = parallel(*[lambda db: ReceivingService.execute(db, cid, final, action="RECONCILE") for _ in range(2)])
    assert [r[1] for r in results] == ["ok", "ok"]
    assert results[0][2] == results[1][2]
    with SessionLocal() as db:
        assert db.query(m.Receipt).count() == db.query(m.Reconciliation).count() == 1
        assert db.query(m.ReceivingEvent).count() == 2


def test_full_oracle_recovery_reserved_not_dispatched():
    cid, pid, ships, req = dispatched()
    checkpoint = call(record_checkpoint, ships["WH-A"], CheckpointRequest(actor_id="PG-driver",
        transaction_ref="checkpoint", event_type="AT_CHECKPOINT", note="Acceptance checkpoint"))
    receipt = call(ReceivingService.execute, cid, req)
    assert receipt["sound_weight_kg"] == 7710
    final = call(ReceivingService.execute, cid, ReconcileRequest(actor_id="PG-finalizer",
        transaction_ref="finalize", receipt_id=receipt["receipt_id"]), action="RECONCILE")
    assert (final["total_dispatched_kg"], final["total_arrived_kg"], final["total_rejected_kg"],
        final["total_sound_kg"], final["unmet_demand_kg"]) == (7950, 7820, 110, 7710, 290)
    before = state()
    v1_tables = ("allocation_plans", "allocation_lines", "approvals", "shipments", "reservations")
    recovery = call(ReplanningService.generate, cid, ReplanRequest(actor_id="PG-planner", transaction_ref="replan",
        source_reconciliation_id=final["reconciliation_id"]))
    assert (recovery["remaining_demand_kg"], recovery["allocated_kg"], recovery["planned_cost"]) == (290, 290, 435000)
    assert recovery["allocations"] == {"WH-A": 0, "WH-B": 290}
    approval = approve(recovery["plan_id"])
    after = state()
    for table in v1_tables:
        ids = {row["id"] for row in before[table]}
        assert [row for row in after[table] if row["id"] in ids] == before[table]
    with SessionLocal() as db:
        case = db.get(m.DistributionCase, cid)
        assert case.status == "RECOVERY_RESERVED"
        shipments = db.query(m.Shipment).filter_by(plan_id=recovery["plan_id"]).all()
        assert len(shipments) == 1
        assert shipments[0].manifest_status == "READY_TO_SHIP"
        assert shipments[0].actual_dispatched_quantity_kg == 0
        assert shipments[0].dispatch_timestamp is None
        assert db.query(m.Reservation).filter_by(plan_id=recovery["plan_id"]).one().reserved_stock_kg == 290
        assert db.query(m.AuditLog).filter_by(action_type="DISPATCH_CONFIRMED").count() == 2
        assert db.query(m.AuditLog).filter_by(action_type="UNSHIPPED_RESERVATION_CANCELLED").count() == 1
        assert db.query(m.ShipmentEvent).count() == 3
        states = {w: warehouse_state(db, w) for w in ships}
        assert [states[w]["physical_stock"] for w in ("WH-A", "WH-B")] == [29000, 20050]
        assert [states[w]["actual_dispatched_today"] for w in ("WH-A", "WH-B")] == [6000, 1950]
        assert states["WH-B"]["active_stock_reservations"] == 290
        assert states["WH-B"]["remaining_dispatch_capacity"] == 1760
    evidence("oracle", dict(case_id=cid, plan_v1=pid, checkpoint=checkpoint, receipt=receipt,
        reconciliation=final, recovery=recovery, approval=approval, warehouse_states=states, full_state=after))
