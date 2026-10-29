from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.db_models import DistributionCase, AllocationPlan, AuditLog, InventorySnapshot, Reservation
from app.models.schemas import (
    CaseCreateRequest,
    CaseCreateResponse,
    ValidationResponse,
    OptimizeResponse,
    ApproveRequest,
    ApproveResponse,
    AuditLogResponse,
    CaseDetailResponse,
    ForecastStatusResponse,
    HistoricalSeriesResponse,
    ForecastPredictionsResponse,
)
from app.services.case_service import CaseService
from app.services.forecasting_service import ForecastingService, ForecastingUnavailableError
from app.services.approval_service import (
    ApprovalService,
    InsufficientCapacityConflictError,
    PlanAlreadyProcessedError,
)

router = APIRouter()

from app.models.schemas import DispatchRequest, CancellationRequest, DispatchResponse, ShipmentResponse, WarehouseStateResponse
from app.models.db_models import Shipment
from app.services.shipment_service import ShipmentService, ShipmentConflict, shipment_detail, warehouse_state
from app.models.schemas import ReceiptRequest, ReconcileRequest
from app.services.receiving_service import ReceivingService, ReceivingConflict, receipt_detail, reconciliation_detail
from app.models.schemas import ReplanRequest, CheckpointRequest
from app.models.db_models import ShipmentCheckpoint
from app.services.shipment_service import record_checkpoint, checkpoint_history
from app.models.db_models import ReplanningEvent
from app.services.replanning_service import ReplanningService, ReplanningConflict, eligibility, recovery_metadata
import json


@router.get("/operations/{transaction_ref}")
def get_operation(transaction_ref: str, db: Session = Depends(get_db)):
    from app.models.db_models import ShipmentEvent, ReceivingEvent
    for model in (ShipmentCheckpoint, ShipmentEvent, ReceivingEvent, ReplanningEvent):
        event = db.query(model).filter_by(transaction_ref=transaction_ref).first()
        if event:
            payload = json.loads(event.request_payload)
            action = payload.pop("action", None)
            if model is ShipmentCheckpoint:
                endpoint = f"/shipments/{payload.pop('shipment_id')}/checkpoints"
            elif model is ShipmentEvent:
                endpoint = f"/shipments/{payload.pop('shipment_id')}/" + ("dispatch" if action == "DISPATCH" else "cancel-reservation")
            else:
                endpoint = f"/cases/{payload.pop('case_id')}/" + ("replan" if model is ReplanningEvent else "receipts" if action == "RECEIVE" else "reconcile")
            return {"endpoint": endpoint, "payload": payload, "result": json.loads(event.response_payload)}
    raise HTTPException(404, "Operasi belum terbukti; bukan bukti kegagalan.")


@router.post("/cases/{id}/replan")
def replan_case(id: str, req: ReplanRequest, db: Session = Depends(get_db)):
    try:
        return ReplanningService.generate(db, id, req)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except (ReplanningConflict, ValueError) as exc:
        raise HTTPException(409, str(exc))


@router.get("/cases/{id}/recovery-eligibility")
def recovery_eligibility(id: str, db: Session = Depends(get_db)):
    try:
        return eligibility(db, id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/cases/{id}/replanning-history")
def replanning_history(id: str, db: Session = Depends(get_db)):
    if db.get(DistributionCase, id) is None:
        raise HTTPException(404, "Kasus tidak ditemukan.")
    return [{"transaction_ref": row.transaction_ref, "source_reconciliation_id": row.source_reconciliation_id,
        "decision_input": json.loads(row.decision_input), "result": json.loads(row.response_payload)}
        for row in db.query(ReplanningEvent).filter_by(case_id=id).order_by(ReplanningEvent.timestamp, ReplanningEvent.transaction_ref)]


@router.post("/cases/{id}/receipts", status_code=201)
def receive_case(id: str, req: ReceiptRequest, db: Session = Depends(get_db)):
    try:
        return ReceivingService.execute(db, id, req)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ReceivingConflict as exc:
        raise HTTPException(409, str(exc))


@router.post("/cases/{id}/reconcile")
def reconcile_case(id: str, req: ReconcileRequest, db: Session = Depends(get_db)):
    try:
        return ReceivingService.execute(db, id, req, "FINALIZE")
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ReceivingConflict as exc:
        raise HTTPException(409, str(exc))


@router.get("/cases/{id}/receipts")
def get_receipt(id: str, db: Session = Depends(get_db)):
    try:
        return receipt_detail(db, id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/cases/{id}/reconciliation")
def get_reconciliation(id: str, db: Session = Depends(get_db)):
    try:
        return reconciliation_detail(db, id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/shipments/{id}/checkpoints", status_code=201)
def create_checkpoint(id: str, req: CheckpointRequest, db: Session = Depends(get_db)):
    try:
        return record_checkpoint(db, id, req)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ShipmentConflict as exc:
        raise HTTPException(409, str(exc))


@router.get("/shipments/{id}/checkpoints")
def list_checkpoints(id: str, db: Session = Depends(get_db)):
    try:
        return checkpoint_history(db, id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/shipments/{id}/dispatch", response_model=DispatchResponse)
def dispatch_shipment(id: str, req: DispatchRequest, db: Session = Depends(get_db)):
    try:
        return ShipmentService.execute(db, id, req)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ShipmentConflict as exc:
        raise HTTPException(409, str(exc))


@router.post("/shipments/{id}/cancel-reservation", response_model=DispatchResponse)
def cancel_shipment_reservation(id: str, req: CancellationRequest, db: Session = Depends(get_db)):
    try:
        return ShipmentService.execute(db, id, req, "CANCEL")
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ShipmentConflict as exc:
        raise HTTPException(409, str(exc))


@router.get("/shipments/{id}", response_model=ShipmentResponse)
def get_shipment(id: str, db: Session = Depends(get_db)):
    try:
        return shipment_detail(db, id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/warehouses/{id}/capacity", response_model=WarehouseStateResponse)
def get_warehouse_capacity(id: str, db: Session = Depends(get_db)):
    try:
        return warehouse_state(db, id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/cases", response_model=CaseCreateResponse, status_code=status.HTTP_201_CREATED)
def create_case(req: CaseCreateRequest, db: Session = Depends(get_db)):
    """Pendaftaran kasus distribusi pangan baru beserta snapshot inventaris."""
    try:
        case = CaseService.create_case(db=db, req=req)
        return CaseCreateResponse(
            status="success",
            case_id=case.id,
            message="Kasus distribusi berhasil dibuat dalam status DRAFT."
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/cases")
def list_cases(db: Session = Depends(get_db)):
    """Case selector uses persisted records, including after browser restart."""
    return [{"id": c.id, "destination_region": c.destination_region,
             "commodity_name": c.commodity_name, "status": c.status,
             "verified_demand": c.verified_demand, "created_at": c.created_at}
            for c in db.query(DistributionCase).order_by(DistributionCase.created_at.desc(), DistributionCase.id)]


@router.get("/cases/{id}", response_model=CaseDetailResponse)
def get_case(id: str, db: Session = Depends(get_db)):
    """Membaca data kasus, snapshot inventaris, rencana alokasi, dan status saat ini."""
    case = db.query(DistributionCase).filter(DistributionCase.id == id).first()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Kasus {id} tidak ditemukan.")

    snapshots_data = [
        {
            "warehouse_id": s.warehouse_id,
            "physical_stock": s.physical_stock,
            "local_demand": s.local_demand,
            "safety_stock": s.safety_stock,
            "safe_capacity": s.safe_capacity,
            "daily_dispatch_capacity": s.daily_dispatch_capacity,
            "remaining_dispatch_capacity": s.remaining_dispatch_capacity,
            "cost_per_kg": s.cost_per_kg,
        }
        for s in case.snapshots
    ]

    plans_data = [
        {
            "plan_id": p.id,
            "version": p.version,
            "solver_status": p.solver_status,
            "fulfillment_status": p.fulfillment_status,
            "allocated_kg": p.allocated_kg,
            "unmet_demand_kg": p.unmet_demand_kg,
            "planned_cost": p.planned_cost,
            "approval_status": p.approval_status,
            "lines": [
                {
                    "warehouse_id": l.warehouse_id,
                    "allocated_kg": l.allocated_kg,
                    "cost_per_kg": l.cost_per_kg,
                    "subtotal_cost": l.subtotal_cost,
                }
                for l in p.lines
            ],
        }
        for p in case.plans
    ]

    reservations_data = [
        {
            "reservation_id": r.id,
            "plan_id": r.plan_id,
            "warehouse_id": r.warehouse_id,
            "reserved_stock_kg": r.reserved_stock_kg,
            "reserved_dispatch_kg": r.reserved_dispatch_kg,
            "status": r.status,
        }
        for r in case.reservations
    ]

    return CaseDetailResponse(
        id=case.id,
        commodity_name=case.commodity_name,
        destination_region=case.destination_region,
        verified_demand=case.verified_demand,
        is_demand_verified=case.is_demand_verified,
        deadline_hours=case.deadline_hours,
        status=case.status,
        created_by=case.created_by,
        created_at=case.created_at,
        snapshots=snapshots_data,
        plans=plans_data,
        reservations=reservations_data,
    )


@router.post("/cases/{id}/validate", response_model=ValidationResponse)
def validate_case(id: str, db: Session = Depends(get_db)):
    """Pemeriksaan Data Readiness Gate pra-optimasi."""
    try:
        return CaseService.validate_gate(db=db, case_id=id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/cases/{id}/optimize", response_model=OptimizeResponse)
def optimize_case(id: str, db: Session = Depends(get_db)):
    """Eksekusi solver Google OR-Tools CP-SAT untuk formulasi alokasi awal (Plan v1)."""
    try:
        return CaseService.optimize_case(db=db, case_id=id)
    except ValueError as e:
        # Jika verifikasi demand belum terpenuhi, tolak dengan HTTP 422 Unprocessable Entity
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/plans/{id}/approve", response_model=ApproveResponse)
def approve_plan(id: str, req: ApproveRequest, db: Session = Depends(get_db)):
    """Persetujuan rencana alokasi dan penguncian reservasi stok aman & kuota harian secara atomik."""
    try:
        return ApprovalService.approve_plan(db=db, plan_id=id, req=req)
    except InsufficientCapacityConflictError as e:
        # HTTP 409 Conflict bila kapasitas aman atau kuota armada terlampaui
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except PlanAlreadyProcessedError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/plans/{id}")
def get_plan(id: str, db: Session = Depends(get_db)):
    """Membaca detail rencana alokasi spesifik."""
    plan = db.query(AllocationPlan).filter(AllocationPlan.id == id).first()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Rencana {id} tidak ditemukan.")

    return {
        "plan_id": plan.id,
        **recovery_metadata(db, plan.id),
        "case_id": plan.case_id,
        "version": plan.version,
        "is_replanning": plan.is_replanning,
        "solver_status": plan.solver_status,
        "fulfillment_status": plan.fulfillment_status,
        "total_demand_kg": plan.total_demand_kg,
        "allocated_kg": plan.allocated_kg,
        "unmet_demand_kg": plan.unmet_demand_kg,
        "planned_cost": plan.planned_cost,
        "estimated_dispatched_cost": plan.estimated_dispatched_cost,
        "shipments": [shipment_detail(db, s.id) for s in db.query(Shipment).filter_by(plan_id=id).order_by(Shipment.warehouse_id)],
        "approval_status": plan.approval_status,
        "lines": [
            {
                "warehouse_id": l.warehouse_id,
                "allocated_kg": l.allocated_kg,
                "cost_per_kg": l.cost_per_kg,
                "subtotal": l.subtotal_cost,
                "remaining_safe_capacity": l.remaining_safe_capacity,
                "remaining_dispatch_capacity": l.remaining_dispatch_capacity,
            }
            for l in plan.lines
        ],
    }


@router.get("/cases/{id}/audit", response_model=List[AuditLogResponse])
def get_case_audit(id: str, db: Session = Depends(get_db)):
    """Membaca riwayat rekam jejak audit keputusan untuk kasus tertentu."""
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.case_id == id)
        .order_by(AuditLog.timestamp.asc())
        .all()
    )
    return logs


@router.get("/forecasting/status", response_model=ForecastStatusResponse)
def get_forecasting_status():
    """Membaca status ketersediaan dataset, artefak model, dan kesegaran data AI Price Forecasting."""
    return ForecastingService.get_status()


@router.get("/forecasting/historical", response_model=HistoricalSeriesResponse)
def get_forecasting_historical(
    limit: int = Query(default=120, ge=1, le=1500),
    observed_only: bool = Query(default=True),
):
    """Membaca seri harga historis Cabai Rawit Merah beserta provenance dan status kesegaran data."""
    try:
        return ForecastingService.get_historical_series(limit=limit, observed_only=observed_only)
    except ForecastingUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.reason_code, "message": exc.message},
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("/forecasting/predictions", response_model=ForecastPredictionsResponse)
def get_forecasting_predictions(origin_date: Optional[str] = Query(default=None)):
    """Menjalankan inferensi harga Cabai Rawit Merah horizon 1, 3, dan 7 hari kalender menggunakan artefak model terlatih."""
    try:
        return ForecastingService.get_forecasts(origin_date=origin_date)
    except ForecastingUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.reason_code, "message": exc.message},
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("/forecasting/evaluation")
def get_forecasting_evaluation():
    """Membaca metadata model, hasil evaluasi kronologis Naive vs XGBoost, dan perbandingan aktual vs prediksi."""
    try:
        return ForecastingService.get_evaluation_and_metadata()
    except ForecastingUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.reason_code, "message": exc.message},
        )
