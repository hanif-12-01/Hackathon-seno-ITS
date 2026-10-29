import datetime
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from xgboost import XGBRegressor

from app.services.forecasting_service import (
    ARTIFACTS_DIR,
    EVAL_PREDICTIONS_JSON_PATH,
    METADATA_PATH,
    ForecastingService,
    ForecastingUnavailableError,
    build_horizon_splits,
    compute_metrics,
    evaluate_risk_signal,
)
from app.services.price_data_pipeline import FEATURE_COLUMNS, HORIZONS, PROCESSED_CSV_PATH


def test_mae_and_metrics_calculation_correctness():
    y_true = pd.Series([50000.0, 52000.0, 48000.0, 60000.0])
    y_pred = np.array([51000.0, 50000.0, 49000.0, 58000.0])
    # abs errors: 1000, 2000, 1000, 2000 -> mean = 1500.0
    m = compute_metrics(y_true, y_pred)
    assert m["mae"] == 1500.0
    assert m["rmse"] == round(float(np.sqrt(np.mean([1000**2, 2000**2, 1000**2, 2000**2]))), 2)
    assert m["mape_pct"] > 0
    assert m["smape_pct"] > 0


def test_strict_chronological_split_and_test_set_isolation():
    cal_df = pd.read_csv(PROCESSED_CSV_PATH)
    for h in HORIZONS:
        train_df, val_df, test_df = build_horizon_splits(cal_df, h, strictly_observed=True)
        assert len(train_df) > 200
        assert len(val_df) > 30
        assert len(test_df) > 30

        td_col = f"target_date_{h}"
        # Zero target leakage across split boundaries: all training targets occur strictly before validation origins
        assert train_df[td_col].max() < val_df["observation_date"].min()
        assert val_df[td_col].max() < test_df["observation_date"].min()

        # Verify exact calendar horizon alignment (h calendar days, never h observation rows)
        for _, row in test_df.head(15).iterrows():
            o_dt = datetime.date.fromisoformat(str(row["observation_date"]))
            t_dt = datetime.date.fromisoformat(str(row[td_col]))
            assert (t_dt - o_dt).days == h


def test_naive_baseline_and_xgboost_evaluation_against_baseline():
    assert METADATA_PATH.exists()
    assert EVAL_PREDICTIONS_JSON_PATH.exists()

    meta = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    eval_rows = json.loads(EVAL_PREDICTIONS_JSON_PATH.read_text(encoding="utf-8"))
    assert len(eval_rows) > 100

    for h in HORIZONS:
        h_meta = meta["evaluation_by_horizon"][str(h)]
        h_rows = [r for r in eval_rows if r["horizon_days"] == h]
        assert len(h_rows) == h_meta["sample_counts"]["test_observed_pairs"]

        # Naive baseline must equal origin price for every evaluation sample
        for r in h_rows:
            assert r["naive_predicted_price"] == r["origin_price"]
            o_dt = datetime.date.fromisoformat(r["origin_date"])
            t_dt = datetime.date.fromisoformat(r["target_date"])
            assert (t_dt - o_dt).days == h

        # Recompute MAE directly from saved evaluation predictions and verify exact match
        manual_naive_mae = round(float(np.mean([r["naive_abs_error"] for r in h_rows])), 2)
        manual_xgb_mae = round(float(np.mean([r["xgboost_abs_error"] for r in h_rows])), 2)
        assert manual_naive_mae == h_meta["test_metrics"]["naive_mae"]
        assert manual_xgb_mae == h_meta["test_metrics"]["xgboost_mae"]

        # Verify honest evidence-based model selection rule
        if h in (1, 3):
            assert h_meta["test_metrics"]["xgboost_mae"] < h_meta["test_metrics"]["naive_mae"]
            assert h_meta["selected_model"] == "xgboost_residual_v1"
        elif h == 7:
            assert h_meta["test_metrics"]["naive_mae"] < h_meta["test_metrics"]["xgboost_mae"]
            assert h_meta["selected_model"] == "naive_persistence_v1"


def test_model_artifact_reload_and_consistent_inference():
    res1 = ForecastingService.get_forecasts(origin_date="2026-10-28")
    res2 = ForecastingService.get_forecasts(origin_date="2026-10-28")

    assert res1["forecast_origin_date"] == "2026-10-28"
    assert res1["origin_observed_price"] == 55250.0
    assert len(res1["forecasts"]) == 3

    for f1, f2 in zip(res1["forecasts"], res2["forecasts"]):
        assert f1["horizon_days"] in (1, 3, 7)
        assert f1["predicted_price"] == f2["predicted_price"]
        assert f1["xgboost_predicted_price"] == f2["xgboost_predicted_price"]
        assert f1["naive_predicted_price"] == 55250.0
        assert np.isfinite(f1["predicted_price"]) and f1["predicted_price"] > 0
        assert np.isfinite(f1["xgboost_predicted_price"]) and f1["xgboost_predicted_price"] > 0

        expected_target = (
            datetime.date(2026, 10, 28) + datetime.timedelta(days=f1["horizon_days"])
        ).isoformat()
        assert f1["target_date"] == expected_target

    # Also verify direct XGBRegressor reload from disk produces identical delta
    cal_df = pd.read_csv(PROCESSED_CSV_PATH)
    last_row = cal_df[cal_df["observation_date"] == "2026-10-28"].iloc[-1]
    X_last = pd.DataFrame([last_row[FEATURE_COLUMNS].to_dict()])[FEATURE_COLUMNS].astype(float)
    for f in res1["forecasts"]:
        h = f["horizon_days"]
        reg = XGBRegressor()
        reg.load_model(str(ARTIFACTS_DIR / f"xgb_model_h{h}.json"))
        direct_pred = round(float(last_row["price"]) + float(reg.predict(X_last)[0]), 2)
        assert direct_pred == f["xgboost_predicted_price"]


def test_model_unavailable_and_missing_feature_behavior(tmp_path: Path):
    missing_csv = tmp_path / "nonexistent.csv"
    st_no_data = ForecastingService.get_status(processed_csv_path=missing_csv)
    assert st_no_data["available"] is False
    assert st_no_data["status"] == "DATASET_UNAVAILABLE"

    with pytest.raises(ForecastingUnavailableError) as exc_info:
        ForecastingService.get_forecasts(processed_csv_path=missing_csv)
    assert exc_info.value.reason_code == "DATASET_UNAVAILABLE"

    # Missing model metadata
    st_no_meta = ForecastingService.get_status(
        processed_csv_path=PROCESSED_CSV_PATH,
        metadata_path=tmp_path / "missing_meta.json",
    )
    assert st_no_meta["available"] is False
    assert st_no_meta["status"] == "MODEL_NOT_TRAINED"

    # Missing artifact file
    empty_art_dir = tmp_path / "empty_artifacts"
    empty_art_dir.mkdir()
    st_no_art = ForecastingService.get_status(
        processed_csv_path=PROCESSED_CSV_PATH,
        metadata_path=METADATA_PATH,
        artifacts_dir=empty_art_dir,
    )
    assert st_no_art["available"] is False
    assert st_no_art["status"] == "ARTIFACT_UNAVAILABLE"

    # Missing feature column in processed CSV
    bad_csv = tmp_path / "bad_features.csv"
    df_bad = pd.read_csv(PROCESSED_CSV_PATH).drop(columns=["rmean_7"])
    df_bad.to_csv(bad_csv, index=False)
    with pytest.raises(ForecastingUnavailableError) as exc_feat:
        ForecastingService.get_forecasts(processed_csv_path=bad_csv)
    assert exc_feat.value.reason_code == "FEATURE_SCHEMA_MISMATCH"


def test_stale_data_handling_without_timestamp_mutation():
    fresh_status = ForecastingService.get_status(reference_date=datetime.date(2026, 10, 28))
    assert fresh_status["available"] is True
    assert fresh_status["status"] == "READY"
    assert fresh_status["data_freshness"]["freshness_status"] == "FRESH"
    assert fresh_status["data_freshness"]["is_fresh"] is True

    stale_status = ForecastingService.get_status(reference_date=datetime.date(2026, 11, 15))
    assert stale_status["available"] is True
    assert stale_status["status"] == "READY_STALE_DATA"
    assert stale_status["data_freshness"]["freshness_status"] == "STALE"
    assert stale_status["data_freshness"]["is_fresh"] is False
    assert stale_status["latest_observation_date"] == "2026-10-28"


def test_rule_based_risk_signal_thresholds_and_separation():
    high = evaluate_risk_signal(latest_price=50000.0, predicted_price=56000.0, rmean_14=50000.0, rstd_14=2000.0)
    assert high["risk_level"] == "HIGH_PRICE_SPIKE_RISK"
    assert high["is_calibrated_probability"] is False
    assert high["triggers_automatic_dispatch"] is False

    elevated = evaluate_risk_signal(latest_price=50000.0, predicted_price=53000.0, rmean_14=50000.0, rstd_14=2000.0)
    assert elevated["risk_level"] == "ELEVATED_VOLATILITY_RISK"

    stable = evaluate_risk_signal(latest_price=50000.0, predicted_price=50500.0, rmean_14=50000.0, rstd_14=1500.0)
    assert stable["risk_level"] == "STABLE_NORMAL"
