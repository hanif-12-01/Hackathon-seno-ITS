from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.core.database import Base, get_db


def test_case_selector_reads_persisted_records(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'selector.sqlite3'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    previous = app.dependency_overrides.copy()
    def db():
        with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = db
    try:
        client = TestClient(app)
        assert client.get('/cases').json() == []
        created = client.post('/cases', json={
            'destination_region': 'Browser selector', 'verified_demand': 17,
            'created_by': 'test', 'warehouses': [{'warehouse_id': 'SELECTOR',
                'physical_stock': 20, 'local_demand': 0, 'safety_stock': 0,
                'daily_dispatch_capacity': 20, 'cost_per_kg': 1}]})
        assert created.status_code == 201
        rows = client.get('/cases').json()
        assert len(rows) == 1 and rows[0]['id'] == created.json()['case_id']
        assert rows[0]['verified_demand'] == 17
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        engine.dispose()


def test_cors_is_explicit_without_credentials():
    client = TestClient(app)
    allowed = client.options('/cases', headers={'Origin': 'http://127.0.0.1:5173', 'Access-Control-Request-Method': 'POST'})
    assert allowed.headers['access-control-allow-origin'] == 'http://127.0.0.1:5173'
    assert 'access-control-allow-credentials' not in allowed.headers
    forbidden = client.options('/cases', headers={'Origin': 'https://unlisted.example', 'Access-Control-Request-Method': 'POST'})
    assert forbidden.status_code == 400
    assert 'access-control-allow-origin' not in forbidden.headers
