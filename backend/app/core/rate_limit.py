import hashlib

from sqlalchemy import Column, Integer, String, Table, case, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.database import Base, engine


admission_buckets = Table(
    'operator_admission_buckets', Base.metadata,
    Column('bucket', String(80), primary_key=True),
    Column('window', Integer, nullable=False),
    Column('attempts', Integer, nullable=False),
)


class DatabaseAdmission:
    def __init__(self, bind=None):
        self.engine = bind if bind is not None else engine

    def failure_key(self, username):
        return 'failure:' + hashlib.sha256(username.encode()).hexdigest() if username else 'failure:unknown'

    def window(self, conn):
        if conn.dialect.name == 'postgresql':
            conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            conn.execute(text("SET LOCAL statement_timeout = '10s'"))
            now = int(conn.scalar(text('SELECT FLOOR(EXTRACT(EPOCH FROM clock_timestamp()))')))
        else:
            now = int(conn.scalar(text("SELECT strftime('%s', 'now')")))
        return now // 60, 60 - now % 60

    def increment(self, conn, key, window, limit):
        insert = {'postgresql': pg_insert, 'sqlite': sqlite_insert}[conn.dialect.name]
        minimum = func.min if conn.dialect.name == 'sqlite' else func.least
        stmt = insert(admission_buckets).values(bucket=key, window=window, attempts=1)
        return conn.scalar(stmt.on_conflict_do_update(
            index_elements=[admission_buckets.c.bucket],
            set_={'window': window, 'attempts': case(
                (admission_buckets.c.window == window, minimum(admission_buckets.c.attempts + 1, limit + 1)),
                else_=1)},
        ).returning(admission_buckets.c.attempts))

    def consume(self, username):
        last_exc = None
        for attempt in range(2):
            try:
                with self.engine.begin() as conn:
                    window, retry = self.window(conn)
                    count = self.increment(conn, 'global', window, 600)
                    failures = conn.scalar(select(admission_buckets.c.attempts).where(
                        admission_buckets.c.bucket == self.failure_key(username),
                        admission_buckets.c.window == window,
                    )) or 0
                return count <= 600 and failures < 10, retry
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    import time
                    time.sleep(0.1)
                    continue
                raise last_exc

    def record_failure(self, username):
        last_exc = None
        for attempt in range(2):
            try:
                with self.engine.begin() as conn:
                    window, _ = self.window(conn)
                    self.increment(conn, self.failure_key(username), window, 10)
                return
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    import time
                    time.sleep(0.1)
                    continue
                raise last_exc
