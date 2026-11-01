"""One actual departure per allocation line; SQLite owns cross-connection locking."""
import json
from app.core.database import begin_mutation
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.exc import IntegrityError, OperationalError
from app.models.db_models import (Shipment, ShipmentEvent, DispatchUsage, AllocationPlan,
    AllocationLine, Reservation, DistributionCase, ReceivingEvent, ReplanningEvent, ShipmentCheckpoint)
from app.services.capacity import capacity, current_snapshot, operational_day
from app.services.optimizer import OptimizationEngine
from app.services.audit_service import AuditService
from app.services.gate import DataReadinessGate
from app.core.security import audit_actor_mode


class ShipmentConflict(Exception):
    pass


def initialize_shipments(db, plan):
    for line in plan.lines:
        if line.allocated_kg > 0:
            reservation = db.query(Reservation).filter_by(plan_id=plan.id, warehouse_id=line.warehouse_id).one()
            db.add(Shipment(id="SHIP-" + uuid.uuid4().hex, allocation_line_id=line.id,
                plan_id=plan.id, warehouse_id=line.warehouse_id,
                planned_quantity_kg=int(line.allocated_kg), operational_date=reservation.operational_date))


def warehouse_state(db, warehouse_id):
    snapshot = current_snapshot(db, warehouse_id)
    if snapshot is None:
        raise LookupError("Snapshot gudang tidak ditemukan.")
    values = capacity(db, snapshot)
    bounds = OptimizationEngine.calculate_bounds(values)
    return {"warehouse_id": warehouse_id, "physical_stock": snapshot.physical_stock,
        "safe_capacity": bounds["safe_available"],
        "remaining_dispatch_capacity": bounds["dispatch_available"],
        "actual_dispatched_today": values["actual_dispatched_today_kg"],
        "active_stock_reservations": values["active_stock_reservations_kg"],
        "active_dispatch_reservations_today": values["active_undispatched_dispatch_reservations_today_kg"],
        "operational_date": operational_day()}


def shipment_detail(db, shipment_id):
    shipment = db.get(Shipment, shipment_id)
    if shipment is None:
        raise LookupError("Shipment tidak ditemukan.")
    plan = db.get(AllocationPlan, shipment.plan_id)
    reservation = db.query(Reservation).filter_by(plan_id=plan.id, warehouse_id=shipment.warehouse_id).one()
    return {"shipment_id": shipment.id, "case_id": plan.case_id, "plan_id": plan.id,
        "warehouse_id": shipment.warehouse_id, "allocation_line_id": shipment.allocation_line_id,
        "planned_quantity_kg": shipment.planned_quantity_kg,
        "actual_dispatched_quantity_kg": shipment.actual_dispatched_quantity_kg,
        "unshipped_cancelled_quantity_kg": shipment.unshipped_cancelled_quantity_kg,
        "remaining_reserved_stock_kg": reservation.reserved_stock_kg,
        "remaining_reserved_dispatch_kg": reservation.reserved_dispatch_kg,
        "in_transit_quantity_kg": shipment.actual_dispatched_quantity_kg if shipment.manifest_status == "DISPATCHED" else 0,
        "estimated_line_dispatched_cost": str(Decimal(shipment.estimated_cost_cents) / 100),
        "dispatch_timestamp": shipment.dispatch_timestamp.replace(tzinfo=timezone.utc).isoformat() if shipment.dispatch_timestamp else None,
        "operational_date": shipment.operational_date, "transaction_ref": shipment.transaction_ref,
        "manifest_status": shipment.manifest_status}


def checkpoint_history(db, shipment_id):
    if db.get(Shipment, shipment_id) is None:
        raise LookupError("Shipment tidak ditemukan.")
    return [json.loads(row.response_payload) for row in db.query(ShipmentCheckpoint)
        .filter_by(shipment_id=shipment_id).order_by(ShipmentCheckpoint.id)]


def record_checkpoint(db, shipment_id, req):
    if db.new or db.dirty or db.deleted:
        raise ValueError("Session memiliki perubahan tertunda.")
    db.rollback()
    try:
        begin_mutation(db)
        payload = json.dumps({"shipment_id": shipment_id, **req.model_dump()}, sort_keys=True, separators=(",", ":"))
        previous = db.query(ShipmentCheckpoint).filter_by(transaction_ref=req.transaction_ref).first()
        if previous:
            if previous.request_payload != payload:
                raise ShipmentConflict("transaction_ref sudah digunakan dengan payload berbeda.")
            result = json.loads(previous.response_payload)
            db.commit()
            return result
        if any(db.get(model, req.transaction_ref) for model in (ShipmentEvent, ReceivingEvent, ReplanningEvent)):
            raise ShipmentConflict("transaction_ref sudah digunakan ledger lain.")
        shipment = db.get(Shipment, shipment_id)
        if shipment is None:
            raise LookupError("Shipment tidak ditemukan.")
        plan = db.get(AllocationPlan, shipment.plan_id)
        case = db.get(DistributionCase, plan.case_id)
        if (plan.is_replanning or plan.approval_status != "APPROVED"
            or shipment.manifest_status != "DISPATCHED" or not shipment.dispatch_timestamp
            or shipment.actual_dispatched_quantity_kg <= 0
            or case.status not in ("PARTIALLY_DISPATCHED", "DISPATCHED")):
            raise ShipmentConflict("Checkpoint hanya untuk shipment aktual dispatched dan terbuka; bukan recovery/receiving.")
        departure = db.get(ShipmentEvent, shipment.transaction_ref) if shipment.transaction_ref else None
        if departure is None or departure.shipment_id != shipment_id or departure.action_type != "DISPATCH":
            raise ShipmentConflict("Dispatch committed tidak terbukti.")
        now = datetime.now(timezone.utc)
        row = ShipmentCheckpoint(shipment_id=shipment_id, transaction_ref=req.transaction_ref,
            event_type=req.event_type, note=req.note, actor_id=req.actor_id,
            timestamp=now.replace(tzinfo=None), request_payload=payload, response_payload="{}")
        db.add(row)
        db.flush()
        result = {"checkpoint_id": row.id, "shipment_id": shipment_id, **req.model_dump(),
            "timestamp": now.isoformat(), "actor_mode": audit_actor_mode()}
        row.response_payload = json.dumps(result)
        AuditService.record_log(db, "SHIPMENT_CHECKPOINT_RECORDED", req.actor_id, result, case.id)
        db.commit()
        return result
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise ShipmentConflict("Konflik checkpoint; ulangi request yang sama.") from exc
    except Exception:
        db.rollback()
        raise


class ShipmentService:
    @staticmethod
    def execute(db, shipment_id, req, action="DISPATCH"):
        if db.new or db.dirty or db.deleted:
            raise ValueError("Session memiliki perubahan tertunda.")
        db.rollback()
        try:
            begin_mutation(db)
            payload = json.dumps({"shipment_id": shipment_id, "action": action,
                **req.model_dump()}, sort_keys=True, separators=(",", ":"))
            previous = db.get(ShipmentEvent, req.transaction_ref)
            if previous:
                if previous.request_payload != payload:
                    raise ShipmentConflict("transaction_ref sudah digunakan dengan payload berbeda.")
                result = json.loads(previous.response_payload)
                db.commit()
                return result
            if db.get(ReceivingEvent, req.transaction_ref) or db.get(ReplanningEvent, req.transaction_ref) or db.query(ShipmentCheckpoint).filter_by(transaction_ref=req.transaction_ref).first():
                raise ShipmentConflict("transaction_ref sudah digunakan untuk receiving.")
            shipment = db.get(Shipment, shipment_id)
            if shipment is None:
                raise LookupError("Shipment tidak ditemukan.")
            plan = db.get(AllocationPlan, shipment.plan_id)
            if plan.is_replanning:
                raise ShipmentConflict("Dispatch/cancellation recovery di luar scope M6.")
            case = db.get(DistributionCase, plan.case_id)
            if plan.approval_status != "APPROVED" or case.status not in ("APPROVED", "PARTIALLY_DISPATCHED", "DISPATCHED"):
                raise ShipmentConflict("Plan/case tidak mengizinkan dispatch atau cancellation.")
            line = db.get(AllocationLine, shipment.allocation_line_id)
            reservation = db.query(Reservation).filter_by(plan_id=plan.id, warehouse_id=shipment.warehouse_id).one_or_none()
            if (line is None or line.plan_id != plan.id or line.warehouse_id != shipment.warehouse_id
                or line.allocated_kg != shipment.planned_quantity_kg or reservation is None
                or reservation.case_id != case.id or reservation.status != "ACTIVE"):
                raise ShipmentConflict("Allocation/reservation tidak valid atau tidak aktif.")
            actual = int(req.actual_dispatched_quantity_kg) if action == "DISPATCH" else 0
            cancel = int(req.unshipped_cancelled_quantity_kg)
            if action == "DISPATCH":
                if shipment.manifest_status != "READY_TO_SHIP" or shipment.transaction_ref:
                    raise ShipmentConflict("Manifest sudah diberangkatkan atau dibatalkan.")
                if req.planned_quantity_kg != shipment.planned_quantity_kg:
                    raise ShipmentConflict("Planned quantity tidak sesuai allocation.")
                if reservation.operational_date != operational_day():
                    raise ShipmentConflict("Reservasi dispatch bukan untuk hari operasional WIB ini.")
            if actual + cancel > min(reservation.reserved_stock_kg, reservation.reserved_dispatch_kg):
                raise ShipmentConflict("Quantity melebihi reservasi tersisa.")
            if shipment.actual_dispatched_quantity_kg + shipment.unshipped_cancelled_quantity_kg + actual + cancel > shipment.planned_quantity_kg:
                raise ShipmentConflict("Quantity melebihi allocation.")
            snapshot = current_snapshot(db, shipment.warehouse_id)
            if snapshot is None:
                raise ShipmentConflict("Snapshot gudang tidak tersedia.")
            before = warehouse_state(db, shipment.warehouse_id)
            if action == "DISPATCH":
                if not DataReadinessGate.validate_case(case, [snapshot])["is_ready"]:
                    raise ShipmentConflict("Snapshot dispatch tidak siap atau usang.")
                # Own reservation becomes available only to this event, never other plans.
                values = capacity(db, snapshot)
                values["active_stock_reservations_kg"] -= actual
                values["active_undispatched_dispatch_reservations_today_kg"] -= actual
                bounds = OptimizationEngine.calculate_bounds(values)
                if actual > bounds["upper_bound"]:
                    raise ShipmentConflict("Stok aman atau kuota harian tidak cukup untuk dispatch.")
                usage = db.get(DispatchUsage, (shipment.warehouse_id, operational_day()))
                if usage is None:
                    usage = DispatchUsage(warehouse_id=shipment.warehouse_id,
                        operational_date=operational_day(), dispatched_kg=0)
                    db.add(usage)
                usage.dispatched_kg = int(before["actual_dispatched_today"]) + actual
                snapshot.physical_stock -= actual
                shipment.actual_dispatched_quantity_kg = actual
                shipment.estimated_cost_cents = int(Decimal(str(line.cost_per_kg)) * 100) * actual
                shipment.dispatch_timestamp = datetime.now(timezone.utc).replace(tzinfo=None)
                shipment.transaction_ref = req.transaction_ref
                shipment.manifest_status = "DISPATCHED"
            shipment.unshipped_cancelled_quantity_kg += cancel
            reservation.reserved_stock_kg -= actual + cancel
            reservation.reserved_dispatch_kg -= actual + cancel
            if reservation.reserved_stock_kg == reservation.reserved_dispatch_kg == 0:
                reservation.status = "CANCELLED" if shipment.actual_dispatched_quantity_kg == 0 else "DISPATCHED"
                if shipment.actual_dispatched_quantity_kg == 0:
                    shipment.manifest_status = "CANCELLED"
            db.flush()
            siblings = db.query(Shipment).filter_by(plan_id=plan.id).all()
            # ponytail: legacy plan cost column remains FLOAT; exact manifest ledger uses integer cents.
            plan.estimated_dispatched_cost = float(sum(Decimal(s.estimated_cost_cents) for s in siblings) / 100)
            if any(s.actual_dispatched_quantity_kg for s in siblings):
                case.status = "DISPATCHED" if all(s.manifest_status in ("DISPATCHED", "CANCELLED") for s in siblings) else "PARTIALLY_DISPATCHED"
            after = warehouse_state(db, shipment.warehouse_id)
            snapshot.active_stock_reservations = after["active_stock_reservations"]
            snapshot.safe_capacity = after["safe_capacity"]
            snapshot.active_undispatched_dispatch_reservations_today = after["active_dispatch_reservations_today"]
            snapshot.remaining_dispatch_capacity = after["remaining_dispatch_capacity"]
            details = {"event_id": req.transaction_ref, "plan_id": plan.id,
                "shipment_id": shipment.id, "warehouse_id": shipment.warehouse_id,
                "planned_quantity_kg": shipment.planned_quantity_kg,
                "actual_dispatched_quantity_kg": actual, "reservation_consumed_kg": actual,
                "reservation_cancelled_kg": cancel, "cancellation_reason": req.cancellation_reason,
                "before": before, "after": after, "result": "COMMITTED",
                "actor_mode": audit_actor_mode(), "operational_date": operational_day()}
            if action == "DISPATCH":
                AuditService.record_log(db, "DISPATCH_CONFIRMED", req.actor_id, details, case.id)
            if cancel:
                AuditService.record_log(db, "UNSHIPPED_RESERVATION_CANCELLED", req.actor_id, details, case.id)
            result = {**shipment_detail(db, shipment.id), "physical_stock_deducted_kg": actual,
                "reservation_released_kg": actual + cancel, "safe_capacity_restored_kg": cancel,
                "warehouse_state": after}
            db.add(ShipmentEvent(transaction_ref=req.transaction_ref, shipment_id=shipment.id,
                action_type=action, request_payload=payload, response_payload=json.dumps(result)))
            db.commit()
            return result
        except (IntegrityError, OperationalError) as exc:
            db.rollback()
            raise ShipmentConflict("Konflik transaksi dispatch; ulangi request yang sama.") from exc
        except Exception:
            db.rollback()
            raise
