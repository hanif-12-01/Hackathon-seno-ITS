import test from 'node:test';
import assert from 'node:assert/strict';
import {summarizeDistribution} from '../lib/dashboard.ts';
const initial = {
  plan_id: 'v1',
  version: 1,
  allocated_kg: 8000,
  shipments: [
    {actual_dispatched_quantity_kg: 6000},
    {actual_dispatched_quantity_kg: 1950},
  ],
};
const recovery = {
  plan_id: 'v2',
  version: 2,
  allocated_kg: 290,
  shipments: [{actual_dispatched_quantity_kg: 0}],
};
test('absent operational data stays zero; no simulated fallback values', () => {
  assert.deepEqual(summarizeDistribution(null, [], null, null), {
    targetDemandKg: 0,
    allocatedKg: 0,
    actualDispatchedKg: 0,
    arrivedKg: 0,
    rejectedKg: 0,
    soundKg: 0,
    unmetDemandKg: 0,
    recoveryReservedKg: 0,
  });
});
test('physical acceptance, deficit and active recovery reservation remain distinct', () => {
  const c: any = {
    verified_demand: 8000,
    reservations: [
      {plan_id: 'v1', status: 'CLOSED', reserved_stock_kg: 8000},
      {plan_id: 'v2', status: 'ACTIVE', reserved_stock_kg: 290},
      {plan_id: 'another-case', status: 'ACTIVE', reserved_stock_kg: 1000},
    ],
  };
  const actual = summarizeDistribution(
    c,
    [initial, recovery] as any,
    {
      arrived_weight_kg: 7820,
      rejected_weight_kg: 110,
      sound_weight_kg: 7710,
    } as any,
    {unmet_demand_kg: 290} as any,
  );
  assert.equal(actual.actualDispatchedKg, 7950);
  assert.equal(actual.soundKg, 7710);
  assert.equal(actual.unmetDemandKg, 290);
  assert.equal(actual.recoveryReservedKg, 290);
});
test('zero measurements and zero final unmet demand are preserved', () => {
  const actual = summarizeDistribution(
    {verified_demand: 0, reservations: []} as any,
    [{...initial, allocated_kg: 0, shipments: []}] as any,
    {arrived_weight_kg: 0, rejected_weight_kg: 0, sound_weight_kg: 0} as any,
    {unmet_demand_kg: 0} as any,
  );
  assert.equal(actual.targetDemandKg, 0);
  assert.equal(actual.soundKg, 0);
  assert.equal(actual.unmetDemandKg, 0);
  assert.equal(actual.allocatedKg, 0);
  assert.equal(actual.actualDispatchedKg, 0);
});
