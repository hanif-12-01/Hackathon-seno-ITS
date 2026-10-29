import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.db_models import (
    DistributionCase,
    Warehouse,
    InventorySnapshot,
    AllocationPlan,
    AllocationLine,
    Reservation,
    Shipment,
)
from app.models.schemas import CaseCreateRequest
from app.services.gate import DataReadinessGate
from app.services.optimizer import OptimizationEngine
from app.services.audit_service import AuditService
from app.services.capacity import capacity, current_snapshot


class CaseService:
    @staticmethod
    def create_case(db: Session, req: CaseCreateRequest) -> DistributionCase:
        if db.new or db.dirty or db.deleted:
            raise ValueError("Pembuatan kasus membutuhkan session tanpa perubahan tertunda.")
        db.rollback()
        try:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            db.expire_all()
            return CaseService._create_case(db, req)
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def _create_case(db: Session, req: CaseCreateRequest) -> DistributionCase:
        """Pendaftaran kasus distribusi baru beserta snapshot inventaris."""
        case_id = f"CASE-{uuid.uuid4().hex[:8].upper()}"

        case = DistributionCase(
            id=case_id,
            commodity_name=req.commodity_name,
            destination_region=req.destination_region,
            verified_demand=req.verified_demand,
            is_demand_verified=req.is_demand_verified,
            deadline_hours=req.deadline_hours,
            status="DRAFT",
            created_by=req.created_by,
        )
        db.add(case)
        db.flush()

        for w_input in req.warehouses:
            # Upsert master warehouse record if not exists
            wh = db.query(Warehouse).filter(Warehouse.id == w_input.warehouse_id).first()
            if not wh:
                wh = Warehouse(
                    id=w_input.warehouse_id,
                    name=f"Gudang {w_input.warehouse_id.replace('WH-', '')}",
                    location_name="Sentra Jawa Timur",
                    base_cost_per_kg=w_input.cost_per_kg,
                    daily_dispatch_capacity_kg=w_input.daily_dispatch_capacity,
                )
                db.add(wh)
                db.flush()

            # Query existing active reservations in database across all plans
            active_stock_res = (
                db.query(func.coalesce(func.sum(Reservation.reserved_stock_kg), 0.0))
                .filter(
                    Reservation.warehouse_id == w_input.warehouse_id,
                    Reservation.status == "ACTIVE"
                )
                .scalar()
            )
            # Add input active_undispatched_reservations if provided
            total_active_stock_res = float(active_stock_res) + float(w_input.active_undispatched_reservations)

            active_disp_res = (
                db.query(func.coalesce(func.sum(Reservation.reserved_dispatch_kg), 0.0))
                .filter(
                    Reservation.warehouse_id == w_input.warehouse_id,
                    Reservation.status == "ACTIVE"
                )
                .scalar()
            )
            total_active_disp_res = float(active_disp_res) + float(w_input.active_undispatched_reservations)

            safe_cap = max(
                0.0,
                w_input.physical_stock - w_input.local_demand - w_input.safety_stock - total_active_stock_res
            )
            rem_disp = max(
                0.0,
                w_input.daily_dispatch_capacity - w_input.actual_dispatched_today - total_active_disp_res
            )

            snapshot = InventorySnapshot(
                id=f"SNAP-{uuid.uuid4().hex[:8].upper()}",
                case_id=case_id,
                warehouse_id=w_input.warehouse_id,
                physical_stock=w_input.physical_stock,
                local_demand=w_input.local_demand,
                safety_stock=w_input.safety_stock,
                active_stock_reservations=total_active_stock_res,
                safe_capacity=safe_cap,
                daily_dispatch_capacity=w_input.daily_dispatch_capacity,
                actual_dispatched_today=w_input.actual_dispatched_today,
                active_undispatched_dispatch_reservations_today=total_active_disp_res,
                remaining_dispatch_capacity=rem_disp,
                cost_per_kg=w_input.cost_per_kg,
                is_verified=True,
                external_reservations=w_input.active_undispatched_reservations,
                snapshot_time=(w_input.snapshot_time.astimezone(timezone.utc).replace(tzinfo=None)
                               if w_input.snapshot_time and w_input.snapshot_time.tzinfo
                               else w_input.snapshot_time or datetime.utcnow()),
            )
            if db.query(Shipment.id).filter(Shipment.warehouse_id == w_input.warehouse_id,
                    Shipment.dispatch_timestamp.is_not(None)).first():
                # Case registration is not a stock adjustment or renewed verification.
                authoritative = current_snapshot(db, w_input.warehouse_id)
                for column in InventorySnapshot.__table__.columns:
                    if column.name not in ("id", "case_id", "warehouse_id"):
                        setattr(snapshot, column.name, getattr(authoritative, column.name))
                values = capacity(db, authoritative)
                bounds = OptimizationEngine.calculate_bounds(values)
                snapshot.active_stock_reservations = values["active_stock_reservations_kg"]
                snapshot.active_undispatched_dispatch_reservations_today = values["active_undispatched_dispatch_reservations_today_kg"]
                snapshot.safe_capacity = bounds["safe_available"]
                snapshot.remaining_dispatch_capacity = bounds["dispatch_available"]
            db.add(snapshot)

        AuditService.record_log(
            db=db,
            case_id=case_id,
            action_type="CASE_CREATED",
            actor_id=req.created_by,
            details={
                "verified_demand": req.verified_demand,
                "is_demand_verified": req.is_demand_verified,
                "warehouses_count": len(req.warehouses),
            },
        )
        db.commit()
        db.refresh(case)
        return case

    @staticmethod
    def validate_gate(db: Session, case_id: str) -> Dict[str, Any]:
        """Menjalankan validasi Data Readiness Gate."""
        case = db.query(DistributionCase).filter(DistributionCase.id == case_id).first()
        if not case:
            raise ValueError(f"Kasus {case_id} tidak ditemukan.")

        snapshots = db.query(InventorySnapshot).filter(InventorySnapshot.case_id == case_id).all()
        original_ready = DataReadinessGate.validate_case(case, snapshots)["is_ready"]
        snapshots = [current_snapshot(db, s.warehouse_id) for s in snapshots]
        result = DataReadinessGate.validate_case(case, snapshots)
        result["is_ready"] = result["is_ready"] and original_ready
        if not original_ready:
            result["validation_details"].append({"rule": "CASE_SNAPSHOT_READINESS", "status": "FAILED"})
        bounds = {s.warehouse_id: OptimizationEngine.calculate_bounds(capacity(db, s)) for s in snapshots}
        result["safe_capacities"] = {key: b["safe_available"] for key, b in bounds.items()}
        result["remaining_dispatch_capacities"] = {key: b["dispatch_available"] for key, b in bounds.items()}

        if result["is_ready"] and case.status == "DRAFT":
            case.status = "VALIDATED"
            db.commit()

        AuditService.record_log(
            db=db,
            case_id=case_id,
            action_type="GATE_VALIDATED",
            actor_id=case.created_by,
            details=result,
        )
        db.commit()
        return result

    @staticmethod
    def optimize_case(db: Session, case_id: str) -> Dict[str, Any]:
        """Menjalankan optimasi OR-Tools CP-SAT untuk menghasilkan Plan v1."""
        case = db.query(DistributionCase).filter(DistributionCase.id == case_id).first()
        if not case:
            raise ValueError(f"Kasus {case_id} tidak ditemukan.")

        if not case.is_demand_verified:
            raise ValueError("Optimasi ditahan: Kebutuhan pasokan belum terverifikasi (Data Readiness Gate Failed).")

        existing = db.query(AllocationPlan).filter_by(case_id=case_id).first()
        if existing:
            raise ValueError("Rencana kasus sudah tersedia; optimasi ulang ditahan.")

        snapshots = db.query(InventorySnapshot).filter(InventorySnapshot.case_id == case_id).all()
        if not snapshots:
            raise ValueError(f"Snapshot inventaris untuk kasus {case_id} tidak ditemukan.")

        if not DataReadinessGate.validate_case(case, snapshots)["is_ready"]:
            raise ValueError("Data Readiness Gate Failed: snapshot kasus tidak siap atau usang.")
        snapshots = [current_snapshot(db, s.warehouse_id) for s in snapshots]
        if not DataReadinessGate.validate_case(case, snapshots)["is_ready"]:
            raise ValueError("Data Readiness Gate Failed: data tidak siap atau snapshot usang.")

        wh_payload = [capacity(db, s) for s in snapshots]
        # Jalankan solver CP-SAT nyata
        solve_res = OptimizationEngine.solve_allocation(
            demand_kg=int(case.verified_demand),
            warehouses=wh_payload
        )

        plan_id = f"PLAN-{case.id[-8:]}-v1"
        plan = AllocationPlan(
            id=plan_id,
            case_id=case_id,
            version=1,
            is_replanning=False,
            solver_status=solve_res["solver_status"],
            fulfillment_status=solve_res["fulfillment_status"],
            claim_category=solve_res["claim_category"],
            total_demand_kg=float(case.verified_demand),
            allocated_kg=float(solve_res["allocated_kg"]),
            unmet_demand_kg=float(solve_res["unmet_demand_kg"]),
            planned_cost=float(solve_res["planned_cost"]),
            solve_time_ms=float(solve_res["solve_time_ms"]),
            approval_status="PENDING_APPROVAL",
        )
        db.add(plan)
        db.flush()

        lines_output = []
        for line_data in solve_res["lines"]:
            alloc_line = AllocationLine(
                id=f"LINE-{uuid.uuid4().hex[:8].upper()}",
                plan_id=plan_id,
                warehouse_id=line_data["warehouse_id"],
                allocated_kg=float(line_data["allocated_kg"]),
                cost_per_kg=float(line_data["cost_per_kg"]),
                subtotal_cost=float(line_data["subtotal_cost"]),
                remaining_safe_capacity=float(line_data["remaining_safe_after_plan"]),
                remaining_dispatch_capacity=float(line_data["remaining_dispatch_after_plan"]),
            )
            db.add(alloc_line)
            lines_output.append({
                "warehouse_id": line_data["warehouse_id"],
                "allocated_kg": float(line_data["allocated_kg"]),
                "cost_per_kg": float(line_data["cost_per_kg"]),
                "subtotal": float(line_data["subtotal_cost"]),
                "remaining_safe_capacity": float(line_data["remaining_safe_after_plan"]),
                "remaining_dispatch_capacity": float(line_data["remaining_dispatch_after_plan"]),
                "allocation_constraint_note": None,
            })

        case.status = "PENDING_APPROVAL"
        AuditService.record_log(
            db=db,
            case_id=case_id,
            action_type="OPTIMIZATION_EXECUTED",
            actor_id="system-solver-cp-sat",
            details={
                "plan_id": plan_id,
                "solver_status": solve_res["solver_status"],
                "fulfillment_status": solve_res["fulfillment_status"],
                "planned_cost": solve_res["planned_cost"],
                "allocated_kg": solve_res["allocated_kg"],
            },
        )
        db.commit()

        return {
            "plan_id": plan_id,
            "version": 1,
            "solver_status": solve_res["solver_status"],
            "fulfillment_status": solve_res["fulfillment_status"],
            "claim_category": solve_res["claim_category"],
            "total_demand_kg": float(case.verified_demand),
            "allocated_kg": float(solve_res["allocated_kg"]),
            "unmet_demand_kg": float(solve_res["unmet_demand_kg"]),
            "planned_cost": float(solve_res["planned_cost"]),
            "cost_note": "Planned cost berdasarkan alokasi rencana yang disetujui; bukan biaya riil tagihan faktur.",
            "solve_time_ms": solve_res["solve_time_ms"],
            "lines": lines_output,
        }
