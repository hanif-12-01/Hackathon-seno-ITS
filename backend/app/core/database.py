from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import inspect
from app.core.config import DATABASE_URL

is_sqlite = DATABASE_URL.startswith("sqlite")

connect_args = {"check_same_thread": False, "timeout": 10} if is_sqlite else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=False,
)

if is_sqlite:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")  # Wait up to 5s for locks
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def upgrade_integrity_schema():
    """Additive SQLite upgrade; duplicate legacy reservations abort, never delete data."""
    if not is_sqlite:
        return
    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            tables = inspect(conn).get_table_names()
            if "inventory_snapshots" in tables:
                columns = {c["name"] for c in inspect(conn).get_columns("inventory_snapshots")}
                if "external_reservations" not in columns:
                    # Old snapshots combine internal/external commitments. Ambiguous
                    # nonzero legacy commitments need explicit re-verification.
                    conn.exec_driver_sql("ALTER TABLE inventory_snapshots ADD COLUMN external_reservations FLOAT NOT NULL DEFAULT 0")
                    conn.exec_driver_sql("UPDATE inventory_snapshots SET is_verified=0 WHERE active_stock_reservations > 0 OR active_undispatched_dispatch_reservations_today > 0")
            if "reservations" in tables:
                columns = {c["name"] for c in inspect(conn).get_columns("reservations")}
                if "operational_date" not in columns:
                    conn.exec_driver_sql("ALTER TABLE reservations ADD COLUMN operational_date VARCHAR(10)")
                    conn.exec_driver_sql("UPDATE reservations SET operational_date=date(created_at, '+7 hours')")
                conn.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_reservation_plan_warehouse ON reservations(plan_id, warehouse_id)")
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def upgrade_dispatch_schema():
    """Create M5A tables explicitly; backfill only unambiguous active legacy approvals."""
    from app.models.db_models import Shipment, ShipmentEvent, DispatchUsage
    from app.services.shipment_service import initialize_shipments
    from app.models.db_models import AllocationPlan, Reservation
    if not is_sqlite:
        raise RuntimeError("Migrasi dispatch PoC hanya mendukung SQLite.")
    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            for table in (Shipment.__table__, ShipmentEvent.__table__, DispatchUsage.__table__):
                table.create(conn, checkfirst=True)
            if "allocation_plans" in inspect(conn).get_table_names():
                with SessionLocal(bind=conn, join_transaction_mode="rollback_only") as db:
                    for plan in db.query(AllocationPlan).filter_by(approval_status="APPROVED"):
                        if db.query(Shipment).filter_by(plan_id=plan.id).first():
                            continue
                        positive = [line for line in plan.lines if line.allocated_kg > 0]
                        for line in positive:
                            row = db.query(Reservation).filter_by(plan_id=plan.id, warehouse_id=line.warehouse_id).one_or_none()
                            if row is None or row.status != "ACTIVE" or row.reserved_stock_kg != line.allocated_kg or row.reserved_dispatch_kg != line.allocated_kg:
                                raise RuntimeError("Legacy approved reservation ambigu; migrasi dispatch dibatalkan.")
                        initialize_shipments(db, plan)
                    db.flush()
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def upgrade_receiving_schema():
    """Add M5B tables in one SQLite transaction; existing history is untouched."""
    from app.models.db_models import Receipt, ReceiptShipment, Reconciliation, ReceivingEvent
    if not is_sqlite:
        raise RuntimeError("Migrasi receiving PoC hanya mendukung SQLite.")
    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            for model in (Receipt, ReceiptShipment, Reconciliation, ReceivingEvent):
                model.__table__.create(conn, checkfirst=True)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def upgrade_checkpoint_schema():
    from app.models.db_models import ShipmentCheckpoint
    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            ShipmentCheckpoint.__table__.create(conn, checkfirst=True)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def upgrade_replanning_schema():
    """Add event ledger and version uniqueness without rewriting existing plans."""
    from app.models.db_models import ReplanningEvent
    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            if "allocation_plans" in inspect(conn).get_table_names():
                conn.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_plan_case_version ON allocation_plans(case_id, version)")
            ReplanningEvent.__table__.create(conn, checkfirst=True)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
