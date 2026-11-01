from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from app.core import database, rate_limit
from app.models.db_models import Base, DistributionCase, Receipt, Reconciliation, Shipment
from app.services.case_service import CaseService
from provision_postgres import provision_postgres


def fake_session(name):
    db = MagicMock()
    db.new = db.dirty = db.deleted = set()
    db.get_bind.return_value.dialect.name = name
    return db


def test_postgres_mutation_protocol_without_connection():
    db = fake_session("postgresql")
    database.begin_mutation(db)
    conn = db.connection.return_value
    assert [call.args[0] for call in conn.exec_driver_sql.call_args_list] == [
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        "SET LOCAL lock_timeout = '5s'",
        "SET LOCAL statement_timeout = '30s'",
    ]
    statement, params = conn.execute.call_args.args
    assert str(statement) == "SELECT pg_advisory_xact_lock(:lock_id)"
    assert params == {"lock_id": database.MUTATION_LOCK_ID}
    db.expire_all.assert_called_once_with()
    assert [call[0] for call in db.mock_calls].index("connection().execute") < [call[0] for call in db.mock_calls].index("expire_all")
    assert not db.commit.called


def test_mutation_failure_rolls_back():
    db = fake_session("postgresql")
    db.connection.return_value.execute.side_effect = RuntimeError("lock failed")
    with pytest.raises(RuntimeError, match="lock failed"):
        database.begin_mutation(db)
    assert db.rollback.call_count == 2
    assert not db.expire_all.called


@pytest.mark.parametrize("name", ["mysql", "mssql"])
def test_mutation_rejects_unsupported_dialect(name):
    db = fake_session(name)
    with pytest.raises(RuntimeError, match="Unsupported"):
        database.begin_mutation(db)
    assert not db.connection.called


def test_mutation_rejects_pending_changes():
    db = fake_session("postgresql")
    db.new = {object()}
    with pytest.raises(ValueError):
        database.begin_mutation(db)
    assert not db.rollback.called


def test_sqlite_mutation_preserves_begin_immediate_and_rollback(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mutation.db'}")
    Base.metadata.create_all(engine)
    statements = []
    event.listen(engine, "before_cursor_execute", lambda conn, cursor, statement, params, context, many: statements.append(statement))
    with Session(engine) as db:
        database.begin_mutation(db)
        assert statements == ["BEGIN IMMEDIATE"]
        db.add(DistributionCase(id="rollback", destination_region="test", verified_demand=0, created_by="test"))
        db.flush()
        db.rollback()
        assert db.get(DistributionCase, "rollback") is None
    engine.dispose()


def test_postgres_ddl_compilation_not_integration():
    pg = {table.name: str(CreateTable(table).compile(dialect=postgresql.dialect())) for table in Base.metadata.sorted_tables}
    sq = str(CreateTable(Receipt.__table__).compile(dialect=sqlite.dialect()))
    assert "typeof(" in sq
    assert not any("typeof(" in ddl or "max(" in ddl for ddl in pg.values())
    assert "CASE WHEN" in pg[Reconciliation.__tablename__]
    assert "NUMERIC(38, 0)" in pg[Shipment.__tablename__]
    assert "BIGINT" in str(CreateTable(Shipment.__table__).compile(dialect=sqlite.dialect()))


def test_schema_verification_read_only(monkeypatch, tmp_path):
    bind = MagicMock()
    bind.dialect.name = "postgresql"
    conn = bind.connect.return_value.__enter__.return_value
    conn.execute.return_value.scalars.return_value.all.return_value = [database.SCHEMA_VERSION]
    inspector = MagicMock()
    inspector.default_schema_name = "public"
    inspector.get_table_names.return_value = list(Base.metadata.tables)
    reference = create_engine(f"sqlite:///{tmp_path / 'schema-reference.db'}")
    Base.metadata.create_all(reference)
    reference_inspector = inspect(reference)
    for method in ("get_columns", "get_foreign_keys", "get_unique_constraints", "get_check_constraints"):
        getattr(inspector, method).side_effect = lambda table, schema=None, method=method: getattr(reference_inspector, method)(table)
    monkeypatch.setattr(database, "inspect", lambda connection: inspector)
    database.verify_postgres_schema(bind)
    assert str(conn.execute.call_args.args[0]).startswith("SELECT version FROM public.")
    assert not conn.commit.called
    assert not conn.exec_driver_sql.called
    conn.execute.return_value.scalars.return_value.all.return_value = []
    with pytest.raises(RuntimeError, match="version mismatch"):
        database.verify_postgres_schema(bind)
    conn.execute.side_effect = OperationalError("SELECT", {}, Exception("secret-url"))
    with pytest.raises(RuntimeError, match="unavailable") as exc:
        database.verify_postgres_schema(bind)
    assert "secret-url" not in str(exc.value)


def test_postgres_initialization_never_creates_schema(monkeypatch):
    monkeypatch.setattr(database, "engine", SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    verify = MagicMock()
    monkeypatch.setattr(database, "verify_postgres_schema", verify)
    create = MagicMock(side_effect=AssertionError("automatic DDL"))
    monkeypatch.setattr(Base.metadata, "create_all", create)
    database.initialize_database()
    verify.assert_called_once_with()
    create.assert_not_called()


def test_provision_refuses_without_confirmation_or_sqlite():
    bind = MagicMock()
    bind.dialect.name = "postgresql"
    with pytest.raises(RuntimeError, match="confirmation"):
        provision_postgres(bind)
    bind.begin.assert_not_called()
    bind.dialect.name = "sqlite"
    with pytest.raises(RuntimeError, match="requires PostgreSQL"):
        provision_postgres(bind, confirmed=True)
    bind.begin.assert_not_called()


def test_provision_refuses_nonempty_postgres_without_ddl():
    bind = MagicMock()
    bind.dialect.name = "postgresql"
    conn = bind.begin.return_value.__enter__.return_value
    conn.execute.return_value.scalar_one.return_value = True
    with pytest.raises(RuntimeError, match="new empty"):
        provision_postgres(bind, confirmed=True)
    assert not any("CREATE" in call.args[0] for call in conn.exec_driver_sql.call_args_list)


def test_postgres_gate_state_and_audit_share_one_commit(monkeypatch, tmp_path):
    from app.services import case_service
    engine = create_engine(f"sqlite:///{tmp_path / 'atomic.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(DistributionCase(id="atomic", destination_region="test", verified_demand=0,
            is_demand_verified=True, created_by="test", status="DRAFT"))
        db.commit()
        monkeypatch.setattr(db, "get_bind", lambda **kwargs: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
        case = SimpleNamespace(status="DRAFT", created_by="test")
        query = MagicMock()
        query.filter.return_value.first.return_value = case
        query.filter.return_value.all.return_value = []
        monkeypatch.setattr(db, "query", lambda model: query)
        monkeypatch.setattr(case_service.DataReadinessGate, "validate_case", lambda case, snapshots: {"is_ready": True})
        commit = MagicMock()
        monkeypatch.setattr(db, "commit", commit)
        def fail(*args, **kwargs):
            assert case.status == "VALIDATED"
            commit.assert_not_called()
            raise RuntimeError("audit failed")
        monkeypatch.setattr(case_service.AuditService, "record_log", fail)
        with pytest.raises(RuntimeError, match="audit failed"):
            CaseService._validate_gate(db, "atomic")
        commit.assert_not_called()
    engine.dispose()


def test_validation_audit_failure_rolls_back_sqlite_state(monkeypatch, tmp_path):
    from app.services import case_service
    from app.models.schemas import CaseCreateRequest
    engine = create_engine(f"sqlite:///{tmp_path / 'validation.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as db:
        case = CaseService.create_case(db, CaseCreateRequest(destination_region="test", verified_demand=10,
            is_demand_verified=True, created_by="creator", warehouses=[dict(warehouse_id="WH-TEST",
                physical_stock=100, local_demand=0, safety_stock=0, daily_dispatch_capacity=100, cost_per_kg=1)]))
        case_id = case.id
        def fail(*args, **kwargs):
            db.flush()
            raise RuntimeError("audit failed")
        monkeypatch.setattr(case_service.AuditService, "record_log", fail)
        with pytest.raises(RuntimeError, match="audit failed"):
            CaseService.validate_gate(db, case_id, actor_id="second")
        assert db.get(DistributionCase, case_id).status == "DRAFT"
    engine.dispose()


@pytest.mark.parametrize("operation", ["validate_gate", "optimize_case"])
def test_postgres_case_locks_before_authoritative_read(monkeypatch, operation):
    from app.services import case_service
    db = fake_session("postgresql")
    calls = []
    monkeypatch.setattr(case_service, "begin_mutation", lambda session: calls.append("lock"))
    def read(session, case_id, actor_id=None):
        calls.append("read")
        raise ValueError("injected")
    monkeypatch.setattr(CaseService, "_" + operation, read)
    with pytest.raises(ValueError, match="injected"):
        getattr(CaseService, operation)(db, "case")
    assert calls == ["lock", "read"]
    db.rollback.assert_called_once_with()
