import argparse

from sqlalchemy import inspect, text


def provision_postgres(bind, confirmed=False):
    from app.core.database import Base, MUTATION_LOCK_ID, SCHEMA_MARKER, SCHEMA_VERSION
    from app.models import db_models
    from app.core import rate_limit

    if not confirmed:
        raise RuntimeError("Explicit empty database confirmation is required.")
    if bind.dialect.name != "postgresql":
        raise RuntimeError("Provisioning requires PostgreSQL.")
    with bind.begin() as conn:
        conn.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
        conn.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
        conn.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": MUTATION_LOCK_ID})
        occupied = conn.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM pg_namespace
                WHERE nspname NOT IN ('public', 'information_schema')
                  AND left(nspname, 3) <> 'pg_'
                UNION ALL
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                UNION ALL
                SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public'
                UNION ALL
                SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = 'public'
                UNION ALL
                SELECT 1 FROM pg_extension WHERE extname <> 'plpgsql'
            )
        """)).scalar_one()
        if occupied or inspect(conn).default_schema_name != "public":
            raise RuntimeError("Provisioning requires a new empty PostgreSQL database using public schema.")
        conn.exec_driver_sql("SET LOCAL search_path = public")
        Base.metadata.create_all(conn, checkfirst=False)
        conn.exec_driver_sql(f"CREATE TABLE public.{SCHEMA_MARKER} (version INTEGER PRIMARY KEY)")
        conn.execute(text(f"INSERT INTO public.{SCHEMA_MARKER} (version) VALUES (:version)"), {"version": SCHEMA_VERSION})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-new-empty-database", action="store_true", required=True)
    args = parser.parse_args()
    try:
        from app.core.database import engine
        provision_postgres(engine, confirmed=args.confirm_new_empty_database)
    except Exception:
        parser.exit(1, "PostgreSQL provisioning refused or failed; database URL withheld.\n")
    print("PostgreSQL schema provisioned.")


if __name__ == "__main__":
    main()
