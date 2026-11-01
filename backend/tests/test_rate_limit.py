import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from app.core.rate_limit import DatabaseAdmission, admission_buckets
from app.core.security import OperatorPerimeter, password_hash


class AdmissionTests(unittest.TestCase):
    def test_shared_atomic_bounded_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            url = 'sqlite:///' + (Path(directory) / 'limits.sqlite3').as_posix()
            engines = [create_engine(url, connect_args={'timeout': 10}) for _ in range(2)]
            try:
                admission_buckets.create(engines[0])
                limiters = [DatabaseAdmission(engine) for engine in engines]
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(lambda i: limiters[i % 2].consume('alice')[0], range(60)))
                self.assertTrue(all(results))
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(lambda i: limiters[i % 2].record_failure('alice'), range(24)))
                self.assertFalse(limiters[1].consume('alice')[0])
                with engines[0].begin() as conn:
                    rows = conn.execute(select(admission_buckets)).all()
                    self.assertEqual(len(rows), 2)
                    self.assertEqual(next(row.attempts for row in rows if row.bucket.startswith('failure:')), 11)
                    conn.execute(admission_buckets.update().values(window=0))
                self.assertTrue(limiters[1].consume('alice')[0])
                for _ in range(10):
                    limiters[0].record_failure(None)
                self.assertFalse(limiters[1].consume(None)[0])
                with engines[0].connect() as conn:
                    self.assertEqual(len(conn.execute(select(admission_buckets)).all()), 3)
                with engines[0].begin() as conn:
                    conn.execute(admission_buckets.update().where(admission_buckets.c.bucket == 'global').values(attempts=599))
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(lambda i: limiters[i % 2].consume('bob')[0], range(24)))
                self.assertEqual(sum(results), 1)
            finally:
                for engine in engines:
                    engine.dispose()

    def test_fail_closed_and_before_hashing(self):
        accounts = json.dumps({'alice': {'password_hash': password_hash('test'), 'role': 'operator'}})
        with patch.dict(os.environ, {'PANGANNADI_OPERATOR_ACCOUNTS': accounts}):
            for failure, status in ((False, 429), (True, 503)):
                class Admission:
                    def consume(self, username):
                        if failure:
                            raise RuntimeError('private database error')
                        return False, 42
                app = FastAPI()
                app.add_middleware(OperatorPerimeter, origins=['https://stage.example.test'], admission=Admission())
                with patch.object(OperatorPerimeter, 'verify', side_effect=AssertionError('hash called')), TestClient(app) as client:
                    self.assertEqual(client.get('/health').json(), {'status': 'ok'})
                    response = client.get('/session')
                    self.assertEqual(response.status_code, status)
                    self.assertIn('Retry-After', response.headers)
                    self.assertNotIn('private', response.text)
                    self.assertEqual(response.headers['cache-control'], 'no-store')


if __name__ == '__main__':
    unittest.main()
