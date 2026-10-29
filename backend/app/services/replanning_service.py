"""Single-cycle recovery; final reconciliation and shared capacity are authoritative."""
import json
import math
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy.exc import IntegrityError, OperationalError
from app.models.db_models import (DistributionCase, AllocationPlan, AllocationLine, Receipt,
    Reconciliation, ReplanningEvent, Shipment, ShipmentEvent, ReceivingEvent, ShipmentCheckpoint)
from app.services.capacity import capacity, current_snapshot, operational_day
from app.services.gate import DataReadinessGate
from app.services.optimizer import OptimizationEngine
from app.services.audit_service import AuditService


class ReplanningConflict(Exception):
    pass


def recovery_context(db, case_id, source_id=None, approval=False):
    case = db.get(DistributionCase, case_id)
    if case is None:
        raise LookupError("Kasus tidak ditemukan.")
    recon = db.query(Reconciliation).filter_by(case_id=case_id).one_or_none()
    if recon is None or (source_id is not None and recon.id != source_id):
        raise ReplanningConflict("Reconciliation sumber final tidak tersedia/cocok.")
    receipt = db.get(Receipt, recon.receipt_id)
    parent = db.get(AllocationPlan, receipt.plan_id) if receipt else None
    if (receipt is None or receipt.case_id != case_id or receipt.receipt_status != "FINALIZED"
        or recon.finalized_at is None or parent is None or parent.case_id != case_id
        or parent.version != 1 or parent.approval_status != "APPROVED"):
        raise ReplanningConflict("Receipt/reconciliation/Plan v1 tidak valid atau belum final.")
    demand = case.verified_demand
    if (not case.is_demand_verified or not math.isfinite(demand) or demand < 0
        or demand > 1_000_000_000 or int(demand) != demand or demand != recon.target_demand_kg
        or recon.total_sound_kg != receipt.sound_weight_kg
        or recon.unmet_demand_kg != max(0, int(demand) - receipt.sound_weight_kg)):
        raise ReplanningConflict("Verified demand dan reconciliation tidak konsisten.")
    remaining = recon.unmet_demand_kg
    allowed = ("RECOVERY_PLAN_PENDING_APPROVAL",) if approval else ("REPLAN_AVAILABLE", "RECONCILED")
    if case.status not in allowed or (remaining > 0 and not approval and case.status != "REPLAN_AVAILABLE"):
        raise ReplanningConflict("State kasus tidak mengizinkan recovery.")
    if remaining == 0:
        return case, recon, parent, []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if case.deadline_hours <= 0 or not case.created_at <= now < case.created_at + timedelta(hours=case.deadline_hours):
        raise ReplanningConflict("Deadline recovery telah berakhir atau timestamp kasus tidak valid.")
    dispatched = db.query(Shipment).filter_by(plan_id=parent.id).filter(Shipment.actual_dispatched_quantity_kg > 0).all()
    if not dispatched or any(s.operational_date != operational_day() or s.manifest_status != "CLOSED" for s in dispatched):
        raise ReplanningConflict("Recovery MVP wajib pada hari dispatch v1 yang sama dan manifest closed.")
    originals = case.snapshots
    snapshots = [current_snapshot(db, s.warehouse_id) for s in sorted(originals, key=lambda s:s.warehouse_id)]
    if (not originals or any(s is None for s in snapshots)
        or not DataReadinessGate.validate_case(case, originals)["is_ready"]
        or not DataReadinessGate.validate_case(case, snapshots)["is_ready"]):
        raise ReplanningConflict("Data Readiness Gate Failed: inventory stale/unverified/invalid.")
    warehouses = []
    for snapshot in snapshots:
        values = capacity(db, snapshot)
        # Validate raw values before calculate_bounds can truncate integer kilograms.
        for key, value in values.items():
            if key in ("id", "cost_per_kg"):
                continue
            if not math.isfinite(value) or value < 0 or int(value) != value:
                raise ReplanningConflict("Current capacity wajib integer kilogram non-negatif.")
        cost = values["cost_per_kg"]
        if not math.isfinite(cost) or not 0 < cost <= 1_000_000_000 or Decimal(str(cost))*100 != (Decimal(str(cost))*100).to_integral_value():
            raise ReplanningConflict("Current transportation cost tidak valid.")
        warehouses.append({**values, "snapshot_id": snapshot.id, "snapshot_time": snapshot.snapshot_time.isoformat(),
            "external_reservations_kg": snapshot.external_reservations,
            **OptimizationEngine.calculate_bounds(values)})
    return case, recon, parent, warehouses


def eligibility(db, case_id):
    if db.get(DistributionCase, case_id) is None:
        raise LookupError("Kasus tidak ditemukan.")
    try:
        case, recon, parent, warehouses = recovery_context(db, case_id)
        existing = db.query(AllocationPlan).filter_by(case_id=case_id, version=2).first()
        if existing:
            raise ReplanningConflict("Recovery v2 sudah tersedia; satu siklus MVP.")
        return {"case_id": case_id, "eligible": recon.unmet_demand_kg > 0,
            "evaluation_status": "READY" if recon.unmet_demand_kg else "NO_RECOVERY_NEEDED",
            "remaining_demand_kg": recon.unmet_demand_kg, "source_reconciliation_id": recon.id,
            "operational_date": operational_day(), "warehouses": warehouses}
    except ReplanningConflict as exc:
        return {"case_id": case_id, "eligible": False, "evaluation_status": "BLOCKED", "reason": str(exc)}


def recovery_metadata(db, plan_id):
    event = db.query(ReplanningEvent).filter_by(plan_id=plan_id).one_or_none()
    if event is None:
        return {}
    return {"source_reconciliation_id": event.source_reconciliation_id,
        "operational_date": event.operational_date, "decision_input": json.loads(event.decision_input)}


class ReplanningService:
    @staticmethod
    def generate(db, case_id, req):
        if db.new or db.dirty or db.deleted:
            raise ValueError("Session memiliki perubahan tertunda.")
        db.rollback()
        try:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            db.expire_all()
            payload = json.dumps({"case_id": case_id, **req.model_dump()}, sort_keys=True, separators=(",", ":"))
            previous = db.get(ReplanningEvent, req.transaction_ref)
            if previous:
                if previous.request_payload != payload:
                    raise ReplanningConflict("transaction_ref sudah digunakan dengan payload berbeda.")
                result = json.loads(previous.response_payload)
                db.commit()
                return result
            if db.get(ShipmentEvent, req.transaction_ref) or db.get(ReceivingEvent, req.transaction_ref) or db.query(ShipmentCheckpoint).filter_by(transaction_ref=req.transaction_ref).first():
                raise ReplanningConflict("transaction_ref sudah digunakan ledger lain.")
            if db.query(AllocationPlan).filter_by(case_id=case_id, version=2).first():
                raise ReplanningConflict("Recovery v2 sudah tersedia; request baru ditolak, bukan Plan v3.")
            case, recon, parent, warehouses = recovery_context(db, case_id, req.source_reconciliation_id)
            inputs = {"verified_demand_kg": recon.target_demand_kg, "accepted_kg": recon.total_sound_kg,
                "remaining_demand_kg": recon.unmet_demand_kg, "source_reconciliation_id": recon.id,
                "parent_plan_id": parent.id, "operational_date": operational_day(),
                "deadline_utc": (case.created_at+timedelta(hours=case.deadline_hours)).isoformat(), "warehouses": warehouses}
            result = {"case_id": case_id, "plan_id": None, "version": None, "plan_status": None,
                "source_reconciliation_id": recon.id, "remaining_demand_kg": recon.unmet_demand_kg,
                "parent_plan_id": parent.id, "is_replanning": True, "operational_date": operational_day()}
            plan_id = None
            if recon.unmet_demand_kg == 0:
                result.update(evaluation_status="NO_RECOVERY_NEEDED", allocated_kg=0, unmet_demand_kg=0,
                    planned_cost=0, solver_status=None, fulfillment_status="FULL_FULFILLMENT", lines=[], solve_time_ms=0)
            else:
                solver_input = [{k:v for k,v in w.items() if k not in ("snapshot_id", "snapshot_time")} for w in warehouses]
                solved = OptimizationEngine.solve_allocation(recon.unmet_demand_kg, solver_input)
                result.update(solved)
                valid_solution = solved["solver_status"] in ("OPTIMAL", "FEASIBLE")
                result["evaluation_status"] = "SOLVER_NO_SOLUTION" if not valid_solution else "NO_ALLOCATION"
                if not valid_solution:
                    result.update(allocated_kg=0, unmet_demand_kg=recon.unmet_demand_kg, planned_cost=0,
                        allocations={}, lines=[], fulfillment_status="NO_ALLOCATION", claim_category="NO_OPTIMALITY_CLAIM")
                if valid_solution:
                    allocations = solved["allocations"]
                    if (set(allocations) != {w["id"] for w in warehouses}
                        or any(not isinstance(q, int) or q < 0 for q in allocations.values())
                        or any(allocations[w["id"]] > w["upper_bound"] for w in warehouses)
                        or sum(allocations.values()) != solved["allocated_kg"]
                        or solved["allocated_kg"] + solved["unmet_demand_kg"] != recon.unmet_demand_kg):
                        raise ReplanningConflict("Solver output melanggar invariant alokasi.")
                    expected_cost = sum(Decimal(str(w['cost_per_kg']))*allocations[w['id']] for w in warehouses)
                    line_ids = [line['warehouse_id'] for line in solved['lines']]
                    expected_status = "FULL_FULFILLMENT" if solved['unmet_demand_kg'] == 0 else ("PARTIAL_FULFILLMENT" if solved['allocated_kg'] else "NO_ALLOCATION")
                    if (len(line_ids) != len(set(line_ids)) or set(line_ids) != set(allocations)
                        or any(line['allocated_kg'] != allocations[line['warehouse_id']] for line in solved['lines'])
                        or Decimal(str(solved['planned_cost'])) != expected_cost
                        or solved['fulfillment_status'] != expected_status):
                        raise ReplanningConflict("Solver lines/cost/status tidak konsisten.")
                if valid_solution and solved["allocated_kg"] > 0:
                    plan_id = "PLAN-" + uuid.uuid4().hex + "-v2"
                    plan = AllocationPlan(id=plan_id, case_id=case_id, version=2, is_replanning=True,
                        parent_plan_id=parent.id, solver_status=solved["solver_status"],
                        fulfillment_status=solved["fulfillment_status"], claim_category=solved["claim_category"],
                        total_demand_kg=recon.unmet_demand_kg, allocated_kg=solved["allocated_kg"],
                        unmet_demand_kg=solved["unmet_demand_kg"], planned_cost=solved["planned_cost"],
                        solve_time_ms=solved["solve_time_ms"], approval_status="PENDING_APPROVAL")
                    db.add(plan)
                    db.flush()
                    for line in solved["lines"]:
                        db.add(AllocationLine(id="LINE-"+uuid.uuid4().hex, plan_id=plan.id,
                            warehouse_id=line["warehouse_id"], allocated_kg=line["allocated_kg"],
                            cost_per_kg=line["cost_per_kg"], subtotal_cost=line["subtotal_cost"],
                            remaining_safe_capacity=line["remaining_safe_after_plan"],
                            remaining_dispatch_capacity=line["remaining_dispatch_after_plan"]))
                    case.status = "RECOVERY_PLAN_PENDING_APPROVAL"
                    result.update(plan_id=plan_id, version=2, plan_status="PENDING_APPROVAL", evaluation_status="RECOVERY_PLAN_CREATED")
            db.flush()
            AuditService.record_log(db, "RECOVERY_EVALUATED", req.actor_id,
                {"result": result, "input": inputs, "transaction_ref": req.transaction_ref,
                    "actor_mode": "SIMULATION_UNAUTHENTICATED"}, case_id)
            db.add(ReplanningEvent(transaction_ref=req.transaction_ref, case_id=case_id,
                source_reconciliation_id=recon.id, plan_id=plan_id, operational_date=operational_day(),
                request_payload=payload, decision_input=json.dumps(inputs), response_payload=json.dumps(result),
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None)))
            db.commit()
            return result
        except (IntegrityError, OperationalError) as exc:
            db.rollback()
            raise ReplanningConflict("Konflik transaksi recovery; retry request yang sama.") from exc
        except Exception:
            db.rollback()
            raise
