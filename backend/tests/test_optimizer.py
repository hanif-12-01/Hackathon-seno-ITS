"""
Unit & Integration Tests for PanganNadi Adaptive Optimization Engine
Verifying:
  - E1: Safety Stock Preservation
  - E2: Optimization Formulation vs Heuristik (Plan v1 Constrained vs Unconstrained)
  - E6: Adaptive Replanning Execution (Plan v2 Baseline 290 kg)
  - TC-DISPATCH-RESERVED-01: Undispatched Reservation Quota & Stock Deduction (250 kg boundary, unmet 40 kg)
  - TC-COST-EST-01: Planned Cost vs Estimated Dispatched Cost Separation
"""

import sys
import os

# Add app directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from app.services.optimizer import OptimizationEngine, solve_plan_v1, solve_recovery_plan_v2


def get_demo_warehouses_initial():
    """Dataset gudang awal sebelum alokasi Plan v1 [SIMULASI]."""
    return [
        {
            "id": "WH-A",
            "name": "Gudang A (Sentra A)",
            "stock_physical_kg": 35000,
            "local_demand_kg": 20000,
            "safety_stock_kg": 5000,
            "active_stock_reservations_kg": 0,
            "daily_dispatch_capacity_kg": 6000,
            "actual_dispatched_today_kg": 0,
            "active_undispatched_dispatch_reservations_today_kg": 0,
            "cost_per_kg": 1200,
        },
        {
            "id": "WH-B",
            "name": "Gudang B (Sentra B)",
            "stock_physical_kg": 22000,
            "local_demand_kg": 15000,
            "safety_stock_kg": 3000,
            "active_stock_reservations_kg": 0,
            "daily_dispatch_capacity_kg": 4000,
            "actual_dispatched_today_kg": 0,
            "active_undispatched_dispatch_reservations_today_kg": 0,
            "cost_per_kg": 1500,
        },
    ]


class TestOptimizationEngine:
    def test_plan_v1_unconstrained_benchmark(self):
        """
        Formulasi tanpa kendala kuota harian:
        Gudang A memiliki kapasitas aman 10.000 kg dan biaya lebih murah (1.200 vs 1.500).
        Solver wajib memilih A = 8.000 kg, B = 0 kg dengan total biaya Rp 9.600.000.
        """
        whs = get_demo_warehouses_initial()
        # Hilangkan batasan kuota pengiriman
        whs[0]["daily_dispatch_capacity_kg"] = 50000
        whs[1]["daily_dispatch_capacity_kg"] = 50000

        result = solve_plan_v1(warehouses=whs, demand_kg=8000)

        assert result["solver_status"] == "OPTIMAL"
        assert result["fulfillment_status"] == "FULL_FULFILLMENT"
        assert result["allocated_kg"] == 8000
        assert result["unmet_demand_kg"] == 0
        assert result["allocations"]["WH-A"] == 8000
        assert result["allocations"]["WH-B"] == 0
        assert result["planned_cost"] == 9_600_000

    def test_plan_v1_constrained_scenario(self):
        """
        Skenario Demo Plan v1 (Dengan kendala kuota pengiriman harian):
        Gudang A dibatasi kuota 6.000 kg/hari.
        Solver wajib mengalokasikan A = 6.000 kg, B = 2.000 kg dengan total biaya Rp 10.200.000.
        """
        whs = get_demo_warehouses_initial()
        result = solve_plan_v1(warehouses=whs, demand_kg=8000)

        assert result["solver_status"] == "OPTIMAL"
        assert result["fulfillment_status"] == "FULL_FULFILLMENT"
        assert result["allocated_kg"] == 8000
        assert result["unmet_demand_kg"] == 0
        assert result["allocations"]["WH-A"] == 6000
        assert result["allocations"]["WH-B"] == 2000
        assert result["planned_cost"] == 10_200_000

    def test_e1_safety_stock_hard_preservation(self):
        """
        E1: Bukti matematis cadangan minimum (safety stock) tidak pernah dilanggar
        bahkan saat demand melebihi total stok aman bebas.
        """
        whs = get_demo_warehouses_initial()
        # safe capacity A = 10.000, safe capacity B = 4.000 -> Total = 14.000 kg
        demand_excess = 20000  # Melebihi total safe capacity

        result = OptimizationEngine.solve_allocation(demand_kg=demand_excess, warehouses=whs)

        assert result["solver_status"] == "OPTIMAL"
        assert result["fulfillment_status"] == "PARTIAL_FULFILLMENT"
        # Total alokasi dibatasi oleh min(safe, dispatch) = min(10.000, 6.000) + min(4.000, 4.000) = 6.000 + 4.000 = 10.000 kg
        assert result["allocations"]["WH-A"] <= 6000
        assert result["allocations"]["WH-B"] <= 4000
        assert result["unmet_demand_kg"] == 20000 - (result["allocations"]["WH-A"] + result["allocations"]["WH-B"])

    def test_plan_v2_recovery_baseline(self):
        """
        E6: Adaptive Replanning Baseline (Defisit 290 kg).
        Gudang A kuota harian habis terpakai (6.000/6.000 kg -> sisa 0 kg).
        Gudang B sisa kuota dan stok aman = 2.050 kg.
        Solver wajib mengalokasikan Gudang A = 0 kg, Gudang B = 290 kg, biaya Rp 435.000.
        """
        warehouses_post_dispatch = [
            {
                "id": "WH-A",
                "name": "Gudang A (Sentra A)",
                "stock_physical_kg": 29000,
                "local_demand_kg": 20000,
                "safety_stock_kg": 5000,
                "active_stock_reservations_kg": 0,
                "daily_dispatch_capacity_kg": 6000,
                "actual_dispatched_today_kg": 6000,
                "active_undispatched_dispatch_reservations_today_kg": 0,
                "cost_per_kg": 1200,
            },
            {
                "id": "WH-B",
                "name": "Gudang B (Sentra B)",
                "stock_physical_kg": 20050,
                "local_demand_kg": 15000,
                "safety_stock_kg": 3000,
                "active_stock_reservations_kg": 0,
                "daily_dispatch_capacity_kg": 4000,
                "actual_dispatched_today_kg": 1950,
                "active_undispatched_dispatch_reservations_today_kg": 0,
                "cost_per_kg": 1500,
            },
        ]

        result = solve_recovery_plan_v2(warehouses=warehouses_post_dispatch, deficit_kg=290)

        assert result["solver_status"] == "OPTIMAL"
        assert result["fulfillment_status"] == "FULL_FULFILLMENT"
        assert result["allocated_kg"] == 290
        assert result["unmet_demand_kg"] == 0
        assert result["allocations"]["WH-A"] == 0
        assert result["allocations"]["WH-B"] == 290
        assert result["planned_cost"] == 435_000

    def test_tc_dispatch_reserved_01_boundary_case(self):
        """
        TC-DISPATCH-RESERVED-01:
        Gudang B sebelum reservasi memiliki stok aman dan sisa kuota 2.050 kg.
        Terdapat reservasi aktif pengiriman lain sebesar 1.800 kg pada hari yang sama.
        safe_available dan dispatch_available keduanya menjadi 250 kg.
        Untuk defisit 290 kg, solver wajib:
          - Mengalokasikan WH-A = 0 kg
          - Mengalokasikan WH-B = 250 kg (bukan 290 kg)
          - Melaporkan unmet_demand = 40 kg
          - solver_status = OPTIMAL (optimalitas matematis terbukti)
          - fulfillment_status = PARTIAL_FULFILLMENT (status bisnis pemenuhan parsial)
          - planned_cost = 250 * 1500 = Rp 375.000
        """
        warehouses_with_active_reservation = [
            {
                "id": "WH-A",
                "name": "Gudang A",
                "stock_physical_kg": 29000,
                "local_demand_kg": 20000,
                "safety_stock_kg": 5000,
                "active_stock_reservations_kg": 0,
                "daily_dispatch_capacity_kg": 6000,
                "actual_dispatched_today_kg": 6000,
                "active_undispatched_dispatch_reservations_today_kg": 0,
                "cost_per_kg": 1200,
            },
            {
                "id": "WH-B",
                "name": "Gudang B",
                "stock_physical_kg": 20050,
                "local_demand_kg": 15000,
                "safety_stock_kg": 3000,
                "active_stock_reservations_kg": 1800,  # Mengikat stok aman bebas (2050 - 1800 = 250)
                "daily_dispatch_capacity_kg": 4000,
                "actual_dispatched_today_kg": 1950,
                "active_undispatched_dispatch_reservations_today_kg": 1800,  # Mengikat kuota harian (2050 - 1800 = 250)
                "cost_per_kg": 1500,
            },
        ]

        bounds_b = OptimizationEngine.calculate_bounds(warehouses_with_active_reservation[1])
        assert bounds_b["safe_available"] == 250
        assert bounds_b["dispatch_available"] == 250
        assert bounds_b["upper_bound"] == 250

        result = solve_recovery_plan_v2(warehouses=warehouses_with_active_reservation, deficit_kg=290)

        assert result["solver_status"] == "OPTIMAL"
        assert result["fulfillment_status"] == "PARTIAL_FULFILLMENT"
        assert result["allocated_kg"] == 250
        assert result["unmet_demand_kg"] == 40
        assert result["allocations"]["WH-A"] == 0
        assert result["allocations"]["WH-B"] == 250
        assert result["planned_cost"] == 375_000

    def test_tc_cost_est_01_cost_differentiation(self):
        """
        TC-COST-EST-01:
        Verifikasi bahwa:
          - Planned cost Plan v1 (8.000 kg) = Rp 10.200.000
          - Estimated dispatched cost berbasis manifest aktual (7.950 kg) = Rp 10.125.000
          - Selisih tepat Rp 75.000 (50 kg * Rp 1.500)
        """
        whs = get_demo_warehouses_initial()
        plan_v1 = solve_plan_v1(warehouses=whs, demand_kg=8000)
        assert plan_v1["planned_cost"] == 10_200_000

        # Manifest aktual: Gudang A = 6.000 kg, Gudang B = 1.950 kg (50 kg unshipped)
        actual_manifest = {
            "WH-A": {"dispatched_kg": 6000, "cost_per_kg": 1200},
            "WH-B": {"dispatched_kg": 1950, "cost_per_kg": 1500},
        }

        estimated_dispatched_cost = sum(
            item["dispatched_kg"] * item["cost_per_kg"] for item in actual_manifest.values()
        )
        assert estimated_dispatched_cost == 10_125_000

        cost_difference = plan_v1["planned_cost"] - estimated_dispatched_cost
        assert cost_difference == 75_000
