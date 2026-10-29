import datetime
import hashlib
import itertools
import json
import platform
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import sklearn
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, root_mean_squared_error
from xgboost import XGBRegressor

from app.services.price_data_pipeline import (
    AUDIT_JSON_PATH,
    AUDIT_M7B1_JSON_PATH,
    FEATURE_COLUMNS,
    HORIZONS,
    NATIONAL_HOLIDAY_WEEKDAYS_2024_2026,
    PROCESSED_CSV_PATH,
    PROJECT_ROOT,
    PROVENANCE_VERIFICATION_STATUS,
    RAW_JSON_PATH,
    DataValidationError,
    build_calendar_and_features,
    enrich_calendar_provenance,
    run_data_pipeline,
    sha256_file,
    validate_raw_dataframe,
)

ARTIFACTS_DIR = PROJECT_ROOT / "backend" / "artifacts" / "forecasting"
METADATA_PATH = ARTIFACTS_DIR / "model_metadata_m7b1.json"
METADATA_M7B1_PATH = METADATA_PATH
EVAL_PREDICTIONS_JSON_PATH = ARTIFACTS_DIR / "evaluation_predictions.json"
EVAL_PREDICTIONS_CSV_PATH = ARTIFACTS_DIR / "evaluation_predictions.csv"

MODEL_VERSION = "m7b-20261028-v1"
TRAIN_CUTOFF = "2026-04-30"
VAL_START = "2026-05-01"
VAL_CUTOFF = "2026-07-31"
TEST_START = "2026-08-01"
TEST_CUTOFF = "2026-10-28"
FRESHNESS_MAX_AGE_DAYS = 7
RANDOM_SEED = 42

SERVING_POLICY_OVERRIDES: Dict[int, Dict[str, Any]] = {
    7: {
        "override_id": "OVERRIDE-ADR021-H7-NAIVE-SERVING-V1",
        "serving_model": "naive_persistence_v1",
        "selection_governance_status": "POST_TEST_EXPLORATORY_POLICY_REVISION",
        "is_independent_holdout_for_revised_policy": False,
        "prospective_validation_protocol": (
            "EXPANDING_WINDOW_ROLLING_ORIGIN_BACKTEST_AND_PROSPECTIVE_HOLDOUT_POST_2026_10_28"
        ),
        "override_reason": (
            "Validation memilih xgboost_residual_v1 (MAE 6200.58 vs 6363.93 Rp/kg), namun evaluasi "
            "periode Test 2026-08-01..2026-10-28 menunjukkan Naive Persistence menghasilkan MAE lebih rendah "
            "(4241.38 vs 4355.01 Rp/kg). Penetapan naive_persistence_v1 sebagai serving model H+7 dicatat "
            "sebagai explicit versioned exploratory policy override (ADR-021), bukan pemilihan otomatis "
            "berbasis Test set. Karena revisi kebijakan serving dilakukan setelah melihat Test set, periode "
            "Test 2026-08-01..2026-10-28 tidak diklaim sebagai independent holdout untuk kebijakan serving H+7."
        ),
    }
}


def select_model_for_horizon(
    horizon: int,
    val_naive_mae: float,
    val_xgb_mae: float,
    serving_overrides: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Determines validation_selected_model strictly from Validation MAE alone (never reading Test MAE).
    Serving model equals validation_selected_model unless an explicit versioned governance override
    is registered for that horizon.
    """
    if any(not np.isfinite(float(value)) or float(value) < 0 for value in (val_naive_mae, val_xgb_mae)):
        raise ValueError("Validation MAE must be finite and nonnegative.")
    val_xgb_wins = float(val_xgb_mae) <= float(val_naive_mae)
    validation_selected_model = "xgboost_residual_v1" if val_xgb_wins else "naive_persistence_v1"

    overrides = SERVING_POLICY_OVERRIDES if serving_overrides is None else serving_overrides
    override_cfg = overrides.get(int(horizon))

    if override_cfg is not None:
        serving_model = str(override_cfg["serving_model"])
        governance_status = str(override_cfg["selection_governance_status"])
        is_independent_holdout = bool(override_cfg["is_independent_holdout_for_revised_policy"])
        override_id = str(override_cfg["override_id"])
        prospective_protocol = str(override_cfg["prospective_validation_protocol"])
        override_reason = str(override_cfg["override_reason"])
    else:
        serving_model = validation_selected_model
        governance_status = "VALIDATION_ONLY_SELECTION"
        is_independent_holdout = True
        override_id = None
        prospective_protocol = "STANDARD_CHRONOLOGICAL_HOLDOUT"
        override_reason = None

    return {
        "validation_selected_model": validation_selected_model,
        "selected_model": serving_model,
        "serving_model": serving_model,
        "selection_governance_status": governance_status,
        "is_independent_holdout_for_revised_policy": is_independent_holdout,
        "serving_override_id": override_id,
        "prospective_validation_protocol": prospective_protocol,
        "serving_override_reason": override_reason,
    }


def classify_target_date_semantics(
    target_dt: datetime.date,
    cal_df: pd.DataFrame,
    horizon: int,
) -> Dict[str, Any]:
    target_iso = target_dt.isoformat()
    target_is_weekday = target_dt.weekday() < 5
    target_is_holiday_weekday = target_is_weekday and (target_iso in NATIONAL_HOLIDAY_WEEKDAYS_2024_2026)
    holiday_name = NATIONAL_HOLIDAY_WEEKDAYS_2024_2026.get(target_iso)

    matches = cal_df[cal_df["observation_date"] == target_iso]
    actual_obs_available = bool((not matches.empty) and bool(matches.iloc[-1].get("source_record_present", matches.iloc[-1]["is_observed"])))

    if not target_is_weekday:
        schedule_expectation = "NON_SURVEY_WEEKEND_SCHEDULE"
    elif target_is_holiday_weekday:
        schedule_expectation = "HOLIDAY_WEEKDAY_SCHEDULE"
    else:
        schedule_expectation = "WEEKDAY_PUBLICATION_SCHEDULE"

    if actual_obs_available:
        obs_status = "HISTORICAL_SOURCE_RECORD_PRESENT_SURVEY_UNKNOWN"
    elif not target_is_weekday:
        obs_status = "NON_SURVEY_WEEKEND_CALENDAR_ESTIMATE"
    elif target_is_holiday_weekday:
        obs_status = "FUTURE_HOLIDAY_WEEKDAY_UNCERTAIN_SURVEY"
    else:
        obs_status = "FUTURE_WEEKDAY_PUBLICATION_TARGET_NO_ACTUAL_YET"

    _, _, evaluated_pairs = build_horizon_splits(cal_df, horizon)
    origin_dt = target_dt - datetime.timedelta(days=horizon)
    schedule_matches = bool((
        (pd.to_datetime(evaluated_pairs["observation_date"]).dt.day_of_week == origin_dt.weekday())
        & (pd.to_datetime(evaluated_pairs[f"target_date_{horizon}"]).dt.day_of_week == target_dt.weekday())
    ).any())
    applies_directly = bool(schedule_matches and actual_obs_available and (
        (evaluated_pairs["observation_date"] == origin_dt.isoformat())
        & (evaluated_pairs[f"target_date_{horizon}"] == target_iso)
    ).any())

    if not target_is_weekday:
        disclosure = (
            f"Target H+{horizon} ({target_iso}, {target_dt.strftime('%A')}) jatuh pada akhir pekan (non-survei PIHPS). "
            f"Prediksi ini merupakan estimasi seri hari kalender tanpa observasi pasar langsung pada tanggal target. "
            f"Metrik evaluasi MAE H+{horizon} dihitung khusus pada pasangan hari kerja bersumber PIHPS (Senin-Jumat) "
            f"dan belum tentu mewakili performa pada tanggal non-survei."
        )
    elif target_is_holiday_weekday:
        disclosure = (
            f"Target H+{horizon} ({target_iso}) jatuh pada hari libur nasional/cuti bersama ({holiday_name}). "
            f"Status pelaksanaan survei lapangan PIHPS pada hari libur tidak dapat dipastikan dari payload publik; "
            f"{'record sumber historis tersedia namun status survei tidak diketahui.' if actual_obs_available else 'observasi aktual belum tersedia untuk tanggal masa depan.'}"
        )
    elif actual_obs_available:
        disclosure = (
            f"Target H+{horizon} ({target_iso}) merupakan tanggal historis yang memiliki record sumber PIHPS "
            f"(SOURCE_RECORD_PRESENT; status survei lapangan UNKNOWN_OBSERVATION_PROVENANCE)."
        )
    else:
        disclosure = (
            f"Target H+{horizon} ({target_iso}, {target_dt.strftime('%A')}) berada pada jadwal publikasi hari kerja "
            f"Senin-Jumat PIHPS, namun merupakan tanggal masa depan sehingga observasi aktual belum tersedia. "
            f"MAE evaluasi historis dihitung pada pasangan hari kerja dengan record sumber PIHPS."
        )

    return {
        "actual_observation_available": actual_obs_available,
        "actual_observation_available_semantics": "SOURCE_PUBLISHED_RECORD_NOT_CONFIRMED_MARKET_SURVEY",
        "target_source_record_present": actual_obs_available,
        "target_confirmed_market_observation": None,
        "target_schedule_matches_evaluation_scope": schedule_matches,
        "holiday_calendar_coverage": "THREE_SAMPLE_DATES_ONLY_NOT_FULL_OFFICIAL_CALENDAR",
        "target_schedule_expectation": schedule_expectation,
        "target_observation_status": obs_status,
        "target_is_national_holiday_weekday": target_is_holiday_weekday,
        "target_national_holiday_name": holiday_name,
        "evaluation_applies_directly_to_target_date": applies_directly,
        "target_validation_disclosure": disclosure,
    }


class ForecastingUnavailableError(RuntimeError):
    """Raised when forecasting dataset or trained artifacts are missing or invalid."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


def _mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean(np.abs((y_true - y_pred) / np.maximum(np.abs(y_true), 1.0))) * 100.0)


def _smape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    denom = (np.abs(y_true) + np.abs(y_pred)) / 2.0
    return float(np.mean(np.abs(y_true - y_pred) / np.maximum(denom, 1.0)) * 100.0)


def compute_metrics(y_true: pd.Series, y_pred: np.ndarray) -> Dict[str, float]:
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    return {
        "mae": round(float(mean_absolute_error(yt, yp)), 2),
        "rmse": round(float(root_mean_squared_error(yt, yp)), 2),
        "mape_pct": round(_mape(yt, yp), 2),
        "smape_pct": round(_smape(yt, yp), 2),
    }


def evaluate_risk_signal(
    latest_price: float,
    predicted_price: float,
    rmean_14: float,
    rstd_14: float,
) -> Dict[str, Any]:
    """
    Deterministic rule-based risk overlay. Explicitly separated from ML point forecast
    and never claimed as a calibrated probability or automatic dispatch trigger.
    """
    pct_change = ((predicted_price - latest_price) / latest_price) * 100.0 if latest_price > 0 else 0.0
    vs_rmean14_pct = ((predicted_price - rmean_14) / rmean_14) * 100.0 if rmean_14 > 0 else 0.0
    cv_14_pct = (rstd_14 / rmean_14) * 100.0 if rmean_14 > 0 else 0.0

    if pct_change >= 10.0 or vs_rmean14_pct >= 15.0:
        level = "HIGH_PRICE_SPIKE_RISK"
        reason = (
            f"Prediksi kenaikan {pct_change:+.2f}% dari harga terakhir atau {vs_rmean14_pct:+.2f}% "
            f"di atas rata-rata 14 hari (ambang: >= +10% perubahan atau >= +15% di atas rata-rata 14 hari)."
        )
    elif pct_change >= 5.0 or vs_rmean14_pct >= 10.0 or cv_14_pct >= 12.0:
        level = "ELEVATED_VOLATILITY_RISK"
        reason = (
            f"Perubahan {pct_change:+.2f}% terhadap harga terakhir, deviasi {vs_rmean14_pct:+.2f}% terhadap "
            f"rata-rata 14 hari, atau volatilitas CV 14 hari {cv_14_pct:.2f}% >= 12.0%."
        )
    else:
        level = "STABLE_NORMAL"
        reason = (
            f"Perubahan {pct_change:+.2f}% (< +5%), deviasi rata-rata 14 hari {vs_rmean14_pct:+.2f}% (< +10%), "
            f"dan volatilitas CV 14 hari {cv_14_pct:.2f}% (< 12%)."
        )

    return {
        "risk_level": level,
        "signal_method": "DETERMINISTIC_RULE_BASED_OVERLAY",
        "is_calibrated_probability": False,
        "triggers_automatic_dispatch": False,
        "expected_pct_change": round(pct_change, 2),
        "price_vs_rmean14_pct": round(vs_rmean14_pct, 2),
        "rolling_14d_cv_pct": round(cv_14_pct, 2),
        "thresholds": {
            "high_pct_change_min": 10.0,
            "high_vs_rmean14_pct_min": 15.0,
            "elevated_pct_change_min": 5.0,
            "elevated_vs_rmean14_pct_min": 10.0,
            "elevated_cv14_pct_min": 12.0,
        },
        "explanation": reason,
    }


def build_horizon_splits(
    cal_df: pd.DataFrame,
    horizon: int,
    train_cutoff: str = TRAIN_CUTOFF,
    val_start: str = VAL_START,
    val_cutoff: str = VAL_CUTOFF,
    test_start: str = TEST_START,
    test_cutoff: str = TEST_CUTOFF,
    strictly_observed: bool = True,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Splits calendar feature dataframe into strict chronological Train / Validation / Test sets
    for a given calendar-day horizon h.
    Enforces that NO target_date in Train exceeds train_cutoff and NO target_date in Validation
    exceeds val_cutoff, preventing target overlap leakage across split boundaries.
    """
    t_col = f"target_price_{horizon}"
    td_col = f"target_date_{horizon}"
    to_col = f"target_is_observed_{horizon}"

    valid = cal_df.dropna(subset=FEATURE_COLUMNS + [t_col, td_col]).copy()
    if strictly_observed:
        valid = valid[valid["is_observed"] & (valid[to_col] == True)].copy()

    train_df = valid[(valid["observation_date"] <= train_cutoff) & (valid[td_col] <= train_cutoff)].copy()
    val_df = valid[(valid["observation_date"] >= val_start) & (valid[td_col] <= val_cutoff)].copy()
    test_df = valid[(valid["observation_date"] >= test_start) & (valid[td_col] <= test_cutoff)].copy()

    return train_df, val_df, test_df


def train_and_evaluate_pipeline(
    processed_csv_path: Path = PROCESSED_CSV_PATH,
    artifacts_dir: Path = ARTIFACTS_DIR,
    preserve_existing_models: bool = True,
    serving_overrides: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if not processed_csv_path.exists() or not AUDIT_JSON_PATH.exists():
        run_data_pipeline()

    cal_df = enrich_calendar_provenance(pd.read_csv(processed_csv_path))
    cal_df["date"] = pd.to_datetime(cal_df["observation_date"], format="%Y-%m-%d")
    audit_path_to_use = AUDIT_M7B1_JSON_PATH if AUDIT_M7B1_JSON_PATH.exists() else AUDIT_JSON_PATH
    audit_info = json.loads(audit_path_to_use.read_text(encoding="utf-8"))

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    meta_file = artifacts_dir / METADATA_PATH.name
    historical_meta_file = artifacts_dir / "model_metadata.json"
    source_meta_file = meta_file if meta_file.exists() else historical_meta_file
    existing_meta = json.loads(source_meta_file.read_text(encoding="utf-8")) if source_meta_file.exists() else {}
    protected_files = [artifacts_dir / f"xgb_model_h{h}.json" for h in HORIZONS] + [
        artifacts_dir / "evaluation_predictions.json", artifacts_dir / "evaluation_predictions.csv"
    ]
    if not preserve_existing_models and any(path.exists() for path in protected_files):
        raise FileExistsError("Existing model/evaluation artifacts are immutable; use a new artifacts directory.")
    for h in HORIZONS:
        if (artifacts_dir / f"xgb_model_h{h}.json").exists() and str(h) not in existing_meta.get("hyperparameters_by_horizon", {}):
            raise FileExistsError(f"Cannot reuse H+{h} weights without historical hyperparameters.")

    candidate_grid = list(
        itertools.product(
            [10, 20, 30],      # n_estimators
            [1, 2, 3],         # max_depth
            [0.02, 0.04, 0.06],# learning_rate
            [3, 10, 20],       # min_child_weight
        )
    )

    evaluation_by_horizon: Dict[str, Any] = {}
    hyperparameters_by_horizon: Dict[str, Any] = {}
    eval_rows: List[Dict[str, Any]] = []

    for h in HORIZONS:
        t_col = f"target_price_{h}"
        td_col = f"target_date_{h}"
        d_col = f"target_delta_{h}"

        train_obs, val_obs, test_obs = build_horizon_splits(cal_df, h, strictly_observed=True)
        _, _, test_all_cal = build_horizon_splits(cal_df, h, strictly_observed=False)

        # Verify strict temporal non-overlap of targets and subsequent origins
        assert train_obs[td_col].max() < val_obs["observation_date"].min()
        assert val_obs[td_col].max() < test_obs["observation_date"].min()

        val_naive_metrics = compute_metrics(val_obs[t_col], val_obs["price"].to_numpy())

        model_file = artifacts_dir / f"xgb_model_h{h}.json"
        if preserve_existing_models and model_file.exists() and str(h) in existing_meta.get("hyperparameters_by_horizon", {}):
            best_model = XGBRegressor()
            best_model.load_model(str(model_file))
            saved_hp = dict(existing_meta["hyperparameters_by_horizon"][str(h)])
            saved_hp.pop("artifact_filename", None)
            saved_hp.pop("artifact_sha256", None)
            best_params = saved_hp
        else:
            # Select XGBoost hyperparameters strictly on Validation MAE (never touching test_obs)
            best_val_mae: Optional[float] = None
            best_params = {}
            best_model = None

            for n_est, max_depth, lr, mcw in candidate_grid:
                params = {
                    "objective": "reg:absoluteerror",
                    "base_score": 0.0,
                    "n_estimators": n_est,
                    "max_depth": max_depth,
                    "learning_rate": lr,
                    "min_child_weight": mcw,
                    "subsample": 0.8,
                    "colsample_bytree": 0.8,
                    "random_state": RANDOM_SEED,
                    "n_jobs": 1,
                }
                model = XGBRegressor(**params)
                model.fit(train_obs[FEATURE_COLUMNS], train_obs[d_col])
                val_pred = val_obs["price"].to_numpy() + model.predict(val_obs[FEATURE_COLUMNS])
                val_mae = float(mean_absolute_error(val_obs[t_col], val_pred))
                if best_val_mae is None or val_mae < best_val_mae:
                    best_val_mae = val_mae
                    best_params = params
                    best_model = model

            assert best_model is not None
            best_model.save_model(str(model_file))

        val_xgb_pred = val_obs["price"].to_numpy() + best_model.predict(val_obs[FEATURE_COLUMNS])
        selection_decision = select_model_for_horizon(
            horizon=h,
            val_naive_mae=float(mean_absolute_error(val_obs[t_col], val_obs["price"].to_numpy())),
            val_xgb_mae=float(mean_absolute_error(val_obs[t_col], val_xgb_pred)),
            serving_overrides=serving_overrides,
        )
        reused_artifact = preserve_existing_models and str(h) in existing_meta.get("hyperparameters_by_horizon", {})
        if reused_artifact:
            selection_decision["is_independent_holdout_for_revised_policy"] = False
            if selection_decision["serving_override_id"] is None:
                selection_decision["selection_governance_status"] = "HISTORICAL_TEST_COUPLED_SELECTION_REUSED_ARTIFACT"
            selection_decision["prospective_validation_protocol"] = "FROZEN_POST_2026_10_28_MINIMUM_60_ELIGIBLE_ORIGINS_PER_HORIZON_NO_PEEKING_NOT_MEASURED"
        test_naive_metrics = compute_metrics(test_obs[t_col], test_obs["price"].to_numpy())
        test_xgb_pred = test_obs["price"].to_numpy() + best_model.predict(test_obs[FEATURE_COLUMNS])

        val_xgb_metrics = compute_metrics(val_obs[t_col], val_xgb_pred)
        test_xgb_metrics = compute_metrics(test_obs[t_col], test_xgb_pred)

        # Diagnostic on full calendar grid (including weekend forward-fill)
        cal_test_naive_mae = round(
            float(mean_absolute_error(test_all_cal[t_col], test_all_cal["price"].to_numpy())), 2
        )
        cal_test_xgb_pred = test_all_cal["price"].to_numpy() + best_model.predict(test_all_cal[FEATURE_COLUMNS])
        cal_test_xgb_mae = round(float(mean_absolute_error(test_all_cal[t_col], cal_test_xgb_pred)), 2)

        validation_selected_model = selection_decision["validation_selected_model"]
        selected_model = selection_decision["selected_model"]

        abs_improvement = round(test_naive_metrics["mae"] - test_xgb_metrics["mae"], 2)
        rel_improvement_pct = (
            round((abs_improvement / test_naive_metrics["mae"]) * 100.0, 2)
            if test_naive_metrics["mae"] > 0
            else None
        )

        improvement_class = "DESCRIPTIVE_TEST_DIFFERENCE"
        relative_description = "undefined (zero Naive MAE)" if rel_improvement_pct is None else f"{rel_improvement_pct:+.2f}%"
        rationale = (
            f"Validation-only replay memilih {validation_selected_model}. Test MAE XGBoost {test_xgb_metrics['mae']:.2f} "
            f"vs Naive {test_naive_metrics['mae']:.2f} Rp/kg; selisih {abs_improvement:+.2f} Rp/kg / {relative_description}. "
            "Selisih deskriptif, bukan bukti signifikansi. Artefak M7B historis seluruh horizon dipilih dengan melihat Test; "
            "replay tidak membuat periode tersebut holdout independen. "
            f"{selection_decision['serving_override_reason'] or ''}"
        )

        hyperparameters_by_horizon[str(h)] = {
            **best_params,
            "artifact_filename": model_file.name,
            "artifact_sha256": sha256_file(model_file),
        }

        origin_weekdays = sorted(int(x) for x in test_obs["dow"].unique())
        target_weekdays = sorted(int(x) for x in pd.to_datetime(test_obs[td_col]).dt.day_of_week.unique())

        evaluation_by_horizon[str(h)] = {
            "horizon_days": h,
            "horizon_definition": (
                f"Exact calendar day origin_date + {h} days, evaluated on pairs where both origin and target "
                f"have PIHPS weekday source records (is_observed=True at origin and target; weekend forward-fill excluded)."
            ),
            "evaluated_target_scope": "SOURCE_RECORD_WEEKDAY_PAIRS_ONLY",
            "evaluated_origin_weekdays": origin_weekdays,
            "evaluated_target_weekdays": target_weekdays,
            "includes_weekend_targets_in_primary_mae": False,
            "sample_counts": {
                "train_observed_pairs": int(len(train_obs)),
                "validation_observed_pairs": int(len(val_obs)),
                "test_observed_pairs": int(len(test_obs)),
                "test_all_calendar_days": int(len(test_all_cal)),
            },
            "split_date_ranges": {
                "train_origin_range": [str(train_obs["observation_date"].min()), str(train_obs["observation_date"].max())],
                "train_target_max": str(train_obs[td_col].max()),
                "validation_origin_range": [str(val_obs["observation_date"].min()), str(val_obs["observation_date"].max())],
                "validation_target_max": str(val_obs[td_col].max()),
                "test_origin_range": [str(test_obs["observation_date"].min()), str(test_obs["observation_date"].max())],
                "test_target_max": str(test_obs[td_col].max()),
            },
            "validation_metrics": {
                "naive": val_naive_metrics,
                "xgboost": val_xgb_metrics,
            },
            "test_metrics": {
                "naive": test_naive_metrics,
                "xgboost": test_xgb_metrics,
                "naive_mae": test_naive_metrics["mae"],
                "xgboost_mae": test_xgb_metrics["mae"],
                "absolute_improvement_rp": abs_improvement,
                "relative_improvement_pct": rel_improvement_pct,
                "improvement_magnitude_classification": improvement_class,
            },
            "calendar_grid_diagnostic_test_mae": {
                "naive_mae": cal_test_naive_mae,
                "xgboost_mae": cal_test_xgb_mae,
                "diagnostic_note": (
                    "Diagnostik seluruh hari kalender mencakup target akhir pekan yang diisi forward-fill (PIPELINE_FORWARD_FILL), "
                    "sehingga bukan ukuran akurasi survei pasar pada akhir pekan."
                ),
            },
            "validation_selected_model": validation_selected_model,
            "selected_model": selected_model,
            "serving_model": selected_model,
            "selection_governance_status": selection_decision["selection_governance_status"],
            "is_independent_holdout_for_revised_policy": selection_decision["is_independent_holdout_for_revised_policy"],
            "serving_override_id": selection_decision["serving_override_id"],
            "prospective_validation_protocol": selection_decision["prospective_validation_protocol"],
            "selection_rationale": rationale,
        }

        for idx_row, (_, row) in enumerate(test_obs.iterrows()):
            naive_p = round(float(row["price"]), 2)
            xgb_p = round(float(test_xgb_pred[idx_row]), 2)
            sel_p = xgb_p if selected_model == "xgboost_residual_v1" else naive_p
            actual_p = round(float(row[t_col]), 2)
            eval_rows.append(
                {
                    "horizon_days": h,
                    "origin_date": str(row["observation_date"]),
                    "target_date": str(row[td_col]),
                    "origin_price": naive_p,
                    "actual_price": actual_p,
                    "naive_predicted_price": naive_p,
                    "xgboost_predicted_price": xgb_p,
                    "selected_predicted_price": sel_p,
                    "selected_model": selected_model,
                    "naive_abs_error": round(abs(actual_p - naive_p), 2),
                    "xgboost_abs_error": round(abs(actual_p - xgb_p), 2),
                }
            )

    eval_json_file = artifacts_dir / "evaluation_predictions.json"
    eval_csv_file = artifacts_dir / "evaluation_predictions.csv"
    if not eval_csv_file.exists():
        pd.DataFrame(eval_rows).to_csv(eval_csv_file, index=False)
    if not eval_json_file.exists():
        with eval_json_file.open("x", encoding="utf-8") as stream:
            json.dump(eval_rows, stream, ensure_ascii=False, indent=2)

    trained_at = existing_meta.get("trained_at_utc") or datetime.datetime.now(datetime.timezone.utc).isoformat()

    metadata: Dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "metadata_schema_version": "m7b1-20261028-v1",
        "trained_at_utc": trained_at,
        "commodity": audit_info["commodity"],
        "geographic_scope": audit_info["geographic_scope"],
        "market_region_id": audit_info["market_region_id"],
        "price_definition": audit_info["price_definition"],
        "currency": audit_info["currency"],
        "unit": audit_info["unit"],
        "data_source": audit_info["source_name"],
        "source_url": audit_info["source_url"],
        "dataset_retrieval_timestamp": audit_info["retrieval_timestamp"],
        "provenance_verification_status": audit_info.get(
            "provenance_verification_status", PROVENANCE_VERIFICATION_STATUS
        ),
        "is_observed_field_semantics": audit_info.get("is_observed_field_semantics", ""),
        "dataset_date_range": {
            "start_date": audit_info["start_date"],
            "end_date": audit_info["end_date"],
            "observed_market_days": audit_info["observed_record_count"],
            "calendar_days": audit_info["calendar_day_span"],
        },
        "dataset_hashes": audit_info["artifacts"],
        "feature_schema": FEATURE_COLUMNS,
        "horizons_days": list(HORIZONS),
        "split_configuration": {
            "method": "STRICT_CHRONOLOGICAL_NO_TARGET_OVERLAP",
            "train_cutoff_date": TRAIN_CUTOFF,
            "validation_start_date": VAL_START,
            "validation_cutoff_date": VAL_CUTOFF,
            "test_start_date": TEST_START,
            "test_cutoff_date": TEST_CUTOFF,
            "random_seed": RANDOM_SEED,
        },
        "holiday_calendar_coverage": "THREE_SAMPLE_DATES_ONLY_NOT_FULL_OFFICIAL_CALENDAR",
        "actual_observation_available_semantics": "SOURCE_PUBLISHED_RECORD_NOT_CONFIRMED_MARKET_SURVEY",
        "target_confirmed_market_observation": None,
        "model_selection_governance": {
            "historical_m7b_selection_scope": "ALL_HORIZONS_TEST_COUPLED_NOT_INDEPENDENT_HOLDOUT",
            "prospective_protocol": {
                "status": "FROZEN_NOT_MEASURED",
                "origin_dates_strictly_after": "2026-10-28",
                "minimum_eligible_origins_per_horizon": 60,
                "policy": "FIXED_WEIGHTS_FEATURES_HYPERPARAMETERS_AND_SERVING_POLICY_NO_PEEKING",
                "evaluation": "Score only after all targets mature; no interim selection or tuning.",
            },
            "hyperparameter_selection_split": "VALIDATION_ONLY",
            "candidate_model_selection_split": "VALIDATION_ONLY",
            "serving_policy_overrides": {str(k): v for k, v in (serving_overrides if serving_overrides is not None else SERVING_POLICY_OVERRIDES).items()},
            "prospective_validation_requirement": (
                "All reused M7B horizons had historical test-coupled selection. Prospective protocol is frozen, not measured: "
                "minimum 60 eligible source-record origins per horizon strictly after 2026-10-28; fixed weights, features, "
                "hyperparameters and serving policy, no interim peeking or tuning, score after all targets mature. "
                "Any policy change requires a new untouched window."
            ),
        },
        "hyperparameters_by_horizon": hyperparameters_by_horizon,
        "evaluation_by_horizon": evaluation_by_horizon,
        "evaluation_predictions_path": str(eval_json_file),
        "evaluation_predictions_sha256": sha256_file(eval_json_file),
        "library_versions": {
            "python": platform.python_version(),
            "xgboost": xgb.__version__,
            "scikit_learn": sklearn.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
        "known_limitations": [
            "Payload publik PIHPS Bank Indonesia hanya memuat tanggal Senin-Jumat tanpa metadata pelaksanaan survei lapangan per tanggal (status: PARTIALLY VERIFIED — SOURCE RECORD CONFIRMED, MARKET SURVEY STATUS UNKNOWN); hari Sabtu dan Minggu tidak memiliki record sumber.",
            "Prediksi yang jatuh pada tanggal akhir pekan (contoh H+3 dari Rabu 2026-10-28 -> Sabtu 2026-10-31) merupakan estimasi seri hari kalender dan tidak memiliki observasi survei PIHPS langsung pada tanggal target.",
            "Seluruh selisih Test MAE adalah DESCRIPTIVE_TEST_DIFFERENCE tanpa pengujian signifikansi atau ambang meaningful 1%.",
            "Kalender libur hanya mencakup tiga tanggal sampel, bukan kalender resmi lengkap; tanggal tidak tercantum tidak membuktikan hari kerja non-libur.",
            "Seluruh artefak M7B H+1/H+3/H+7 memiliki riwayat pemilihan terkait Test; replay validation-only tidak menghasilkan holdout independen retrospektif.",
            "Pada horizon H+7, Validation memilih xgboost_residual_v1 (Rp 6.200,58 vs Rp 6.363,93/kg) namun Test menunjukkan Naive lebih rendah (Rp 4.241,38 vs Rp 4.355,01/kg). Penggunaan naive_persistence_v1 sebagai serving model H+7 adalah explicit versioned exploratory policy override (ADR-021), sehingga periode Test 2026-08-01..2026-10-28 tidak diklaim sebagai independent holdout untuk kebijakan serving H+7.",
            "Prediksi harga dan sinyal risiko hanya berfungsi sebagai Early Warning bagi Planner, bukan bukti defisit fisik terverifikasi (Verified Demand) dan dilarang memicu pengiriman otomatis.",
        ],
    }

    meta_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


class ForecastingService:
    """
    Inference and evaluation service for FastAPI. Loads trusted local model artifacts
    without retraining on API calls.
    """

    @staticmethod
    def _resolve_paths(
        processed_csv_path: Optional[Path] = None,
        metadata_path: Optional[Path] = None,
        artifacts_dir: Optional[Path] = None,
    ) -> Tuple[Path, Path, Path]:
        private_root = os.environ.get("PANGANNADI_FORECAST_ROOT")
        if private_root:
            root = Path(private_root)
            if not root.is_absolute():
                raise ValueError("PANGANNADI_FORECAST_ROOT must be absolute")
            return (
                processed_csv_path or root / PROCESSED_CSV_PATH.name,
                metadata_path or root / METADATA_PATH.name,
                artifacts_dir or root,
            )
        return (
            processed_csv_path or PROCESSED_CSV_PATH,
            metadata_path or METADATA_PATH,
            artifacts_dir or ARTIFACTS_DIR,
        )

    @classmethod
    def check_freshness(cls, latest_obs_date_str: str, reference_date: Optional[datetime.date] = None) -> Dict[str, Any]:
        ref = reference_date or (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=7)).date()
        latest_date = datetime.date.fromisoformat(latest_obs_date_str)
        age_days = (ref - latest_date).days
        is_stale = age_days > FRESHNESS_MAX_AGE_DAYS
        return {
            "latest_observation_date": latest_obs_date_str,
            "reference_date_wib": ref.isoformat(),
            "age_days": age_days,
            "max_fresh_age_days": FRESHNESS_MAX_AGE_DAYS,
            "freshness_status": "STALE" if is_stale else "FRESH",
            "is_fresh": not is_stale,
            "freshness_note": (
                f"Observasi terakhir {latest_obs_date_str} berumur {age_days} hari (ambang segar <= {FRESHNESS_MAX_AGE_DAYS} hari)."
            ),
        }

    @classmethod
    def get_status(
        cls,
        processed_csv_path: Optional[Path] = None,
        metadata_path: Optional[Path] = None,
        artifacts_dir: Optional[Path] = None,
        reference_date: Optional[datetime.date] = None,
    ) -> Dict[str, Any]:
        csv_path, meta_path, art_dir = cls._resolve_paths(processed_csv_path, metadata_path, artifacts_dir)

        if not csv_path.exists():
            return {
                "available": False,
                "status": "DATASET_UNAVAILABLE",
                "reason": f"Dataset terproses belum tersedia di {csv_path.name}.",
                "commodity": "Cabai Rawit Merah",
                "geographic_scope": "Provinsi Jawa Timur",
                "model_version": None,
                "data_freshness": None,
            }

        if not meta_path.exists():
            return {
                "available": False,
                "status": "MODEL_NOT_TRAINED",
                "reason": "Metadata model belum ditemukan; jalankan pipeline pelatihan terlebih dahulu.",
                "commodity": "Cabai Rawit Merah",
                "geographic_scope": "Provinsi Jawa Timur",
                "model_version": None,
                "data_freshness": None,
            }

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for h in HORIZONS:
            model_file = art_dir / f"xgb_model_h{h}.json"
            if not model_file.exists():
                return {
                    "available": False,
                    "status": "ARTIFACT_UNAVAILABLE",
                    "reason": f"Artefak model horizon H+{h} ({model_file.name}) tidak ditemukan.",
                    "commodity": meta.get("commodity", "Cabai Rawit Merah"),
                    "geographic_scope": meta.get("geographic_scope", "Provinsi Jawa Timur"),
                    "model_version": meta.get("model_version"),
                    "data_freshness": None,
                }

        latest_obs = meta["dataset_date_range"]["end_date"]
        freshness = cls.check_freshness(latest_obs, reference_date=reference_date)
        return {
            "available": True,
            "status": "READY_STALE_DATA" if not freshness["is_fresh"] else "READY",
            "reason": freshness["freshness_note"],
            "commodity": meta["commodity"],
            "geographic_scope": meta["geographic_scope"],
            "market_region_id": meta["market_region_id"],
            "data_source": meta["data_source"],
            "source_url": meta["source_url"],
            "currency": meta["currency"],
            "unit": meta["unit"],
            "model_version": meta["model_version"],
            "trained_at_utc": meta["trained_at_utc"],
            "horizons_days": meta["horizons_days"],
            "latest_observation_date": latest_obs,
            "data_freshness": freshness,
            "provenance_verification_status": meta.get(
                "provenance_verification_status", PROVENANCE_VERIFICATION_STATUS
            ),
            "validation_selected_models_by_horizon": {
                k: v.get("validation_selected_model", v["selected_model"])
                for k, v in meta["evaluation_by_horizon"].items()
            },
            "selected_models_by_horizon": {
                k: v["selected_model"] for k, v in meta["evaluation_by_horizon"].items()
            },
        }

    @classmethod
    def _load_verified_bundle(
        cls,
        processed_csv_path: Optional[Path] = None,
        metadata_path: Optional[Path] = None,
        artifacts_dir: Optional[Path] = None,
    ) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[int, XGBRegressor]]:
        csv_path, meta_path, art_dir = cls._resolve_paths(processed_csv_path, metadata_path, artifacts_dir)
        status = cls.get_status(csv_path, meta_path, art_dir)
        if not status["available"]:
            raise ForecastingUnavailableError(status["status"], status["reason"])

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        raw_cal_df = pd.read_csv(csv_path)
        missing_features = set(FEATURE_COLUMNS) - set(raw_cal_df.columns)
        if missing_features:
            raise ForecastingUnavailableError(
                "FEATURE_SCHEMA_MISMATCH",
                f"Fitur wajib tidak ditemukan pada dataset: {sorted(missing_features)}",
            )
        cal_df = enrich_calendar_provenance(raw_cal_df)

        models: Dict[int, XGBRegressor] = {}
        for h in HORIZONS:
            model_file = art_dir / f"xgb_model_h{h}.json"
            reg = XGBRegressor()
            reg.load_model(str(model_file))
            models[h] = reg

        return cal_df, meta, models

    @classmethod
    def get_historical_series(
        cls,
        limit: int = 120,
        observed_only: bool = True,
        processed_csv_path: Optional[Path] = None,
        metadata_path: Optional[Path] = None,
        artifacts_dir: Optional[Path] = None,
        reference_date: Optional[datetime.date] = None,
    ) -> Dict[str, Any]:
        if limit < 1 or limit > 1500:
            raise ValueError("Parameter limit wajib berada pada rentang 1..1500.")
        cal_df, meta, _ = cls._load_verified_bundle(processed_csv_path, metadata_path, artifacts_dir)
        sub = cal_df[cal_df["is_observed"] == True].copy() if observed_only else cal_df.copy()
        sub = sub.tail(limit)

        latest_obs = meta["dataset_date_range"]["end_date"]
        freshness = cls.check_freshness(latest_obs, reference_date=reference_date)

        records = [
            {
                "observation_date": str(r["observation_date"]),
                "price": round(float(r["price"]), 2),
                "is_observed": bool(r["is_observed"]),
                "source_record_present": bool(r["source_record_present"]),
                "confirmed_market_observation": (
                    None if pd.isna(r.get("confirmed_market_observation")) else bool(r["confirmed_market_observation"])
                ),
                "is_national_holiday_weekday": bool(r["is_national_holiday_weekday"]),
                "observation_provenance": str(r["observation_provenance"]),
                "rmean_7": round(float(r["rmean_7"]), 2) if pd.notna(r.get("rmean_7")) else None,
                "rmean_14": round(float(r["rmean_14"]), 2) if pd.notna(r.get("rmean_14")) else None,
            }
            for _, r in sub.iterrows()
        ]

        return {
            "commodity": meta["commodity"],
            "geographic_scope": meta["geographic_scope"],
            "market_region_id": meta["market_region_id"],
            "data_source": meta["data_source"],
            "source_url": meta["source_url"],
            "currency": meta["currency"],
            "unit": meta["unit"],
            "latest_observation_date": latest_obs,
            "data_freshness": freshness,
            "provenance_verification_status": meta.get(
                "provenance_verification_status", PROVENANCE_VERIFICATION_STATUS
            ),
            "is_observed_field_semantics": meta.get("is_observed_field_semantics", ""),
            "total_observed_records": meta["dataset_date_range"]["observed_market_days"],
            "returned_records_count": len(records),
            "observed_only": observed_only,
            "series": records,
        }

    @classmethod
    def get_forecasts(
        cls,
        origin_date: Optional[str] = None,
        processed_csv_path: Optional[Path] = None,
        metadata_path: Optional[Path] = None,
        artifacts_dir: Optional[Path] = None,
        reference_date: Optional[datetime.date] = None,
    ) -> Dict[str, Any]:
        cal_df, meta, models = cls._load_verified_bundle(processed_csv_path, metadata_path, artifacts_dir)

        if origin_date:
            matches = cal_df[cal_df["observation_date"] == origin_date]
            if matches.empty:
                raise ValueError(f"Tanggal origin {origin_date} tidak ditemukan pada dataset historis.")
            origin_row = matches.iloc[-1]
        else:
            obs_rows = cal_df[cal_df["is_observed"] == True]
            origin_row = obs_rows.iloc[-1]

        if origin_row[FEATURE_COLUMNS].isna().any():
            raise ValueError(
                f"Fitur historis pada tanggal origin {origin_row['observation_date']} belum lengkap untuk inferensi."
            )

        origin_dt = datetime.date.fromisoformat(str(origin_row["observation_date"]))
        origin_price = round(float(origin_row["price"]), 2)
        origin_provenance = str(origin_row.get("observation_provenance", "UNKNOWN_OBSERVATION_PROVENANCE"))
        rmean_14 = float(origin_row["rmean_14"])
        rstd_14 = float(origin_row["rstd_14"])
        X_origin = pd.DataFrame([origin_row[FEATURE_COLUMNS].to_dict()])[FEATURE_COLUMNS].astype(float)

        latest_obs = meta["dataset_date_range"]["end_date"]
        freshness = cls.check_freshness(latest_obs, reference_date=reference_date)

        forecasts: List[Dict[str, Any]] = []
        for h in HORIZONS:
            target_dt = origin_dt + datetime.timedelta(days=h)
            target_is_weekday = target_dt.weekday() < 5
            target_sem = classify_target_date_semantics(target_dt, cal_df, h)
            eval_meta = meta["evaluation_by_horizon"][str(h)]
            selected_model = eval_meta["selected_model"]
            validation_selected_model = eval_meta.get("validation_selected_model", selected_model)

            naive_pred = origin_price
            xgb_delta = float(models[h].predict(X_origin)[0])
            xgb_pred = round(origin_price + xgb_delta, 2)
            if not np.isfinite(xgb_pred) or xgb_pred <= 0:
                raise ForecastingUnavailableError(
                    "INVALID_PREDICTION_OUTPUT",
                    f"Output prediksi H+{h} tidak valid ({xgb_pred}).",
                )

            primary_pred = xgb_pred if selected_model == "xgboost_residual_v1" else naive_pred
            risk = evaluate_risk_signal(
                latest_price=origin_price,
                predicted_price=primary_pred,
                rmean_14=rmean_14,
                rstd_14=rstd_14,
            )

            forecasts.append(
                {
                    "horizon_days": h,
                    "forecast_origin_date": origin_dt.isoformat(),
                    "origin_observation_provenance": origin_provenance,
                    "target_date": target_dt.isoformat(),
                    "target_day_of_week": target_dt.strftime("%A"),
                    "target_is_weekday_market": target_is_weekday,
                    "actual_observation_available": target_sem["actual_observation_available"],
                    "actual_observation_available_semantics": target_sem["actual_observation_available_semantics"],
                    "target_source_record_present": target_sem["target_source_record_present"],
                    "target_confirmed_market_observation": None,
                    "target_schedule_matches_evaluation_scope": target_sem["target_schedule_matches_evaluation_scope"],
                    "holiday_calendar_coverage": target_sem["holiday_calendar_coverage"],
                    "target_schedule_expectation": target_sem["target_schedule_expectation"],
                    "target_observation_status": target_sem["target_observation_status"],
                    "target_is_national_holiday_weekday": target_sem["target_is_national_holiday_weekday"],
                    "target_validation_disclosure": target_sem["target_validation_disclosure"],
                    "origin_observed_price": origin_price,
                    "predicted_price": primary_pred,
                    "validation_selected_model": validation_selected_model,
                    "selected_model": selected_model,
                    "xgboost_predicted_price": xgb_pred,
                    "naive_predicted_price": naive_pred,
                    "predicted_change_rp": round(primary_pred - origin_price, 2),
                    "predicted_change_pct": round(((primary_pred - origin_price) / origin_price) * 100.0, 2),
                    "currency": meta["currency"],
                    "unit": meta["unit"],
                    "evaluation_reference": {
                        "test_naive_mae": eval_meta["test_metrics"]["naive_mae"],
                        "test_xgboost_mae": eval_meta["test_metrics"]["xgboost_mae"],
                        "validation_naive_mae": eval_meta["validation_metrics"]["naive"]["mae"],
                        "validation_xgboost_mae": eval_meta["validation_metrics"]["xgboost"]["mae"],
                        "relative_improvement_pct": eval_meta["test_metrics"]["relative_improvement_pct"],
                        "improvement_magnitude_classification": eval_meta["test_metrics"].get(
                            "improvement_magnitude_classification", "REPORTED"
                        ),
                        "validation_selected_model": validation_selected_model,
                        "serving_model": selected_model,
                        "selection_governance_status": eval_meta.get(
                            "selection_governance_status", "VALIDATION_ONLY_SELECTION"
                        ),
                        "is_independent_holdout_for_revised_policy": eval_meta.get(
                            "is_independent_holdout_for_revised_policy", True
                        ),
                        "serving_override_id": eval_meta.get("serving_override_id"),
                        "evaluated_target_scope": eval_meta.get(
                            "evaluated_target_scope", "SOURCE_RECORD_WEEKDAY_PAIRS_ONLY"
                        ),
                        "applies_directly_to_target_date": target_sem["evaluation_applies_directly_to_target_date"],
                        "target_schedule_matches_evaluation_scope": target_sem["target_schedule_matches_evaluation_scope"],
                        "selection_rationale": eval_meta["selection_rationale"],
                    },
                    "risk_signal": risk,
                }
            )

        # Overall summary risk is the highest severity across horizons
        severity_rank = {"STABLE_NORMAL": 0, "ELEVATED_VOLATILITY_RISK": 1, "HIGH_PRICE_SPIKE_RISK": 2}
        top_forecast = max(forecasts, key=lambda f: severity_rank.get(f["risk_signal"]["risk_level"], 0))

        return {
            "commodity": meta["commodity"],
            "geographic_scope": meta["geographic_scope"],
            "market_region_id": meta["market_region_id"],
            "price_definition": meta["price_definition"],
            "data_source": meta["data_source"],
            "source_url": meta["source_url"],
            "currency": meta["currency"],
            "unit": meta["unit"],
            "model_version": meta["model_version"],
            "provenance_verification_status": meta.get(
                "provenance_verification_status", PROVENANCE_VERIFICATION_STATUS
            ),
            "forecast_origin_date": origin_dt.isoformat(),
            "origin_observed_price": origin_price,
            "origin_observation_provenance": origin_provenance,
            "latest_observation_date": latest_obs,
            "data_freshness": freshness,
            "overall_risk_signal": top_forecast["risk_signal"],
            "operational_boundary_note": (
                "Prediksi harga dan sinyal risiko hanya Early Warning bagi Planner. "
                "Prediksi harga BUKAN bukti verified physical shortage dan TIDAK memicu pengiriman otomatis. "
                "Prediksi yang jatuh pada tanggal akhir pekan/non-survei (seperti H+3 Sabtu) adalah estimasi seri "
                "hari kalender dan tidak memiliki validasi survei pasar langsung pada tanggal target. "
                "Seluruh keputusan alokasi wajib melewati Data Readiness Gate dan Single-Level Human Approval."
            ),
            "forecasts": forecasts,
        }

    @classmethod
    def get_evaluation_and_metadata(
        cls,
        processed_csv_path: Optional[Path] = None,
        metadata_path: Optional[Path] = None,
        artifacts_dir: Optional[Path] = None,
        reference_date: Optional[datetime.date] = None,
    ) -> Dict[str, Any]:
        _, meta, _ = cls._load_verified_bundle(processed_csv_path, metadata_path, artifacts_dir)
        _, _, art_dir = cls._resolve_paths(processed_csv_path, metadata_path, artifacts_dir)

        eval_pred_file = art_dir / "evaluation_predictions.json"
        eval_predictions = (
            json.loads(eval_pred_file.read_text(encoding="utf-8")) if eval_pred_file.exists() else []
        )

        latest_obs = meta["dataset_date_range"]["end_date"]
        freshness = cls.check_freshness(latest_obs, reference_date=reference_date)

        return {
            **meta,
            "latest_observation_date": latest_obs,
            "data_freshness": freshness,
            "test_predictions_vs_actual": eval_predictions,
        }


if __name__ == "__main__":
    meta_out = train_and_evaluate_pipeline()
    print(
        json.dumps(
            {
                "model_version": meta_out["model_version"],
                "evaluation_summary": {
                    h: {
                        "naive_mae": v["test_metrics"]["naive_mae"],
                        "xgboost_mae": v["test_metrics"]["xgboost_mae"],
                        "selected_model": v["selected_model"],
                    }
                    for h, v in meta_out["evaluation_by_horizon"].items()
                },
            },
            indent=2,
        )
    )
