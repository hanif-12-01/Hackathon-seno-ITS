"""One combined receipt/final reconciliation per MVP case; no source inventory mutation."""
import json
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy.exc import IntegrityError, OperationalError
from app.models.db_models import (DistributionCase, AllocationPlan, AllocationLine,
    Shipment, ShipmentEvent, Reservation, Receipt, ReceiptShipment, Reconciliation, ReceivingEvent, ReplanningEvent, ShipmentCheckpoint)
from app.services.audit_service import AuditService


class ReceivingConflict(Exception):
    pass


def receipt_detail(db, case_id):
    if db.get(DistributionCase, case_id) is None:
        raise LookupError("Kasus tidak ditemukan.")
    receipt = db.query(Receipt).filter_by(case_id=case_id).one_or_none()
    if receipt is None:
        raise LookupError("Receipt tidak ditemukan.")
    return {"receipt_id": receipt.id, "case_id": case_id, "plan_id": receipt.plan_id,
        "shipment_ids": sorted(row.shipment_id for row in db.query(ReceiptShipment).filter_by(receipt_id=receipt.id)),
        "total_dispatched_kg": receipt.total_dispatched_kg,
        "estimated_total_dispatched_cost": str(Decimal(receipt.estimated_cost_cents) / 100),
        "cost_note": "Estimated dispatched cost; bukan biaya riil tagihan faktur.",
        "arrived_weight_kg": receipt.arrived_weight_kg, "rejected_weight_kg": receipt.rejected_weight_kg,
        "sound_weight_kg": receipt.sound_weight_kg, "transit_discrepancy_kg": receipt.transit_discrepancy_kg,
        "receipt_status": receipt.receipt_status, "inspector_id": receipt.inspector_id,
        "inspection_notes": receipt.inspection_notes,
        "inspected_at": receipt.inspected_at.replace(tzinfo=timezone.utc).isoformat()}


def reconciliation_detail(db, case_id):
    result = db.query(Reconciliation).filter_by(case_id=case_id).one_or_none()
    if result is None:
        raise LookupError("Reconciliation final belum tersedia.")
    receipt = receipt_detail(db, case_id)
    return {"reconciliation_id": result.id, "receipt_id": result.receipt_id, "case_id": case_id,
        "plan_id": receipt["plan_id"], "target_demand_kg": result.target_demand_kg,
        "total_dispatched_kg": receipt["total_dispatched_kg"], "total_arrived_kg": receipt["arrived_weight_kg"],
        "total_rejected_kg": receipt["rejected_weight_kg"], "total_sound_kg": result.total_sound_kg,
        "transit_discrepancy_kg": receipt["transit_discrepancy_kg"], "unmet_demand_kg": result.unmet_demand_kg,
        "reconciliation_status": result.reconciliation_status, "case_status": result.case_status,
        "finalization_status": "FINALIZED", "replan_ready": result.case_status == "REPLAN_AVAILABLE",
        "replan_action_endpoint": None,  # ponytail: M6 endpoint not implemented; consume persisted record first.
        "finalized_at": result.finalized_at.replace(tzinfo=timezone.utc).isoformat()}


def validate_dispatch(db, case, plan_id, shipment_ids):
    plan = db.get(AllocationPlan, plan_id)
    if plan is None:
        raise LookupError("Plan tidak ditemukan.")
    if plan.case_id != case.id or plan.approval_status != "APPROVED" or plan.version != 1:
        raise ReceivingConflict("Plan bukan Plan v1 approved milik kasus.")
    ships = db.query(Shipment).filter_by(plan_id=plan.id).all()
    dispatched = [s for s in ships if s.actual_dispatched_quantity_kg > 0]
    if not dispatched or sorted(s.id for s in dispatched) != sorted(shipment_ids):
        raise ReceivingConflict("Receipt gabungan wajib mereferensikan seluruh dispatch plan yang committed.")
    for s in ships:
        line = db.get(AllocationLine, s.allocation_line_id)
        reservation = db.query(Reservation).filter_by(plan_id=plan.id, warehouse_id=s.warehouse_id).one_or_none()
        if (line is None or line.plan_id != plan.id or line.warehouse_id != s.warehouse_id
            or line.allocated_kg != s.planned_quantity_kg or reservation is None
            or reservation.case_id != case.id or reservation.reserved_stock_kg or reservation.reserved_dispatch_kg
            or reservation.status not in ("DISPATCHED", "CANCELLED")):
            raise ReceivingConflict("Referensi allocation/reservation belum terselesaikan.")
        if s.actual_dispatched_quantity_kg:
            event = db.get(ShipmentEvent, s.transaction_ref)
            if (s.manifest_status != "DISPATCHED" or s.dispatch_timestamp is None or event is None
                or event.action_type != "DISPATCH" or event.shipment_id != s.id):
                raise ReceivingConflict("Shipment belum committed atau sudah ditutup.")
        elif s.manifest_status != "CANCELLED":
            raise ReceivingConflict("Seluruh manifest harus dispatch atau cancellation eksplisit.")
    return dispatched


class ReceivingService:
    @staticmethod
    def execute(db, case_id, req, action="RECEIVE"):
        if db.new or db.dirty or db.deleted:
            raise ValueError("Session memiliki perubahan tertunda.")
        db.rollback()
        try:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            db.expire_all()
            payload = json.dumps({"case_id": case_id, "action": action, **req.model_dump()}, sort_keys=True, separators=(",", ":"))
            previous = db.get(ReceivingEvent, req.transaction_ref)
            if previous:
                if previous.request_payload != payload:
                    raise ReceivingConflict("transaction_ref sudah digunakan dengan payload berbeda.")
                result = json.loads(previous.response_payload)
                db.commit()
                return result
            if db.get(ShipmentEvent, req.transaction_ref) or db.get(ReplanningEvent, req.transaction_ref) or db.query(ShipmentCheckpoint).filter_by(transaction_ref=req.transaction_ref).first():
                raise ReceivingConflict("transaction_ref sudah digunakan untuk shipment.")
            case = db.get(DistributionCase, case_id)
            if case is None:
                raise LookupError("Kasus tidak ditemukan.")
            if db.query(Reconciliation).filter_by(case_id=case_id).first():
                raise ReceivingConflict("Kasus sudah difinalisasi; retry gunakan key asli.")
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if action == "RECEIVE":
                if case.status != "DISPATCHED" or db.query(Receipt).filter_by(case_id=case_id).first():
                    raise ReceivingConflict("Receiving memerlukan kasus DISPATCHED tanpa receipt existing.")
                ships = validate_dispatch(db, case, req.plan_id, req.shipment_ids)
                total = sum(s.actual_dispatched_quantity_kg for s in ships)
                if req.total_dispatched_kg != total or req.arrived_weight_kg > total:
                    raise ReceivingConflict("Total dispatch tidak sesuai manifest atau over-receiving.")
                receipt = Receipt(id="RCP-" + uuid.uuid4().hex, case_id=case_id, plan_id=req.plan_id,
                    total_dispatched_kg=total, estimated_cost_cents=sum(s.estimated_cost_cents for s in ships),
                    arrived_weight_kg=int(req.arrived_weight_kg), rejected_weight_kg=int(req.rejected_weight_kg),
                    sound_weight_kg=int(req.arrived_weight_kg - req.rejected_weight_kg),
                    transit_discrepancy_kg=total - int(req.arrived_weight_kg),
                    inspector_id=req.inspector_id, inspection_notes=req.inspection_notes,
                    inspected_at=now, receipt_status="RECORDED")
                db.add(receipt)
                db.flush()
                db.add_all([ReceiptShipment(receipt_id=receipt.id, shipment_id=s.id) for s in ships])
                case.status = "RECEIVED"
                db.flush()
                result = receipt_detail(db, case_id)
                actor = req.inspector_id
            else:
                receipt = db.get(Receipt, req.receipt_id)
                if receipt is None:
                    raise LookupError("Receipt tidak ditemukan.")
                if receipt.case_id != case_id or receipt.receipt_status != "RECORDED" or case.status != "RECEIVED":
                    raise ReceivingConflict("Receipt/case tidak siap difinalisasi.")
                ids = [r.shipment_id for r in db.query(ReceiptShipment).filter_by(receipt_id=receipt.id)]
                ships = validate_dispatch(db, case, receipt.plan_id, ids)
                if sum(s.actual_dispatched_quantity_kg for s in ships) != receipt.total_dispatched_kg:
                    raise ReceivingConflict("Dispatch berubah setelah receiving.")
                demand = case.verified_demand
                if not case.is_demand_verified or demand < 0 or not float(demand).is_integer():
                    raise ReceivingConflict("Demand belum valid/verified.")
                remaining = max(0, int(demand) - receipt.sound_weight_kg)
                valid_time = case.created_at <= now < case.created_at + timedelta(hours=case.deadline_hours)
                case.status = "REPLAN_AVAILABLE" if remaining and valid_time else "RECONCILED"
                receipt.receipt_status = "FINALIZED"
                db.add(Reconciliation(id="REC-" + uuid.uuid4().hex, case_id=case_id, receipt_id=receipt.id,
                    target_demand_kg=int(demand), total_sound_kg=receipt.sound_weight_kg, unmet_demand_kg=remaining,
                    reconciliation_status="DEFICIT_DETECTED" if remaining else "FULFILLED",
                    case_status=case.status, actor_id=req.actor_id, finalized_at=now))
                # Closure is accounting membership, not per-source arrival/loss attribution.
                for ship in ships:
                    ship.manifest_status = "CLOSED"
                db.flush()
                result = reconciliation_detail(db, case_id)
                actor = req.actor_id
            AuditService.record_log(db, "RECEIPT_INSPECTED" if action == "RECEIVE" else "RECONCILIATION_FINALIZED",
                actor, {**result, "transaction_ref": req.transaction_ref, "actor_mode": "SIMULATION_UNAUTHENTICATED"}, case_id)
            db.add(ReceivingEvent(transaction_ref=req.transaction_ref, case_id=case_id, action_type=action,
                request_payload=payload, response_payload=json.dumps(result), timestamp=now))
            db.commit()
            return result
        except (IntegrityError, OperationalError) as exc:
            db.rollback()
            raise ReceivingConflict("Konflik transaksi receiving; ulangi request yang sama.") from exc
        except Exception:
            db.rollback()
            raise
