"""
PanganNadi Adaptive — Core Allocation & Adaptive Replanning Engine
Powered by Google OR-Tools CP-SAT Solver.

Formulation:
  Minimize Z = M * u + sum(c_i * x_i)
  Subject to:
    sum(x_i) + u == D
    0 <= x_i <= min(safe_available_i, dispatch_available_i)
    x_i, u >= 0, integer
"""

import time
import math
from decimal import Decimal
from typing import Any, Dict, List, Optional
from ortools.sat.python import cp_model


class OptimizationEngine:
    PENALTY_WEIGHT = 1_000_000  # M penalty weight for unmet demand

    @staticmethod
    def calculate_bounds(warehouse: Dict[str, Any]) -> Dict[str, int]:
        """
        Hitung safe_available dan dispatch_available secara deterministik
        dengan memperhitungkan reservasi aktif tanpa double counting.
        """
        stock_physical = int(warehouse.get("stock_physical_kg", 0))
        local_demand = int(warehouse.get("local_demand_kg", 0))
        safety_stock = int(warehouse.get("safety_stock_kg", 0))
        stock_reserved = int(warehouse.get("active_stock_reservations_kg", 0))

        # safe_available: stok fisik dikurangi kebutuhan lokal, safety stock, dan reservasi aktif
        if "safe_available" in warehouse:
            safe_available = max(0, int(warehouse["safe_available"]))
        elif "safe_capacity" in warehouse:
            safe_available = max(0, int(warehouse["safe_capacity"]) - stock_reserved)
        else:
            safe_available = max(0, stock_physical - local_demand - safety_stock - stock_reserved)

        # dispatch_available: kuota harian dikurangi pengiriman berangkat dan reservasi dispatch aktif
        daily_capacity = int(warehouse.get("daily_dispatch_capacity_kg", safe_available))
        if "daily_dispatch_capacity" in warehouse and "daily_dispatch_capacity_kg" not in warehouse:
            daily_capacity = int(warehouse["daily_dispatch_capacity"])

        dispatched_today = int(warehouse.get("actual_dispatched_today_kg", 0))
        if "actual_dispatched_today" in warehouse and "actual_dispatched_today_kg" not in warehouse:
            dispatched_today = int(warehouse["actual_dispatched_today"])

        dispatch_reserved = int(warehouse.get("active_undispatched_dispatch_reservations_today_kg", 0))
        if "active_undispatched_dispatch_reservations_today" in warehouse and "active_undispatched_dispatch_reservations_today_kg" not in warehouse:
            dispatch_reserved = int(warehouse["active_undispatched_dispatch_reservations_today"])

        if "dispatch_available" in warehouse:
            dispatch_available = max(0, int(warehouse["dispatch_available"]))
        else:
            dispatch_available = max(0, daily_capacity - dispatched_today - dispatch_reserved)

        upper_bound = min(safe_available, dispatch_available)

        return {
            "safe_available": safe_available,
            "dispatch_available": dispatch_available,
            "upper_bound": upper_bound,
            "cost_per_kg": warehouse.get("cost_per_kg", warehouse.get("base_cost_per_kg", 0)),
        }

    @classmethod
    def solve_allocation(
        cls,
        demand_kg: int,
        warehouses: List[Dict[str, Any]],
        max_time_seconds: float = 2.0,
    ) -> Dict[str, Any]:
        """
        Menjalankan solver CP-SAT untuk memecahkan alokasi pengiriman optimal.
        Memisahkan solver_status (matematis) dari fulfillment_status (bisnis).
        """
        start_time = time.perf_counter()
        if not math.isfinite(demand_kg) or demand_kg < 0 or int(demand_kg) != demand_kg or demand_kg > 1_000_000_000:
            raise ValueError("Demand wajib integer kilogram antara 0 dan 1.000.000.000.")
        ids = [str(w["id"]) for w in warehouses]
        if len(ids) != len(set(ids)):
            raise ValueError("Gudang duplikat.")
        for wh in warehouses:
            for key, value in wh.items():
                if key in ("id", "name", "cost_per_kg", "base_cost_per_kg"):
                    continue
                if isinstance(value, (int, float)) and (not math.isfinite(value) or value < 0 or int(value) != value):
                    raise ValueError("Kuantitas wajib integer kilogram non-negatif.")
            cost = wh.get("cost_per_kg", wh.get("base_cost_per_kg", 0))
            if not math.isfinite(cost) or not 0 < cost <= 1_000_000_000 or Decimal(str(cost)) * 100 != (Decimal(str(cost)) * 100).to_integral_value():
                raise ValueError("Tarif positif maksimal dua angka desimal dan <= 1.000.000.000.")
        model = cp_model.CpModel()

        bounds_map = {}
        x_vars = {}

        for wh in warehouses:
            wh_id = str(wh["id"])
            bounds = cls.calculate_bounds(wh)
            bounds_map[wh_id] = bounds
            x_vars[wh_id] = model.NewIntVar(0, min(int(demand_kg), bounds["upper_bound"]), f"x_{wh_id}")

        # Slack variable untuk unmet demand
        u_var = model.NewIntVar(0, int(demand_kg), "unmet_demand")

        # Kendala 1: Keseimbangan demand
        model.Add(sum(x_vars[wh_id] for wh_id in bounds_map) + u_var == int(demand_kg))

        # Fungsi Objektif: Minimalkan M * u + sum(c_i * x_i)
        # For this single-destination linear model, maximum fulfillment is exactly
        # min(demand, sum(bounds)); fixing it enforces lexicographic priority without big-M.
        model.Add(u_var == max(0, int(demand_kg) - sum(b["upper_bound"] for b in bounds_map.values())))
        cost_expressions = [int(Decimal(str(bounds_map[key]["cost_per_kg"])) * 100) * x_vars[key] for key in bounds_map]
        if sum(int(Decimal(str(b["cost_per_kg"])) * 100) * min(int(demand_kg), b["upper_bound"]) for b in bounds_map.values()) > 2**62:
            raise ValueError("Domain biaya melebihi batas integer solver.")
        model.Minimize(sum(cost_expressions))

        # Eksekusi Solver
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max_time_seconds
        status = solver.Solve(model)
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

        status_mapping = {
            cp_model.OPTIMAL: "OPTIMAL",
            cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE",
            cp_model.MODEL_INVALID: "MODEL_INVALID",
        }
        solver_status = status_mapping.get(status, "UNKNOWN")

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            allocations = {
                wh_id: int(solver.Value(x_vars[wh_id])) for wh_id in bounds_map
            }
            unmet = int(solver.Value(u_var))
            allocated_total = int(demand_kg) - unmet
            planned_cost = sum(
                allocations[wh_id] * bounds_map[wh_id]["cost_per_kg"]
                for wh_id in bounds_map
            )

            # Penentuan fulfillment_status (status bisnis)
            if unmet == 0:
                fulfillment_status = "FULL_FULFILLMENT"
            elif allocated_total > 0:
                fulfillment_status = "PARTIAL_FULFILLMENT"
            else:
                fulfillment_status = "NO_ALLOCATION"

            lines = []
            for wh in warehouses:
                wh_id = str(wh["id"])
                alloc_qty = allocations[wh_id]
                cost_kg = bounds_map[wh_id]["cost_per_kg"]
                lines.append(
                    {
                        "warehouse_id": wh_id,
                        "warehouse_name": wh.get("name", wh_id),
                        "allocated_kg": alloc_qty,
                        "cost_per_kg": cost_kg,
                        "subtotal_cost": alloc_qty * cost_kg,
                        "safe_available": bounds_map[wh_id]["safe_available"],
                        "dispatch_available": bounds_map[wh_id]["dispatch_available"],
                        "remaining_safe_after_plan": bounds_map[wh_id]["safe_available"] - alloc_qty,
                        "remaining_dispatch_after_plan": bounds_map[wh_id]["dispatch_available"] - alloc_qty,
                    }
                )

            return {
                "solver_status": solver_status,
                "fulfillment_status": fulfillment_status,
                "claim_category": "SOLVER_VERIFIED_OPTIMAL" if status == cp_model.OPTIMAL else "SOLVER_FEASIBLE",
                "total_demand_kg": int(demand_kg),
                "allocated_kg": allocated_total,
                "unmet_demand_kg": unmet,
                "planned_cost": planned_cost,
                "allocations": allocations,
                "lines": lines,
                "solve_time_ms": elapsed_ms,
            }
        else:
            return {
                "solver_status": solver_status,
                "fulfillment_status": "NO_ALLOCATION",
                "claim_category": "NO_OPTIMALITY_CLAIM",
                "total_demand_kg": int(demand_kg),
                "allocated_kg": 0,
                "unmet_demand_kg": int(demand_kg),
                "planned_cost": 0,
                "allocations": {wh_id: 0 for wh_id in bounds_map},
                "lines": [],
                "solve_time_ms": elapsed_ms,
            }


def solve_plan_v1(warehouses: List[Dict[str, Any]], demand_kg: int = 8000) -> Dict[str, Any]:
    """Helper fungsi untuk Plan v1 alokasi awal."""
    return OptimizationEngine.solve_allocation(demand_kg=demand_kg, warehouses=warehouses)


def solve_recovery_plan_v2(warehouses: List[Dict[str, Any]], deficit_kg: int = 290) -> Dict[str, Any]:
    """Helper fungsi untuk Recovery Plan v2 replanning adaptif."""
    return OptimizationEngine.solve_allocation(demand_kg=deficit_kg, warehouses=warehouses)
