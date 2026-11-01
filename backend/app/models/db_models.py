import datetime
from sqlalchemy import (
    Column,
    String,
    Float,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    CheckConstraint,
    BigInteger,
    Numeric,
)
from sqlalchemy.orm import relationship
from app.core.database import Base


class Warehouse(Base):
    __tablename__ = "warehouses"

    id = Column(String(50), primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    location_name = Column(String(100), nullable=False)
    base_cost_per_kg = Column(Float, nullable=False)
    daily_dispatch_capacity_kg = Column(Float, nullable=False)

    snapshots = relationship("InventorySnapshot", back_populates="warehouse")
    allocation_lines = relationship("AllocationLine", back_populates="warehouse")
    reservations = relationship("Reservation", back_populates="warehouse")


class DistributionCase(Base):
    __tablename__ = "distribution_cases"

    id = Column(String(50), primary_key=True, index=True)
    commodity_name = Column(String(100), default="Cabai Rawit Merah")
    destination_region = Column(String(100), nullable=False)
    verified_demand = Column(Float, nullable=False)
    is_demand_verified = Column(Boolean, default=False)
    deadline_hours = Column(Integer, default=48)
    status = Column(String(50), default="DRAFT", index=True)
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    snapshots = relationship("InventorySnapshot", back_populates="case", cascade="all, delete-orphan")
    plans = relationship("AllocationPlan", back_populates="case", cascade="all, delete-orphan")
    reservations = relationship("Reservation", back_populates="case", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="case", cascade="all, delete-orphan")


class InventorySnapshot(Base):
    __tablename__ = "inventory_snapshots"

    id = Column(String(50), primary_key=True, index=True)
    case_id = Column(String(50), ForeignKey("distribution_cases.id"), nullable=False)
    warehouse_id = Column(String(50), ForeignKey("warehouses.id"), nullable=False)
    physical_stock = Column(Float, nullable=False)
    local_demand = Column(Float, nullable=False)
    safety_stock = Column(Float, nullable=False)
    active_stock_reservations = Column(Float, default=0.0)
    safe_capacity = Column(Float, nullable=False)
    daily_dispatch_capacity = Column(Float, nullable=False)
    actual_dispatched_today = Column(Float, default=0.0)
    active_undispatched_dispatch_reservations_today = Column(Float, default=0.0)
    remaining_dispatch_capacity = Column(Float, nullable=False)
    cost_per_kg = Column(Float, nullable=False)
    snapshot_time = Column(DateTime, default=datetime.datetime.utcnow)
    is_verified = Column(Boolean, default=True)
    external_reservations = Column(Float, nullable=False, default=0)

    case = relationship("DistributionCase", back_populates="snapshots")
    warehouse = relationship("Warehouse", back_populates="snapshots")


class AllocationPlan(Base):
    __tablename__ = "allocation_plans"
    __table_args__ = (UniqueConstraint("case_id", "version", name="uq_plan_case_version"),)

    id = Column(String(50), primary_key=True, index=True)
    case_id = Column(String(50), ForeignKey("distribution_cases.id"), nullable=False)
    version = Column(Integer, default=1)
    is_replanning = Column(Boolean, default=False)
    parent_plan_id = Column(String(50), nullable=True)
    solver_status = Column(String(50), nullable=False)
    fulfillment_status = Column(String(50), nullable=False)
    claim_category = Column(String(100), default="SOLVER_VERIFIED_OPTIMAL")
    total_demand_kg = Column(Float, nullable=False)
    allocated_kg = Column(Float, nullable=False)
    unmet_demand_kg = Column(Float, default=0.0)
    planned_cost = Column(Float, nullable=False)
    estimated_dispatched_cost = Column(Float, nullable=True)
    solve_time_ms = Column(Float, default=0.0)
    approval_status = Column(String(50), default="PENDING_APPROVAL", index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    case = relationship("DistributionCase", back_populates="plans")
    lines = relationship("AllocationLine", back_populates="plan", cascade="all, delete-orphan")
    reservations = relationship("Reservation", back_populates="plan", cascade="all, delete-orphan")
    approvals = relationship("Approval", back_populates="plan", cascade="all, delete-orphan")


class AllocationLine(Base):
    __tablename__ = "allocation_lines"

    id = Column(String(50), primary_key=True, index=True)
    plan_id = Column(String(50), ForeignKey("allocation_plans.id"), nullable=False)
    warehouse_id = Column(String(50), ForeignKey("warehouses.id"), nullable=False)
    allocated_kg = Column(Float, nullable=False)
    cost_per_kg = Column(Float, nullable=False)
    subtotal_cost = Column(Float, nullable=False)
    remaining_safe_capacity = Column(Float, default=0.0)
    remaining_dispatch_capacity = Column(Float, default=0.0)
    allocation_constraint_note = Column(String(255), nullable=True)

    plan = relationship("AllocationPlan", back_populates="lines")
    warehouse = relationship("Warehouse", back_populates="allocation_lines")


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (UniqueConstraint("plan_id", "warehouse_id", name="uq_reservation_plan_warehouse"),)

    id = Column(String(50), primary_key=True, index=True)
    plan_id = Column(String(50), ForeignKey("allocation_plans.id"), nullable=False)
    case_id = Column(String(50), ForeignKey("distribution_cases.id"), nullable=False)
    warehouse_id = Column(String(50), ForeignKey("warehouses.id"), nullable=False)
    reservation_type = Column(String(50), default="STOCK_AND_DISPATCH")
    reserved_stock_kg = Column(Float, nullable=False)
    reserved_dispatch_kg = Column(Float, nullable=False)
    status = Column(String(50), default="ACTIVE", index=True)  # ACTIVE, DISPATCHED, CANCELLED
    operational_date = Column(String(10), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    plan = relationship("AllocationPlan", back_populates="reservations")
    case = relationship("DistributionCase", back_populates="reservations")
    warehouse = relationship("Warehouse", back_populates="reservations")


class Approval(Base):
    __tablename__ = "approvals"

    id = Column(String(50), primary_key=True, index=True)
    plan_id = Column(String(50), ForeignKey("allocation_plans.id"), nullable=False)
    actor_id = Column(String(100), nullable=False)
    decision = Column(String(50), nullable=False)  # APPROVE, REJECT
    notes = Column(Text, nullable=True)
    approved_at = Column(DateTime, default=datetime.datetime.utcnow)

    plan = relationship("AllocationPlan", back_populates="approvals")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String(50), ForeignKey("distribution_cases.id"), nullable=True)
    action_type = Column(String(100), nullable=False, index=True)
    actor_id = Column(String(100), nullable=False)
    details = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    case = relationship("DistributionCase", back_populates="audit_logs")


class Shipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (
        UniqueConstraint("allocation_line_id", name="uq_shipment_allocation_line"),
        UniqueConstraint("plan_id", "warehouse_id", name="uq_shipment_plan_warehouse"),
        CheckConstraint("actual_dispatched_quantity_kg >= 0 AND unshipped_cancelled_quantity_kg >= 0 AND actual_dispatched_quantity_kg + unshipped_cancelled_quantity_kg <= planned_quantity_kg"),
    )
    id = Column(String(50), primary_key=True)
    allocation_line_id = Column(String(50), ForeignKey("allocation_lines.id"), nullable=False)
    plan_id = Column(String(50), ForeignKey("allocation_plans.id"), nullable=False)
    warehouse_id = Column(String(50), ForeignKey("warehouses.id"), nullable=False)
    planned_quantity_kg = Column(Integer, nullable=False)
    actual_dispatched_quantity_kg = Column(Integer, nullable=False, default=0)
    unshipped_cancelled_quantity_kg = Column(Integer, nullable=False, default=0)
    estimated_cost_cents = Column(BigInteger().with_variant(Numeric(38, 0), "postgresql"), nullable=False, default=0)
    operational_date = Column(String(10), nullable=False)
    dispatch_timestamp = Column(DateTime, nullable=True)
    manifest_status = Column(String(30), nullable=False, default="READY_TO_SHIP")
    transaction_ref = Column(String(100), unique=True, nullable=True)


class ShipmentEvent(Base):
    __tablename__ = "shipment_events"
    transaction_ref = Column(String(100), primary_key=True)
    shipment_id = Column(String(50), ForeignKey("shipments.id"), nullable=False)
    action_type = Column(String(50), nullable=False)
    request_payload = Column(Text, nullable=False)
    response_payload = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class ShipmentCheckpoint(Base):
    __tablename__ = "shipment_checkpoints"
    __table_args__ = (
        CheckConstraint("event_type IN ('IN_TRANSIT', 'AT_CHECKPOINT', 'DELAY_REPORTED')"),
        CheckConstraint("length(actor_id) BETWEEN 1 AND 100 AND length(transaction_ref) BETWEEN 1 AND 100"),
        CheckConstraint("note IS NULL OR length(note) <= 2000"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    shipment_id = Column(String(50), ForeignKey("shipments.id"), nullable=False, index=True)
    transaction_ref = Column(String(100), nullable=False, unique=True)
    event_type = Column(String(30), nullable=False)
    note = Column(Text, nullable=True)
    actor_id = Column(String(100), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    request_payload = Column(Text, nullable=False)
    response_payload = Column(Text, nullable=False)


class DispatchUsage(Base):
    __tablename__ = "dispatch_usage"
    __table_args__ = (CheckConstraint("dispatched_kg >= 0"),)
    warehouse_id = Column(String(50), ForeignKey("warehouses.id"), primary_key=True)
    operational_date = Column(String(10), primary_key=True)
    dispatched_kg = Column(Integer, nullable=False, default=0)


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (
        CheckConstraint("typeof(arrived_weight_kg) = 'integer' AND typeof(rejected_weight_kg) = 'integer'").ddl_if(dialect="sqlite"),
        CheckConstraint("0 <= rejected_weight_kg AND rejected_weight_kg <= arrived_weight_kg AND arrived_weight_kg <= total_dispatched_kg"),
        CheckConstraint("sound_weight_kg = arrived_weight_kg - rejected_weight_kg AND transit_discrepancy_kg = total_dispatched_kg - arrived_weight_kg"),
    )
    id = Column(String(50), primary_key=True)
    case_id = Column(String(50), ForeignKey("distribution_cases.id"), nullable=False, unique=True)
    plan_id = Column(String(50), ForeignKey("allocation_plans.id"), nullable=False, unique=True)
    total_dispatched_kg = Column(Integer, nullable=False)
    estimated_cost_cents = Column(BigInteger().with_variant(Numeric(38, 0), "postgresql"), nullable=False)
    arrived_weight_kg = Column(Integer, nullable=False)
    rejected_weight_kg = Column(Integer, nullable=False)
    sound_weight_kg = Column(Integer, nullable=False)
    transit_discrepancy_kg = Column(Integer, nullable=False)
    inspector_id = Column(String(100), nullable=False)
    inspection_notes = Column(Text, nullable=False)
    receipt_status = Column(String(30), nullable=False, default="RECORDED")
    inspected_at = Column(DateTime, nullable=False)


class ReceiptShipment(Base):
    """Combined receipt membership only; no invented per-warehouse arrived weights."""
    __tablename__ = "receipt_shipments"
    receipt_id = Column(String(50), ForeignKey("receipts.id"), primary_key=True)
    shipment_id = Column(String(50), ForeignKey("shipments.id"), primary_key=True, unique=True)


class Reconciliation(Base):
    __tablename__ = "reconciliations"
    __table_args__ = (
        CheckConstraint("target_demand_kg >= 0 AND total_sound_kg >= 0 AND unmet_demand_kg = CASE WHEN target_demand_kg > total_sound_kg THEN target_demand_kg - total_sound_kg ELSE 0 END"),
    )
    id = Column(String(50), primary_key=True)
    case_id = Column(String(50), ForeignKey("distribution_cases.id"), nullable=False, unique=True)
    receipt_id = Column(String(50), ForeignKey("receipts.id"), nullable=False, unique=True)
    target_demand_kg = Column(Integer, nullable=False)
    total_sound_kg = Column(Integer, nullable=False)
    unmet_demand_kg = Column(Integer, nullable=False)
    reconciliation_status = Column(String(30), nullable=False)
    case_status = Column(String(50), nullable=False)
    actor_id = Column(String(100), nullable=False)
    finalized_at = Column(DateTime, nullable=False)


class ReceivingEvent(Base):
    __tablename__ = "receiving_events"
    transaction_ref = Column(String(100), primary_key=True)
    case_id = Column(String(50), ForeignKey("distribution_cases.id"), nullable=False)
    action_type = Column(String(30), nullable=False)
    request_payload = Column(Text, nullable=False)
    response_payload = Column(Text, nullable=False)
    timestamp = Column(DateTime, nullable=False)


class ReplanningEvent(Base):
    __tablename__ = "replanning_events"
    transaction_ref = Column(String(100), primary_key=True)
    case_id = Column(String(50), ForeignKey("distribution_cases.id"), nullable=False)
    source_reconciliation_id = Column(String(50), ForeignKey("reconciliations.id"), nullable=False)
    plan_id = Column(String(50), ForeignKey("allocation_plans.id"), nullable=True, unique=True)
    operational_date = Column(String(10), nullable=False)
    request_payload = Column(Text, nullable=False)
    decision_input = Column(Text, nullable=False)
    response_payload = Column(Text, nullable=False)
    timestamp = Column(DateTime, nullable=False)
