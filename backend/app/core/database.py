import os
import time
from pathlib import Path
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import inspect
from app.core.config import DATABASE_URL

SCHEMA_VERSION = 1
MUTATION_LOCK_ID = 72601423910831
SCHEMA_MARKER = "pangannadi_schema_version"


def classify_connection_error(exc: Exception) -> str:
    orig = getattr(exc, "orig", exc)
    msg = str(orig).lower()
    sqlstate = getattr(orig, "sqlstate", None) or getattr(exc, "sqlstate", None)

    if (
        "name or service not known" in msg
        or "nodename nor servname provided" in msg
        or "could not translate host name" in msg
        or "getaddrinfo" in msg
    ):
        return "DnsResolutionFailure"
    if (
        "timeout expired" in msg
        or "connection timed out" in msg
        or "operation timed out" in msg
        or "timeout" in msg
    ):
        return "TcpConnectionTimeout"
    if (
        "connection refused" in msg
        or "network is unreachable" in msg
        or "no route to host" in msg
    ):
        return "NetworkUnreachableOrRefused"
    if "root certificate file" in msg and (
        "no such file" in msg or "not found" in msg or "cannot open" in msg or "missing" in msg
    ):
        return "SslCertificateFileMissing"
    if (
        "certificate verify failed" in msg
        or "ssl certificate" in msg
        or "tlsv1_alert" in msg
        or "self-signed" in msg
        or "certificate verification" in msg
    ):
        return "TlsCertificateValidationFailed"
    if (
        "password authentication failed" in msg
        or sqlstate == "28P01"
        or "authentication failed" in msg
    ):
        return "AuthenticationRejection"
    if (
        "invalid connection option" in msg
        or "unrecognized configuration parameter" in msg
        or "invalid parameter" in msg
    ):
        return "InvalidLibpqOption"
    return "UnknownConnectionFailure"


def resolve_ca_root(url_query):
    if "sslrootcert" in url_query:
        path_str = url_query["sslrootcert"]
        if path_str == "system":
            return "system", "system_param", True, True
        f = Path(path_str)
        exists = f.is_file()
        readable = False
        if exists:
            try:
                with open(f, "rb") as fp:
                    readable = len(fp.read(32)) > 0
            except Exception:
                pass
        return path_str, "query_param", exists, readable

    # 1. Prioritize deterministically bundled trusted Mozilla CA bundle
    bundled_ca = (Path(__file__).resolve().parent / "ca-bundle.pem").resolve()
    if bundled_ca.is_file():
        readable = False
        try:
            with open(bundled_ca, "rb") as fp:
                data_head = fp.read(4096)
                readable = (len(data_head) > 0) and (b"-----BEGIN CERTIFICATE-----" in data_head)
        except Exception:
            pass
        if readable:
            return str(bundled_ca), "bundled", True, True

    # 2. Probe certifi.where()
    try:
        import certifi
        certifi_path = certifi.where()
        if certifi_path:
            f = Path(certifi_path)
            if f.is_file():
                readable = False
                try:
                    with open(f, "rb") as fp:
                        readable = len(fp.read(32)) > 0
                except Exception:
                    pass
                if readable:
                    return certifi_path, "certifi", True, True
    except Exception:
        pass

    # 3. Check libpq native 'system' support (PostgreSQL 16+)
    libpq_supports_system = False
    try:
        import psycopg
        libpq_supports_system = psycopg.pq.version() >= 160000
    except Exception:
        pass

    if libpq_supports_system:
        return "system", "system", True, True

    # 4. Fallback to Linux standard system bundle paths if libpq < 16
    for sys_ca in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
        f = Path(sys_ca)
        if f.is_file():
            try:
                with open(f, "rb") as fp:
                    if len(fp.read(32)) > 0:
                        return sys_ca, "system_bundle", True, True
            except Exception:
                pass

    return None, "none_available", False, False


url_obj = make_url(DATABASE_URL)
dialect = url_obj.get_backend_name()
if dialect not in ("sqlite", "postgresql"):
    raise RuntimeError("Unsupported database dialect.")
is_sqlite = dialect == "sqlite"

ca_source = "not_applicable"
ca_exists = False
ca_readable = False

if is_sqlite:
    connect_args = {"check_same_thread": False, "timeout": 10}
    engine_kwargs = {"connect_args": connect_args, "echo": False}
else:
    connect_args = {}
    first_label = (url_obj.host.split(".")[0] if url_obj.host else "").lower()
    if first_label.endswith("-pooler") or "-pooler." in (url_obj.host or ""):
        connect_args["prepare_threshold"] = None
    if "connect_timeout" not in url_obj.query and "connect_timeout" not in connect_args:
        connect_args["connect_timeout"] = 20

    ca_root, ca_source, ca_exists, ca_readable = resolve_ca_root(url_obj.query)
    if ca_root is None:
        raise RuntimeError("No trusted CA certificate bundle found; explicit TLS validation cannot proceed.")
    connect_args["sslrootcert"] = ca_root

    engine_kwargs = {
        "connect_args": connect_args,
        "pool_pre_ping": True,
        "echo": False,
    }
    if os.environ.get("VERCEL") == "1" or os.environ.get("PANGANNADI_HOSTED") == "1":
        from sqlalchemy.pool import NullPool
        engine_kwargs["poolclass"] = NullPool

engine = create_engine(
    DATABASE_URL,
    **engine_kwargs,
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


def begin_mutation(db):
    if db.new or db.dirty or db.deleted:
        raise ValueError("Session memiliki perubahan tertunda.")
    name = db.get_bind().dialect.name
    if name not in ("sqlite", "postgresql"):
        raise RuntimeError("Unsupported database dialect.")
    db.rollback()
    try:
        conn = db.connection()
        if name == "sqlite":
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            conn.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            conn.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
            conn.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": MUTATION_LOCK_ID})
        db.expire_all()
    except Exception:
        db.rollback()
        raise


def verify_postgres_schema(bind=None):
    from app.models import db_models
    from app.core import rate_limit
    target = bind if bind is not None else engine
    if target.dialect.name != "postgresql":
        raise RuntimeError("Schema verification requires PostgreSQL.")
    t0 = time.monotonic()
    try:
        with target.connect() as conn:
            versions = conn.execute(text(f"SELECT version FROM public.{SCHEMA_MARKER}")).scalars().all()
            if versions != [SCHEMA_VERSION]:
                raise RuntimeError("PostgreSQL schema version mismatch; explicit provisioning required.")
            inspector = inspect(conn)
            if inspector.default_schema_name != "public":
                raise RuntimeError("PostgreSQL requires public as the default schema.")
            if not set(Base.metadata.tables).issubset(inspector.get_table_names(schema="public")):
                raise RuntimeError("PostgreSQL schema is incomplete.")
            for table in Base.metadata.sorted_tables:
                columns = {c["name"]: c for c in inspector.get_columns(table.name, schema="public")}
                if any(c.name not in columns or (not c.nullable and columns[c.name]["nullable"]) for c in table.columns):
                    raise RuntimeError("PostgreSQL schema columns are incomplete.")
                foreign_keys = inspector.get_foreign_keys(table.name, schema="public")
                for constraint in table.foreign_key_constraints:
                    if not any(item["constrained_columns"] == [c.name for c in constraint.columns]
                        and item["referred_table"] == constraint.referred_table.name
                        and item["referred_columns"] == [element.column.name for element in constraint.elements]
                        for item in foreign_keys):
                        raise RuntimeError("PostgreSQL schema foreign keys are incomplete.")
                unique_columns = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table.name, schema="public")}
                for constraint in table.constraints:
                    if isinstance(constraint, UniqueConstraint) and tuple(c.name for c in constraint.columns) not in unique_columns:
                        raise RuntimeError("PostgreSQL schema uniqueness is incomplete.")
                expected_checks = sum(isinstance(c, CheckConstraint) and "typeof(" not in str(c.sqltext) for c in table.constraints)
                if len(inspector.get_check_constraints(table.name, schema="public")) < expected_checks:
                    raise RuntimeError("PostgreSQL schema check constraints are incomplete.")
    except SQLAlchemyError as err:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        category = classify_connection_error(err)
        err_cls = type(err).__name__
        orig_cls = type(getattr(err, "orig", err)).__name__
        sqlstate = getattr(err, "sqlstate", None) or getattr(getattr(err, "orig", None), "sqlstate", None)
        state_str = f" [{sqlstate}]" if sqlstate else ""

        try:
            import psycopg
            psycopg_ver = getattr(psycopg, "__version__", "unknown")
        except ImportError:
            psycopg_ver = "not_found"
        import sqlalchemy
        sa_ver = getattr(sqlalchemy, "__version__", "unknown")

        diag_msg = (
            f"PostgreSQL schema unavailable at Phase D: {category} ({err_cls}/{orig_cls}{state_str}) "
            f"in {elapsed_ms}ms (ca_source={ca_source}, ca_exists={ca_exists}, ca_readable={ca_readable}, "
            f"psycopg={psycopg_ver}, sqlalchemy={sa_ver}); explicit provisioning required."
        )
        raise RuntimeError(diag_msg) from None


def initialize_database():
    from app.models import db_models
    from app.core import rate_limit
    if engine.dialect.name == "postgresql":
        verify_postgres_schema()
        return
    if engine.dialect.name != "sqlite":
        raise RuntimeError("Unsupported database dialect.")
    upgrade_integrity_schema()
    upgrade_dispatch_schema()
    upgrade_receiving_schema()
    upgrade_replanning_schema()
    upgrade_checkpoint_schema()
    Base.metadata.create_all(bind=engine)


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
