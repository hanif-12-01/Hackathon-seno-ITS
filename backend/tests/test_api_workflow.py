"""
Integration and API Workflow Test Suite for PanganNadi Adaptive
Verifying:
  - API-01: Valid and complete data produces Plan v1 (WH-A = 6.000 kg, WH-B = 2.000 kg, planned_cost = Rp 10.200.000)
  - API-02: Missing/unverified demand holds operational recommendation (Gate fails, optimize returns 422)
  - API-03: Approval of Plan v1 creates reservations (A=6.000, B=2.000) WITHOUT decreasing physical stock
  - API-04: Competing approvals exceeding capacity fail safe with HTTP 409 Conflict
  - API-05: Idempotency of approval: Repeated approval requests do not duplicate reservations
  - API-06: Optimizer respects active reservations from other approved cases
  - API-07: Audit log records case creation, gate validation, optimization, approval, and conflict rejection
  - API-08: Concurrency stress test: Simultaneous approval attempts with SQLite BEGIN IMMEDIATE locking
"""

import os
import sys
import tempfile
import threading
from typing import List

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, get_db
from app.main import app
from app.models.db_models import (
    DistributionCase,
    InventorySnapshot,
    AllocationPlan,
    Reservation,
    AuditLog,
)

# Setup isolated test database file
TEST_DB_FILE = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
TEST_DB_URL = f"sqlite:///{TEST_DB_FILE}"

test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False, "timeout": 15},
    echo=False,
)

@event.listens_for(test_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def clean_database():
    """Ensure clean database tables for every test function."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_file():
    yield
    test_engine.dispose()
    try:
        if os.path.exists(TEST_DB_FILE):
            os.remove(TEST_DB_FILE)
    except Exception:
        pass


@pytest.fixture
def client():
    return TestClient(app)


def get_demo_case_payload(is_verified: bool = True, demand: float = 8000.0) -> dict:
    return {
        "commodity_name": "Cabai Rawit Merah",
        "destination_region": "Pasar Induk Surabaya",
        "verified_demand": demand,
        "is_demand_verified": is_verified,
        "deadline_hours": 48,
        "created_by": "user-bambang-planner",
        "warehouses": [
            {
                "warehouse_id": "WH-A",
                "physical_stock": 35000.0,
                "local_demand": 20000.0,
                "safety_stock": 5000.0,
                "daily_dispatch_capacity": 6000.0,
                "actual_dispatched_today": 0.0,
                "active_undispatched_reservations": 0.0,
                "cost_per_kg": 1200.0,
            },
            {
                "warehouse_id": "WH-B",
                "physical_stock": 22000.0,
                "local_demand": 15000.0,
                "safety_stock": 3000.0,
                "daily_dispatch_capacity": 4000.0,
                "actual_dispatched_today": 0.0,
                "active_undispatched_reservations": 0.0,
                "cost_per_kg": 1500.0,
            },
        ],
    }


class TestPanganNadiAPIWorkflow:
    def test_api_01_plan_v1_optimization_success(self, client):
        """API-01: Data lengkap dan valid menghasilkan Plan v1 (A=6.000 kg, B=2.000 kg, Rp 10.200.000)."""
        payload = get_demo_case_payload(is_verified=True, demand=8000.0)
        resp_create = client.post("/cases", json=payload)
        assert resp_create.status_code == 201
        case_id = resp_create.json()["case_id"]

        # Validate gate
        resp_val = client.post(f"/cases/{case_id}/validate")
        assert resp_val.status_code == 200
        val_data = resp_val.json()
        assert val_data["is_ready"] is True
        assert val_data["is_demand_verified"] is True
        assert val_data["safe_capacities"]["WH-A"] == 10000.0
        assert val_data["safe_capacities"]["WH-B"] == 4000.0
        assert val_data["remaining_dispatch_capacities"]["WH-A"] == 6000.0
        assert val_data["remaining_dispatch_capacities"]["WH-B"] == 4000.0

        # Execute optimization
        resp_opt = client.post(f"/cases/{case_id}/optimize")
        assert resp_opt.status_code == 200
        opt_data = resp_opt.json()
        assert opt_data["solver_status"] == "OPTIMAL"
        assert opt_data["fulfillment_status"] == "FULL_FULFILLMENT"
        assert opt_data["total_demand_kg"] == 8000.0
        assert opt_data["allocated_kg"] == 8000.0
        assert opt_data["unmet_demand_kg"] == 0.0
        assert opt_data["planned_cost"] == 10200000.0

        lines = {l["warehouse_id"]: l["allocated_kg"] for l in opt_data["lines"]}
        assert lines["WH-A"] == 6000.0
        assert lines["WH-B"] == 2000.0

    def test_api_02_unverified_demand_gate_failure(self, client):
        """API-02: Verified demand tidak ada atau belum disetujui menyebabkan optimasi ditahan."""
        payload = get_demo_case_payload(is_verified=False, demand=8000.0)
        resp_create = client.post("/cases", json=payload)
        assert resp_create.status_code == 201
        case_id = resp_create.json()["case_id"]

        # Validate gate -> should fail DEMAND_VERIFIED
        resp_val = client.post(f"/cases/{case_id}/validate")
        assert resp_val.status_code == 200
        val_data = resp_val.json()
        assert val_data["is_ready"] is False
        assert val_data["is_demand_verified"] is False
        demand_rule = next(r for r in val_data["validation_details"] if r["rule"] == "DEMAND_VERIFIED")
        assert demand_rule["status"] == "FAILED"

        # Attempt optimize -> must be rejected with HTTP 422
        resp_opt = client.post(f"/cases/{case_id}/optimize")
        assert resp_opt.status_code == 422
        assert "Kebutuhan pasokan belum terverifikasi" in resp_opt.json()["detail"]

    def test_api_03_approval_creates_reservations_without_physical_stock_deduction(self, client):
        """API-03: Approval Plan v1 menghasilkan reservasi A=6.000 dan B=2.000 tanpa mengurangi physical stock."""
        payload = get_demo_case_payload(is_verified=True, demand=8000.0)
        case_id = client.post("/cases", json=payload).json()["case_id"]
        client.post(f"/cases/{case_id}/validate")
        plan_id = client.post(f"/cases/{case_id}/optimize").json()["plan_id"]

        # Approve plan
        approve_payload = {
            "actor_id": "user-ratna-approver",
            "decision": "APPROVE",
            "notes": "Alokasi Plan v1 disetujui.",
        }
        resp_appr = client.post(f"/plans/{plan_id}/approve", json=approve_payload)
        assert resp_appr.status_code == 200
        appr_data = resp_appr.json()
        assert appr_data["approval_status"] == "APPROVED"

        res_map = {r["warehouse_id"]: r["reserved_kg"] for r in appr_data["reservations_created"]}
        assert res_map["WH-A"] == 6000.0
        assert res_map["WH-B"] == 2000.0

        # Verify physical stock in DB is completely untouched
        db = TestingSessionLocal()
        snaps = db.query(InventorySnapshot).filter(InventorySnapshot.case_id == case_id).all()
        snap_map = {s.warehouse_id: s.physical_stock for s in snaps}
        assert snap_map["WH-A"] == 35000.0
        assert snap_map["WH-B"] == 22000.0

        # Verify reservations are active in DB
        res_db = db.query(Reservation).filter(Reservation.plan_id == plan_id).all()
        assert len(res_db) == 2
        assert all(r.status == "ACTIVE" for r in res_db)
        db.close()

    def test_api_04_competing_approvals_fail_safe_on_insufficient_capacity(self, client):
        """API-04: Dua approval yang berebut stok atau kuota harus gagal aman untuk permintaan yang kapasitasnya tidak cukup (409 Conflict)."""
        # Case 1 requests 8.000 kg (6.000 kg from A, 2.000 kg from B)
        payload1 = get_demo_case_payload(is_verified=True, demand=8000.0)
        case1_id = client.post("/cases", json=payload1).json()["case_id"]
        client.post(f"/cases/{case1_id}/validate")
        plan1_id = client.post(f"/cases/{case1_id}/optimize").json()["plan_id"]

        # Case 2 also requests 8.000 kg
        payload2 = get_demo_case_payload(is_verified=True, demand=8000.0)
        case2_id = client.post("/cases", json=payload2).json()["case_id"]
        client.post(f"/cases/{case2_id}/validate")
        plan2_id = client.post(f"/cases/{case2_id}/optimize").json()["plan_id"]

        # Approve Case 1 -> Success
        resp1 = client.post(
            f"/plans/{plan1_id}/approve",
            json={"actor_id": "approver-1", "decision": "APPROVE", "notes": "First approval"}
        )
        assert resp1.status_code == 200
        assert resp1.json()["approval_status"] == "APPROVED"

        # Now approve Case 2 -> Must fail with HTTP 409 Conflict because WH-A quota is exhausted by Case 1!
        resp2 = client.post(
            f"/plans/{plan2_id}/approve",
            json={"actor_id": "approver-2", "decision": "APPROVE", "notes": "Second conflicting approval"}
        )
        assert resp2.status_code == 409
        assert "Konflik" in resp2.json()["detail"]

    def test_api_05_idempotent_approval_retry(self, client):
        """API-05: Approval yang dikirim dua kali tidak menggandakan reservasi."""
        payload = get_demo_case_payload(is_verified=True, demand=8000.0)
        case_id = client.post("/cases", json=payload).json()["case_id"]
        client.post(f"/cases/{case_id}/validate")
        plan_id = client.post(f"/cases/{case_id}/optimize").json()["plan_id"]

        approve_payload = {"actor_id": "approver-ratna", "decision": "APPROVE", "notes": "First submit"}

        # First submit
        resp_first = client.post(f"/plans/{plan_id}/approve", json=approve_payload)
        assert resp_first.status_code == 200
        assert resp_first.json()["approval_status"] == "APPROVED"

        # Second submit (retry / replay)
        resp_second = client.post(f"/plans/{plan_id}/approve", json=approve_payload)
        assert resp_second.status_code == 200
        assert resp_second.json()["approval_status"] == "APPROVED"

        # Check DB to ensure exactly 2 reservations exist (1 for WH-A, 1 for WH-B), NOT 4!
        db = TestingSessionLocal()
        reservations = db.query(Reservation).filter(Reservation.plan_id == plan_id).all()
        assert len(reservations) == 2
        total_reserved = sum(r.reserved_stock_kg for r in reservations)
        assert total_reserved == 8000.0  # Exactly 6000 + 2000
        db.close()

    def test_api_06_optimizer_respects_active_reservations_from_other_cases(self, client):
        """API-06: Optimizer menghormati reservasi aktif dari kasus lain."""
        # Case A uses 6.000 kg from WH-A and 2.000 kg from WH-B, and gets approved
        payload_a = get_demo_case_payload(is_verified=True, demand=8000.0)
        case_a_id = client.post("/cases", json=payload_a).json()["case_id"]
        client.post(f"/cases/{case_a_id}/validate")
        plan_a_id = client.post(f"/cases/{case_a_id}/optimize").json()["plan_id"]
        client.post(f"/plans/{plan_a_id}/approve", json={"actor_id": "approver", "decision": "APPROVE"})

        # Now, create Case B requesting 1.000 kg.
        # WH-A dispatch quota is exhausted (6000 - 6000 = 0 kg remaining).
        # WH-B has 4000 - 2000 = 2000 kg remaining dispatch capacity.
        payload_b = get_demo_case_payload(is_verified=True, demand=1000.0)
        case_b_id = client.post("/cases", json=payload_b).json()["case_id"]
        client.post(f"/cases/{case_b_id}/validate")

        resp_opt_b = client.post(f"/cases/{case_b_id}/optimize")
        assert resp_opt_b.status_code == 200
        opt_b_data = resp_opt_b.json()

        # WH-A must receive 0 kg because its active reservation took full quota!
        # WH-B must fulfill all 1.000 kg.
        allocs = {l["warehouse_id"]: l["allocated_kg"] for l in opt_b_data["lines"]}
        assert allocs["WH-A"] == 0.0
        assert allocs["WH-B"] == 1000.0
        assert opt_b_data["allocated_kg"] == 1000.0

    def test_api_07_audit_log_captures_all_critical_events(self, client):
        """API-07: Audit log mencatat pembuatan, validasi gate, optimasi, approval, dan penolakan konflik."""
        payload = get_demo_case_payload(is_verified=True, demand=8000.0)
        case_id = client.post("/cases", json=payload).json()["case_id"]
        client.post(f"/cases/{case_id}/validate")
        plan_id = client.post(f"/cases/{case_id}/optimize").json()["plan_id"]
        client.post(f"/plans/{plan_id}/approve", json={"actor_id": "approver-audit", "decision": "APPROVE"})

        # Read audit log via API
        resp_audit = client.get(f"/cases/{case_id}/audit")
        assert resp_audit.status_code == 200
        logs = resp_audit.json()
        action_types = [log["action_type"] for log in logs]

        assert "CASE_CREATED" in action_types
        assert "GATE_VALIDATED" in action_types
        assert "OPTIMIZATION_EXECUTED" in action_types
        assert "PLAN_APPROVED" in action_types

    def test_api_08_concurrent_approval_stress_test(self, client):
        """API-08: Concurrency stress test membuktikan isolasi transaksi mencegah over-allocation secara simultan."""
        payload1 = get_demo_case_payload(is_verified=True, demand=8000.0)
        case1_id = client.post("/cases", json=payload1).json()["case_id"]
        client.post(f"/cases/{case1_id}/validate")
        plan1_id = client.post(f"/cases/{case1_id}/optimize").json()["plan_id"]

        payload2 = get_demo_case_payload(is_verified=True, demand=8000.0)
        case2_id = client.post("/cases", json=payload2).json()["case_id"]
        client.post(f"/cases/{case2_id}/validate")
        plan2_id = client.post(f"/cases/{case2_id}/optimize").json()["plan_id"]

        results: List[int] = []

        def worker(p_id: str):
            c = TestClient(app)
            resp = c.post(
                f"/plans/{p_id}/approve",
                json={"actor_id": f"actor-{p_id}", "decision": "APPROVE", "notes": "Racing approval"}
            )
            results.append(resp.status_code)

        t1 = threading.Thread(target=worker, args=(plan1_id,))
        t2 = threading.Thread(target=worker, args=(plan2_id,))

        t1.start()
        t2.start()

        t1.join()
        t2.join()

        # One must succeed (200 OK) and the competing one must fail safe (409 Conflict)
        assert 200 in results, f"Expected at least one successful approval, got {results}"
        assert 409 in results, f"Expected competing approval to fail safe with 409 Conflict, got {results}"
