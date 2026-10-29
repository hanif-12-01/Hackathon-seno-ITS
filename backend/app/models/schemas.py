import datetime
from typing import Dict, List, Optional, Literal, Annotated
from pydantic import BaseModel, Field, AfterValidator, model_validator


def whole_kg(value: float) -> float:
    if not value.is_integer():
        raise ValueError("Kuantitas wajib integer kilogram.")
    return value


Kilograms = Annotated[float, Field(ge=0, le=1_000_000_000, allow_inf_nan=False), AfterValidator(whole_kg)]


class WarehouseInput(BaseModel):
    warehouse_id: str
    physical_stock: Kilograms
    local_demand: Kilograms
    safety_stock: Kilograms
    daily_dispatch_capacity: Kilograms
    actual_dispatched_today: Kilograms = 0
    active_undispatched_reservations: Kilograms = 0
    cost_per_kg: float = Field(..., gt=0, le=1_000_000_000, allow_inf_nan=False)
    snapshot_time: Optional[datetime.datetime] = None

    @model_validator(mode="after")
    def tariff_precision(self):
        from decimal import Decimal
        cents = Decimal(str(self.cost_per_kg)) * 100
        if cents != cents.to_integral_value():
            raise ValueError("Tarif maksimal dua angka desimal.")
        return self


class CaseCreateRequest(BaseModel):
    commodity_name: str = "Cabai Rawit Merah"
    destination_region: str
    verified_demand: Kilograms
    is_demand_verified: bool = False
    deadline_hours: int = Field(default=48, gt=0)
    created_by: str
    warehouses: List[WarehouseInput] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_warehouses(self):
        ids = [w.warehouse_id for w in self.warehouses]
        if len(ids) != len(set(ids)):
            raise ValueError("Gudang duplikat.")
        return self


class CaseCreateResponse(BaseModel):
    status: str = "success"
    case_id: str
    message: str


class ValidationRuleResult(BaseModel):
    rule: str
    status: str  # PASSED, FAILED


class ValidationResponse(BaseModel):
    case_id: str
    is_ready: bool
    is_demand_verified: bool
    validation_details: List[ValidationRuleResult]
    safe_capacities: Dict[str, float]
    remaining_dispatch_capacities: Dict[str, float]


class AllocationLineResponse(BaseModel):
    warehouse_id: str
    allocated_kg: float
    cost_per_kg: float
    subtotal: float
    remaining_safe_capacity: float
    remaining_dispatch_capacity: float
    allocation_constraint_note: Optional[str] = None


class OptimizeResponse(BaseModel):
    plan_id: str
    version: int
    solver_status: str
    fulfillment_status: str
    claim_category: str
    total_demand_kg: float
    allocated_kg: float
    unmet_demand_kg: float
    planned_cost: float
    cost_note: str = (
        "Planned cost berdasarkan alokasi rencana yang disetujui; bukan biaya riil tagihan faktur."
    )
    solve_time_ms: float
    lines: List[AllocationLineResponse]


class ApproveRequest(BaseModel):
    actor_id: str
    decision: Literal["APPROVE", "REJECT"] = "APPROVE"
    notes: Optional[str] = None


class ReservationItem(BaseModel):
    warehouse_id: str
    reserved_kg: float
    status: str = "ACTIVE"


class ApproveResponse(BaseModel):
    plan_id: str
    approval_status: str
    reservations_created: List[ReservationItem]
    shipments_initialized: List[str]


class AuditLogResponse(BaseModel):
    id: int
    case_id: Optional[str]
    action_type: str
    actor_id: str
    details: str
    timestamp: datetime.datetime


class CaseDetailResponse(BaseModel):
    id: str
    commodity_name: str
    destination_region: str
    verified_demand: float
    is_demand_verified: bool
    deadline_hours: int
    status: str
    created_by: str
    created_at: datetime.datetime
    snapshots: List[Dict]
    plans: List[Dict]
    reservations: List[Dict]


class CancellationRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=100)
    transaction_ref: str = Field(min_length=1, max_length=100)
    unshipped_cancelled_quantity_kg: Kilograms
    cancellation_reason: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def explicit_reason(self):
        if self.unshipped_cancelled_quantity_kg and not (self.cancellation_reason or "").strip():
            raise ValueError("Alasan cancellation wajib.")
        if type(self) is CancellationRequest and not self.unshipped_cancelled_quantity_kg:
            raise ValueError("Quantity cancellation harus positif.")
        return self


class DispatchRequest(CancellationRequest):
    planned_quantity_kg: Kilograms
    actual_dispatched_quantity_kg: Kilograms = Field(gt=0)
    unshipped_cancelled_quantity_kg: Kilograms = 0


class WarehouseStateResponse(BaseModel):
    warehouse_id: str
    physical_stock: float
    safe_capacity: float
    remaining_dispatch_capacity: float
    actual_dispatched_today: float
    active_stock_reservations: float
    active_dispatch_reservations_today: float
    operational_date: str


class ShipmentResponse(BaseModel):
    shipment_id: str
    case_id: str
    plan_id: str
    warehouse_id: str
    allocation_line_id: str
    planned_quantity_kg: int
    actual_dispatched_quantity_kg: int
    unshipped_cancelled_quantity_kg: int
    remaining_reserved_stock_kg: float
    remaining_reserved_dispatch_kg: float
    in_transit_quantity_kg: int
    estimated_line_dispatched_cost: str
    dispatch_timestamp: Optional[str]
    operational_date: str
    transaction_ref: Optional[str]
    manifest_status: str


class DispatchResponse(ShipmentResponse):
    physical_stock_deducted_kg: int
    reservation_released_kg: int
    safe_capacity_restored_kg: int
    warehouse_state: WarehouseStateResponse


class ReceivingIdentity(BaseModel):
    transaction_ref: str = Field(min_length=1, max_length=100, pattern=r"\S")


class ReceiptRequest(ReceivingIdentity):
    inspector_id: str = Field(min_length=1, max_length=100, pattern=r"\S")
    plan_id: str = Field(min_length=1, max_length=50, pattern=r"\S")
    shipment_ids: List[str] = Field(min_length=1)
    total_dispatched_kg: Kilograms
    arrived_weight_kg: Kilograms
    rejected_weight_kg: Kilograms
    inspection_notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def receiving_bounds(self):
        if len(set(self.shipment_ids)) != len(self.shipment_ids):
            raise ValueError("Shipment duplikat.")
        if self.rejected_weight_kg > self.arrived_weight_kg:
            raise ValueError("Rejected melebihi arrived.")
        self.shipment_ids = sorted(self.shipment_ids)
        return self


class ReconcileRequest(ReceivingIdentity):
    actor_id: str = Field(min_length=1, max_length=100, pattern=r"\S")
    receipt_id: str = Field(min_length=1, max_length=50, pattern=r"\S")


class CheckpointRequest(ReceivingIdentity):
    model_config = {"extra": "forbid"}
    actor_id: str = Field(min_length=1, max_length=100, pattern=r"\S")
    event_type: Literal["IN_TRANSIT", "AT_CHECKPOINT", "DELAY_REPORTED"]
    note: Optional[str] = Field(default=None, max_length=2000)


class ReplanRequest(ReceivingIdentity):
    model_config = {"extra": "forbid"}
    actor_id: str = Field(min_length=1, max_length=100, pattern=r"\S")
    source_reconciliation_id: str = Field(min_length=1, max_length=50, pattern=r"\S")


class DataFreshnessResponse(BaseModel):
    latest_observation_date: str
    reference_date_wib: str
    age_days: int
    max_fresh_age_days: int
    freshness_status: Literal["FRESH", "STALE"]
    is_fresh: bool
    freshness_note: str


class ForecastStatusResponse(BaseModel):
    available: bool
    status: str
    reason: str
    commodity: str
    geographic_scope: str
    market_region_id: Optional[str] = None
    data_source: Optional[str] = None
    source_url: Optional[str] = None
    currency: Optional[str] = None
    unit: Optional[str] = None
    model_version: Optional[str] = None
    trained_at_utc: Optional[str] = None
    horizons_days: Optional[List[int]] = None
    latest_observation_date: Optional[str] = None
    data_freshness: Optional[DataFreshnessResponse] = None
    provenance_verification_status: Optional[str] = None
    validation_selected_models_by_horizon: Optional[Dict[str, str]] = None
    selected_models_by_horizon: Optional[Dict[str, str]] = None


class HistoricalPointResponse(BaseModel):
    observation_date: str
    price: float
    is_observed: bool
    source_record_present: bool = True
    confirmed_market_observation: Optional[bool] = None
    is_national_holiday_weekday: bool = False
    observation_provenance: str = "UNKNOWN_OBSERVATION_PROVENANCE"
    rmean_7: Optional[float] = None
    rmean_14: Optional[float] = None


class HistoricalSeriesResponse(BaseModel):
    commodity: str
    geographic_scope: str
    market_region_id: str
    data_source: str
    source_url: str
    currency: str
    unit: str
    latest_observation_date: str
    data_freshness: DataFreshnessResponse
    provenance_verification_status: str
    is_observed_field_semantics: str
    total_observed_records: int
    returned_records_count: int
    observed_only: bool
    series: List[HistoricalPointResponse]


class RiskSignalResponse(BaseModel):
    risk_level: Literal["STABLE_NORMAL", "ELEVATED_VOLATILITY_RISK", "HIGH_PRICE_SPIKE_RISK"]
    signal_method: str
    is_calibrated_probability: bool
    triggers_automatic_dispatch: bool
    expected_pct_change: float
    price_vs_rmean14_pct: float
    rolling_14d_cv_pct: float
    thresholds: Dict[str, float]
    explanation: str


class HorizonForecastItemResponse(BaseModel):
    horizon_days: Literal[1, 3, 7]
    forecast_origin_date: str
    origin_observation_provenance: str
    target_date: str
    target_day_of_week: str
    target_is_weekday_market: bool
    actual_observation_available: bool
    target_source_record_present: bool
    target_confirmed_market_observation: Optional[bool] = None
    target_schedule_matches_evaluation_scope: bool
    target_schedule_expectation: Literal[
        "WEEKDAY_PUBLICATION_SCHEDULE",
        "NON_SURVEY_WEEKEND_SCHEDULE",
        "HOLIDAY_WEEKDAY_SCHEDULE",
    ]
    target_observation_status: str
    target_is_national_holiday_weekday: bool
    target_validation_disclosure: str
    origin_observed_price: float
    predicted_price: float
    validation_selected_model: str
    selected_model: str
    xgboost_predicted_price: float
    naive_predicted_price: float
    predicted_change_rp: float
    predicted_change_pct: float
    currency: str
    unit: str
    evaluation_reference: Dict[str, object]
    risk_signal: RiskSignalResponse


class ForecastPredictionsResponse(BaseModel):
    commodity: str
    geographic_scope: str
    market_region_id: str
    price_definition: str
    data_source: str
    source_url: str
    currency: str
    unit: str
    model_version: str
    provenance_verification_status: str
    forecast_origin_date: str
    origin_observed_price: float
    origin_observation_provenance: str
    latest_observation_date: str
    data_freshness: DataFreshnessResponse
    overall_risk_signal: RiskSignalResponse
    operational_boundary_note: str
    forecasts: List[HorizonForecastItemResponse]
