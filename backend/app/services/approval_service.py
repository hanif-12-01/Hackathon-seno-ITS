import uuid
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError, IntegrityError
from app.models.db_models import AllocationPlan, Reservation, Approval, DistributionCase
from app.models.schemas import ApproveRequest
from app.services.audit_service import AuditService
from app.services.capacity import capacity, current_snapshot, operational_day
from app.services.gate import DataReadinessGate
from app.services.optimizer import OptimizationEngine
from app.models.db_models import Shipment
from app.services.shipment_service import initialize_shipments


class InsufficientCapacityConflictError(Exception):
    pass


class PlanAlreadyProcessedError(InsufficientCapacityConflictError):
    pass


class ApprovalService:
    @staticmethod
    def approve_plan(db: Session, plan_id: str, req: ApproveRequest):
        # Read-only callers may have cached an older plan. End their read transaction
        # before taking the write reservation; never commit their unrelated changes.
        if db.new or db.dirty or db.deleted:
            raise ValueError("Approval membutuhkan session tanpa perubahan tertunda.")
        db.rollback()
        try:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            db.expire_all()
            plan = db.query(AllocationPlan).filter_by(id=plan_id).first()
            if not plan:
                raise ValueError(f"Rencana alokasi {plan_id} tidak ditemukan.")

            def response():
                rows = db.query(Reservation).filter_by(plan_id=plan_id).all()
                return {"plan_id": plan_id, "approval_status": plan.approval_status,
                        "reservations_created": [{"warehouse_id": r.warehouse_id,
                            "reserved_kg": r.reserved_stock_kg, "status": r.status} for r in rows],
                        "shipments_initialized": [s.id for s in db.query(Shipment).filter_by(plan_id=plan_id).order_by(Shipment.warehouse_id)]}

            def conflict(message):
                AuditService.record_log(db, "APPROVAL_CONFLICT", req.actor_id,
                    {"plan_id": plan_id, "decision": req.decision, "reason": message}, plan.case_id)
                db.commit()  # Only the conflict audit is written; no reservation mutations yet.
                raise InsufficientCapacityConflictError(message)

            target = "APPROVED" if req.decision == "APPROVE" else "REJECTED"
            if plan.approval_status in ("APPROVED", "REJECTED"):
                if plan.approval_status != target:
                    conflict("Konflik keputusan: rencana sudah " + plan.approval_status)
                result = response()
                db.commit()
                return result

            case = db.query(DistributionCase).filter_by(id=plan.case_id).one()
            if req.decision == "APPROVE":
                if plan.is_replanning:
                    from app.services.replanning_service import recovery_context, ReplanningConflict
                    from app.models.db_models import ReplanningEvent
                    event = db.query(ReplanningEvent).filter_by(plan_id=plan.id).one_or_none()
                    if event is None or plan.version != 2 or plan.allocated_kg <= 0:
                        conflict("Recovery plan tidak actionable atau metadata sumber hilang.")
                    try:
                        _, recon, parent, _ = recovery_context(db, case.id, event.source_reconciliation_id, approval=True)
                    except ReplanningConflict as exc:
                        conflict(str(exc))
                    if (event.operational_date != operational_day() or plan.parent_plan_id != parent.id
                        or plan.total_demand_kg != recon.unmet_demand_kg
                        or sum(l.allocated_kg for l in plan.lines) != plan.allocated_kg
                        or plan.allocated_kg + plan.unmet_demand_kg != recon.unmet_demand_kg):
                        conflict("Recovery input/operational date berubah atau tidak konsisten.")
                    import json
                    generated = json.loads(event.response_payload)
                    if (plan.allocated_kg != generated['allocated_kg'] or plan.unmet_demand_kg != generated['unmet_demand_kg']
                        or plan.planned_cost != generated['planned_cost']
                        or {l.warehouse_id:l.allocated_kg for l in plan.lines} != generated['allocations']
                        or any(l.allocated_kg < 0 or int(l.allocated_kg) != l.allocated_kg for l in plan.lines)):
                        conflict("Recovery plan berubah dari keputusan solver tersimpan.")
                if plan.solver_status not in ("OPTIMAL", "FEASIBLE"):
                    conflict("Status solver tidak dapat disetujui.")
                if not plan.lines:
                    conflict("Rencana tidak memiliki rincian alokasi.")
                if not DataReadinessGate.validate_case(case, case.snapshots)["is_ready"]:
                    conflict("Data Readiness Gate Failed: snapshot kasus tidak siap.")
                snapshots = [current_snapshot(db, line.warehouse_id) for line in plan.lines]
                if any(s is None for s in snapshots) or not DataReadinessGate.validate_case(case, snapshots)["is_ready"]:
                    conflict("Data Readiness Gate Failed saat approval.")
                pending = []
                for line, snapshot in zip(plan.lines, snapshots):
                    bounds = OptimizationEngine.calculate_bounds(capacity(db, snapshot))
                    if line.allocated_kg > bounds["upper_bound"]:
                        conflict(f"Konflik Kapasitas Gudang {line.warehouse_id}: stok aman atau kuota harian tidak cukup.")
                    if line.allocated_kg > 0:
                        pending.append(Reservation(id="RES-" + uuid.uuid4().hex,
                            plan_id=plan_id, case_id=case.id, warehouse_id=line.warehouse_id,
                            reserved_stock_kg=line.allocated_kg, reserved_dispatch_kg=line.allocated_kg,
                            operational_date=operational_day(), status="ACTIVE"))
                db.add_all(pending)
                db.flush()
                initialize_shipments(db, plan)
            plan.approval_status = target
            case.status = ("RECOVERY_RESERVED" if target == "APPROVED" else "RECOVERY_REJECTED") if plan.is_replanning else target
            db.add(Approval(id="APPR-" + uuid.uuid4().hex, plan_id=plan_id,
                actor_id=req.actor_id, decision=req.decision, notes=req.notes))
            AuditService.record_log(db, "PLAN_" + target, req.actor_id,
                {"plan_id": plan_id, "notes": req.notes}, case.id)
            db.flush()
            result = response()
            db.commit()
            return result
        except (OperationalError, IntegrityError) as exc:
            db.rollback()
            raise InsufficientCapacityConflictError("Konflik transaksi approval; ulangi request yang sama.") from exc
        except Exception:
            db.rollback()
            raise
