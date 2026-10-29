from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from itertools import product
from statistics import median
from time import perf_counter
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.services.capacity import capacity
from app.services.optimizer import OptimizationEngine
from test_optimizer import get_demo_warehouses_initial


def greedy(demand, warehouses):
    if type(demand) is not int or not 0 <= demand <= 1_000_000_000:
        raise ValueError('Invalid demand')
    ids = [str(w['id']) for w in warehouses]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate warehouse')
    bounds = {}
    for w in warehouses:
        for key, value in w.items():
            if key not in ('id', 'name', 'cost_per_kg', 'base_cost_per_kg'):
                if not isinstance(value, (int, float)) or value < 0 or int(value) != value:
                    raise ValueError('Invalid quantity')
        b = OptimizationEngine.calculate_bounds(w)
        cost = Decimal(str(b['cost_per_kg'])) * 100
        if not cost.is_finite() or not 0 < cost <= 100_000_000_000 or cost != cost.to_integral_value():
            raise ValueError('Invalid cost')
        bounds[str(w['id'])] = (b['upper_bound'], int(cost))
    if sum(c * min(demand, upper) for upper, c in bounds.values()) > 2**62:
        raise ValueError('Cost domain overflow')
    remaining = demand
    allocations = dict.fromkeys(ids, 0)
    for key in sorted(ids, key=lambda key: (bounds[key][1], key)):
        allocations[key] = min(remaining, bounds[key][0])
        remaining -= allocations[key]
    return dict(allocations=allocations, unmet_demand_kg=remaining,
                cost_cents=sum(allocations[key] * bounds[key][1] for key in ids), status='FEASIBLE_GREEDY')


def scenarios():
    initial = get_demo_warehouses_initial()
    recovery = deepcopy(initial)
    recovery[0].update(stock_physical_kg=29000, actual_dispatched_today_kg=6000)
    recovery[1].update(stock_physical_kg=20050, actual_dispatched_today_kg=1950)
    external = []
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            for w in recovery:
                snapshot = SimpleNamespace(warehouse_id=w['id'], physical_stock=w['stock_physical_kg'],
                    local_demand=w['local_demand_kg'], safety_stock=w['safety_stock_kg'],
                    external_reservations=1800 if w['id'] == 'WH-B' else 0,
                    daily_dispatch_capacity=w['daily_dispatch_capacity_kg'],
                    actual_dispatched_today=w['actual_dispatched_today_kg'],
                    snapshot_time=datetime.utcnow(), cost_per_kg=w['cost_per_kg'])
                external.append(capacity(db, snapshot))
    finally:
        engine.dispose()
    expensive = deepcopy(initial)
    expensive[0]['cost_per_kg'] = 1_000_000_000
    expensive[1]['cost_per_kg'] = 0.01
    ties = deepcopy(initial)
    ties[1]['cost_per_kg'] = 1200
    exhausted = deepcopy(initial)
    for w in exhausted:
        w['actual_dispatched_today_kg'] = w['daily_dispatch_capacity_kg']
    return [
        ('full', 8000, initial, (0, 1_020_000_000)),
        ('shortage', 15000, initial, (5000, 1_320_000_000)),
        ('quota_exhausted', 290, exhausted, (290, 0)),
        ('external_reservation', 290, external, (40, 37_500_000)),
        ('low_demand', 100, initial, (0, 12_000_000)),
        ('expensive_cheap', 8000, expensive, (0, 400_000_000_004_000)),
        ('recovery', 290, recovery, (0, 43_500_000)),
        ('ties', 8000, ties, (0, 960_000_000)),
        ('zero_demand', 0, initial, (0, 0)),
    ]


def objective(demand, warehouses, result):
    allocations = result['allocations']
    assert set(allocations) == {str(w['id']) for w in warehouses}
    for w in warehouses:
        qty = allocations[str(w['id'])]
        assert type(qty) is int and 0 <= qty <= OptimizationEngine.calculate_bounds(w)['upper_bound']
    unmet = result['unmet_demand_kg']
    assert type(unmet) is int and unmet >= 0 and sum(allocations.values()) + unmet == demand
    cents = sum(allocations[str(w['id'])] * int(Decimal(str(w['cost_per_kg'])) * 100) for w in warehouses)
    return unmet, cents


def benchmark(repeats=5):
    rows = []
    for name, demand, warehouses, expected in scenarios():
        timings = {'greedy': [], 'cp_sat': []}
        results = {}
        for _ in range(repeats):
            for method, solve in [('greedy', greedy), ('cp_sat', OptimizationEngine.solve_allocation)]:
                start = perf_counter()
                result = solve(demand, deepcopy(warehouses))
                timings[method].append((perf_counter() - start) * 1000)
                actual = objective(demand, warehouses, result)
                assert actual == expected, (name, method, actual, expected)
                if method == 'cp_sat':
                    assert result['solver_status'] == 'OPTIMAL'
                results[method] = result
        rows.append(dict(scenario=name, demand_kg=demand, inputs=warehouses, expected_objective=expected,
            objective=list(expected), results=results, feasible=True, runtime_ms=timings,
            median_ms={key: median(values) for key, values in timings.items()}))
    return rows


@pytest.mark.parametrize('name,demand,warehouses,expected', scenarios(), ids=lambda value: value if isinstance(value, str) else None)
def test_greedy_matches_cp_sat(name, demand, warehouses, expected):
    original = deepcopy(warehouses)
    g = greedy(demand, warehouses)
    cp = OptimizationEngine.solve_allocation(demand, warehouses)
    assert objective(demand, warehouses, g) == objective(demand, warehouses, cp) == expected
    assert cp['solver_status'] == 'OPTIMAL'
    assert greedy(demand, list(reversed(warehouses))) == g
    assert warehouses == original


def test_tiny_exhaustive_lexicographic_oracle():
    for a, b, ca, cb, demand in product(range(3), range(3), (1, 2), (1, 2), range(5)):
        warehouses = [dict(id='A', safe_available=a, dispatch_available=a, cost_per_kg=ca),
                      dict(id='B', safe_available=b, dispatch_available=b, cost_per_kg=cb)]
        expected = min((demand-x-y, (ca*x+cb*y)*100) for x in range(a+1) for y in range(b+1) if x+y <= demand)
        assert objective(demand, warehouses, greedy(demand, warehouses)) == expected


@pytest.mark.parametrize('demand,warehouses', [(-1, []), (0.5, []), (1, [dict(id='A', cost_per_kg=0)]),
    (1, [dict(id='A', cost_per_kg=1.001)]), (1, [dict(id='A', cost_per_kg=1, safe_available=-1)]),
    (1, [dict(id='A', cost_per_kg=1), dict(id='A', cost_per_kg=2)])])
def test_greedy_rejects_invalid(demand, warehouses):
    with pytest.raises(ValueError):
        greedy(demand, warehouses)
