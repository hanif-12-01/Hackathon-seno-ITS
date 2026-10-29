from typing import Any, Dict, List
from app.models.db_models import DistributionCase, InventorySnapshot
from datetime import datetime, timedelta


class DataReadinessGate:
    @staticmethod
    def validate_case(case: DistributionCase, snapshots: List[InventorySnapshot]) -> Dict[str, Any]:
        """
        Pemeriksaan Data Readiness Gate pra-optimasi sesuai spesifikasi.
        Memvalidasi 5 aturan kritis:
        1. MANDATORY_FIELDS
        2. NON_NEGATIVE_BOUNDS
        3. DEMAND_VERIFIED
        4. DATA_FRESHNESS
        5. CONSTRAINT_PARAMETERS_AVAILABLE
        """
        details = []

        # 1. MANDATORY_FIELDS
        has_mandatory = bool(
            case.commodity_name
            and case.destination_region
            and case.verified_demand >= 0
            and len(snapshots) > 0
        )
        details.append({
            "rule": "MANDATORY_FIELDS",
            "status": "PASSED" if has_mandatory else "FAILED"
        })

        # 2. NON_NEGATIVE_BOUNDS
        bounds_ok = True
        for s in snapshots:
            if (
                s.physical_stock < 0
                or s.local_demand < 0
                or s.safety_stock < 0
                or s.daily_dispatch_capacity < 0
                or s.actual_dispatched_today < 0
            ):
                bounds_ok = False
                break
        details.append({
            "rule": "NON_NEGATIVE_BOUNDS",
            "status": "PASSED" if bounds_ok else "FAILED"
        })

        # 3. DEMAND_VERIFIED
        demand_verified = bool(case.is_demand_verified)
        details.append({
            "rule": "DEMAND_VERIFIED",
            "status": "PASSED" if demand_verified else "FAILED"
        })

        # 4. DATA_FRESHNESS
        freshness_ok = all(
            s.is_verified and timedelta(0) <= datetime.utcnow() - s.snapshot_time <= timedelta(hours=24)
            for s in snapshots
        ) if snapshots else False
        details.append({
            "rule": "DATA_FRESHNESS",
            "status": "PASSED" if freshness_ok else "FAILED"
        })

        # 5. CONSTRAINT_PARAMETERS_AVAILABLE
        constraints_ok = bool(snapshots and all(s.cost_per_kg > 0 for s in snapshots))
        details.append({
            "rule": "CONSTRAINT_PARAMETERS_AVAILABLE",
            "status": "PASSED" if constraints_ok else "FAILED"
        })

        safe_capacities = {s.warehouse_id: float(s.safe_capacity) for s in snapshots}
        rem_dispatch = {s.warehouse_id: float(s.remaining_dispatch_capacity) for s in snapshots}

        all_passed = all(d["status"] == "PASSED" for d in details)

        return {
            "case_id": case.id,
            "is_ready": all_passed,
            "is_demand_verified": demand_verified,
            "validation_details": details,
            "safe_capacities": safe_capacities,
            "remaining_dispatch_capacities": rem_dispatch,
        }
