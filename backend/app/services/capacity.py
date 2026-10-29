"""Shared authoritative capacity; timestamps stored as naive UTC, dispatch day in WIB."""
from datetime import datetime, timedelta
from sqlalchemy import func
from app.models.db_models import InventorySnapshot, Reservation, DispatchUsage


def operational_day(timestamp=None):
    return ((timestamp or datetime.utcnow()) + timedelta(hours=7)).date().isoformat()


def current_snapshot(db, warehouse_id):
    return db.query(InventorySnapshot).filter_by(warehouse_id=warehouse_id).order_by(
        InventorySnapshot.snapshot_time.desc(), InventorySnapshot.id.desc()
    ).first()


def capacity(db, snapshot):
    day = operational_day()
    active = db.query(Reservation).filter_by(warehouse_id=snapshot.warehouse_id, status="ACTIVE")
    stock = active.with_entities(func.coalesce(func.sum(Reservation.reserved_stock_kg), 0)).scalar()
    dispatch = active.filter(Reservation.operational_date == day).with_entities(
        func.coalesce(func.sum(Reservation.reserved_dispatch_kg), 0)
    ).scalar()
    external = snapshot.external_reservations
    usage = db.get(DispatchUsage, (snapshot.warehouse_id, day))
    reported = snapshot.actual_dispatched_today if operational_day(snapshot.snapshot_time) == day else 0
    return {
        "id": snapshot.warehouse_id,
        "stock_physical_kg": snapshot.physical_stock,
        "local_demand_kg": snapshot.local_demand,
        "safety_stock_kg": snapshot.safety_stock,
        "active_stock_reservations_kg": stock + external,
        "daily_dispatch_capacity_kg": snapshot.daily_dispatch_capacity,
        "actual_dispatched_today_kg": max(reported, usage.dispatched_kg if usage else 0),
        "active_undispatched_dispatch_reservations_today_kg": dispatch + (external if operational_day(snapshot.snapshot_time) == day else 0),
        "cost_per_kg": snapshot.cost_per_kg,
    }
