import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base, get_db
from app.main import app
from app.models.db_models import ShipmentEvent, ReceivingEvent, ReplanningEvent


@pytest.mark.parametrize('model,action,identity,endpoint', [
    (ShipmentEvent, 'DISPATCH', {'shipment_id': 's'}, '/shipments/s/dispatch'),
    (ShipmentEvent, 'CANCEL', {'shipment_id': 's'}, '/shipments/s/cancel-reservation'),
    (ReceivingEvent, 'RECEIVE', {'case_id': 'c'}, '/cases/c/receipts'),
    (ReceivingEvent, 'FINALIZE', {'case_id': 'c'}, '/cases/c/reconcile'),
    (ReplanningEvent, None, {'case_id': 'c'}, '/cases/c/replan'),
])
def test_exact_persisted_operation_readback(tmp_path, model, action, identity, endpoint):
    engine = create_engine('sqlite:///' + (tmp_path / 'proof.sqlite3').as_posix(), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    body = {'transaction_ref': 'key', 'actor_id': 'operator'}
    payload = {**identity, **body}
    values = {'transaction_ref': 'key', 'case_id': 'c', 'request_payload': json.dumps(payload), 'response_payload': json.dumps({'saved': True})}
    if model is ShipmentEvent:
        values.pop('case_id')
        values['shipment_id'] = 's'
    if model is not ShipmentEvent:
        values['timestamp'] = datetime.now()
    if action:
        payload['action'] = action
        values['action_type'] = action
        values['request_payload'] = json.dumps(payload)
    else:
        values.update(source_reconciliation_id='r', decision_input='{}', operational_date='2026-10-29')
    with sessions() as db:
        db.add(model(**values))
        db.commit()
    def override():
        with sessions() as db:
            yield db
    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as client:
            result = client.get('/operations/key')
            assert result.status_code == 200
            assert result.json() == {'endpoint': endpoint, 'payload': body, 'result': {'saved': True}}
            assert client.get('/operations/absent').status_code == 404
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        engine.dispose()
