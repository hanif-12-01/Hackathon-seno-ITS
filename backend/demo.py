import argparse
import json
import os
from pathlib import Path
import sys
import uuid
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser(description='Create a new local simulation database; never reset an existing database.')
    parser.add_argument('--database', type=Path, required=True)
    args = parser.parse_args()
    path = args.database.resolve()
    if path.suffix != '.sqlite3' or not path.parent.is_dir():
        parser.error('Use a .sqlite3 filename in an existing directory')
    try:
        with path.open('xb'):
            pass
    except FileExistsError:
        parser.error('Database already exists; choose a new filename. No reset is available.')
    os.environ['PANGANNADI_DATABASE_URL'] = 'sqlite:///' + path.as_posix()
    from fastapi.testclient import TestClient
    from app.main import app
    key = lambda: uuid.uuid4().hex
    with TestClient(app) as client:
        def post(endpoint, body=None, status=200):
            response = client.post(endpoint, json=body)
            if response.status_code != status:
                raise RuntimeError(f'{endpoint}: {response.status_code} {response.text}')
            return response.json()
        def get(endpoint):
            response = client.get(endpoint)
            response.raise_for_status()
            return response.json()
        warehouses = [dict(warehouse_id=f'WH-{name}',physical_stock=stock,local_demand=local,safety_stock=safety,daily_dispatch_capacity=quota,cost_per_kg=cost,snapshot_time=datetime.now(timezone.utc).isoformat()) for name,stock,local,safety,quota,cost in [('A',35000,20000,5000,6000,1200),('B',22000,15000,3000,4000,1500)]]
        cid = post('/cases',dict(destination_region='SAFE LOCAL SIMULATION',created_by='local-demo',verified_demand=8000,is_demand_verified=True,deadline_hours=48,warehouses=warehouses),201)['case_id']
        assert post(f'/cases/{cid}/validate')['is_ready']
        v1 = post(f'/cases/{cid}/optimize')
        assert v1['planned_cost'] == 10200000
        post(f"/plans/{v1['plan_id']}/approve",dict(actor_id='local-demo',decision='APPROVE'))
        initial = get(f"/plans/{v1['plan_id']}")
        for shipment in initial['shipments']:
            actual = 6000 if shipment['warehouse_id'] == 'WH-A' else 1950
            post(f"/shipments/{shipment['shipment_id']}/dispatch",dict(actor_id='local-demo',transaction_ref=key(),planned_quantity_kg=shipment['planned_quantity_kg'],actual_dispatched_quantity_kg=actual))
            if actual == 1950:
                post(f"/shipments/{shipment['shipment_id']}/cancel-reservation",dict(actor_id='local-demo',transaction_ref=key(),unshipped_cancelled_quantity_kg=50,cancellation_reason='SIMULATION unshipped'))
        capacities = {name:get(f'/warehouses/WH-{name}/capacity') for name in ['A','B']}
        assert capacities['A']['physical_stock'] == 29000 and capacities['B']['physical_stock'] == 20050
        receipt = post(f'/cases/{cid}/receipts',dict(inspector_id='local-demo',transaction_ref=key(),plan_id=v1['plan_id'],shipment_ids=[s['shipment_id'] for s in initial['shipments']],total_dispatched_kg=7950,arrived_weight_kg=7820,rejected_weight_kg=110),201)
        assert receipt['sound_weight_kg'] == 7710 and receipt['transit_discrepancy_kg'] == 130
        final = post(f'/cases/{cid}/reconcile',dict(actor_id='local-demo',transaction_ref=key(),receipt_id=receipt['receipt_id']))
        assert final['unmet_demand_kg'] == 290
        history = get(f"/plans/{v1['plan_id']}")
        v2 = post(f'/cases/{cid}/replan',dict(actor_id='local-demo',transaction_ref=key(),source_reconciliation_id=final['reconciliation_id']))
        assert v2['allocated_kg'] == 290 and v2['planned_cost'] == 435000
        post(f"/plans/{v2['plan_id']}/approve",dict(actor_id='local-demo',decision='APPROVE'))
        assert get(f"/plans/{v1['plan_id']}") == history
        recovery = get(f"/plans/{v2['plan_id']}")
        assert all(s['actual_dispatched_quantity_kg'] == 0 for s in recovery['shipments'])
        assert get(f'/cases/{cid}/reconciliation')['unmet_demand_kg'] == 290
        print(json.dumps(dict(database=str(path),case_id=cid,v1=v1,v2=v2,reconciliation=final,recovery_status='RESERVED_NOT_DELIVERED'),indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
