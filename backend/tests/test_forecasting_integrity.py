import datetime
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import forecasting_service
from app.services.forecasting_service import (
    ARTIFACTS_DIR,
    EVAL_PREDICTIONS_JSON_PATH,
    METADATA_PATH,
    SERVING_POLICY_OVERRIDES,
    ForecastingService,
    ForecastingUnavailableError,
    build_horizon_splits,
    select_model_for_horizon,
    train_and_evaluate_pipeline,
)
from app.services.price_data_pipeline import (
    AUDIT_M7B1_JSON_PATH,
    CANONICAL_CSV_PATH,
    FEATURE_COLUMNS,
    HORIZONS,
    HOLIDAY_CALENDAR_VERIFICATION_STATUS,
    NATIONAL_HOLIDAY_WEEKDAYS_2024_2026,
    PROCESSED_CSV_PATH,
    PROCESSED_M7B1_CSV_PATH,
    PROVENANCE_VERIFICATION_STATUS,
    RAW_CSV_PATH,
    RAW_JSON_PATH,
    build_calendar_and_features,
    enrich_calendar_provenance,
    extract_raw_records_from_json,
    sha256_file,
    validate_raw_dataframe,
)

client = TestClient(app)


def test_int01_raw_provenance_integrity():
    assert RAW_JSON_PATH.exists()
    assert RAW_CSV_PATH.exists()
    assert PROCESSED_CSV_PATH.exists()
    assert CANONICAL_CSV_PATH.exists()
    assert PROCESSED_M7B1_CSV_PATH.exists()
    assert AUDIT_M7B1_JSON_PATH.exists()

    assert sha256_file(RAW_JSON_PATH) == "84f8463e73a17a6faceeeca354f5b2bc62069a5077f8e17706532447b1e30c9b"
    assert sha256_file(RAW_CSV_PATH) == "0455dc6d9af1966122078f5b4e2395215e5e3e2989aa548473e3e7a9f0a5bf95"
    assert sha256_file(PROCESSED_CSV_PATH) == "151ac36c1e05791598bc93388b8ac21ee33faf5bb15c66620f2ca66b70d31ba8"
    assert sha256_file(CANONICAL_CSV_PATH) == "151ac36c1e05791598bc93388b8ac21ee33faf5bb15c66620f2ca66b70d31ba8"

    raw_df = extract_raw_records_from_json(RAW_JSON_PATH)
    assert len(raw_df) == 738


def test_int02_calendar_grid_completeness():
    raw_df = extract_raw_records_from_json(RAW_JSON_PATH)
    val_df, audit = validate_raw_dataframe(raw_df)
    cal_df = build_calendar_and_features(val_df)

    assert len(cal_df) == 1032
    assert audit["calendar_day_span"] == 1032
    assert audit["observed_record_count"] == 738
    assert audit["missing_calendar_days_count"] == 294
    assert cal_df["date"].is_monotonic_increasing
    assert (cal_df["date"].diff().dropna() == pd.Timedelta(days=1)).all()


def test_int03_observation_vs_forward_fill():
    cal_df = enrich_calendar_provenance(pd.read_csv(PROCESSED_CSV_PATH))
    weekend_rows = cal_df[~cal_df["source_record_present"]]
    assert len(weekend_rows) == 294
    assert (weekend_rows["is_observed"] == False).all()
    assert (weekend_rows["confirmed_market_observation"] == False).all()
    assert (weekend_rows["observation_provenance"] == "PIPELINE_FORWARD_FILL").all()
    assert set(weekend_rows["dow"].unique()) == {5, 6}


def test_int04_holiday_classification():
    cal_df = enrich_calendar_provenance(pd.read_csv(PROCESSED_CSV_PATH))
    for holiday_dt in ("2024-01-01", "2024-04-10", "2024-12-25"):
        row = cal_df[cal_df["observation_date"] == holiday_dt].iloc[0]
        assert bool(row["source_record_present"]) is True
        assert bool(row["is_observed"]) is True
        assert bool(row["is_national_holiday_weekday"]) is True
        assert row["confirmed_market_observation"] is None
        assert row["observation_provenance"] == "UNKNOWN_OBSERVATION_PROVENANCE"
        assert row["observation_provenance"] != "CONFIRMED_MARKET_OBSERVATION"
        assert row["observation_provenance"] != "SOURCE_PUBLISHED_CARRY_FORWARD"


def test_int05_unknown_provenance_uncertainty():
    raw_df = extract_raw_records_from_json(RAW_JSON_PATH)
    val_df, audit = validate_raw_dataframe(raw_df)
    cal_df = build_calendar_and_features(val_df)

    assert audit["provenance_verification_status"] == PROVENANCE_VERIFICATION_STATUS
    assert audit["confirmed_market_observation_count"] == 0
    assert audit["unknown_observation_provenance_count"] == 738
    assert audit["pipeline_forward_fill_count"] == 294

    src_rows = cal_df[cal_df["source_record_present"]]
    assert len(src_rows) == 738
    assert src_rows["confirmed_market_observation"].isna().all()
    assert (src_rows["observation_provenance"] == "UNKNOWN_OBSERVATION_PROVENANCE").all()


def test_int06_causal_forward_fill():
    raw_df = extract_raw_records_from_json(RAW_JSON_PATH)
    val1, _ = validate_raw_dataframe(raw_df)
    val2 = val1.copy()
    val2.loc[val2["observation_date"] == "2024-01-08", "price"] = 999999.0

    cal1 = build_calendar_and_features(val1)
    cal2 = build_calendar_and_features(val2)

    for wk_dt in ("2024-01-06", "2024-01-07"):
        p1 = float(cal1.loc[cal1["observation_date"] == wk_dt, "price"].iloc[0])
        p2 = float(cal2.loc[cal2["observation_date"] == wk_dt, "price"].iloc[0])
        assert p1 == p2 == 58500.0


def test_int07_target_date_calendar_alignment():
    cal_df = pd.read_csv(PROCESSED_CSV_PATH)
    for h in HORIZONS:
        sub = cal_df.dropna(subset=[f"target_date_{h}"])
        origin_dates = pd.to_datetime(sub["observation_date"])
        target_dates = pd.to_datetime(sub[f"target_date_{h}"])
        assert ((target_dates - origin_dates).dt.days == h).all()


def test_int08_weekend_target_disclosure_h3_from_2026_10_28():
    pred = ForecastingService.get_forecasts(origin_date="2026-10-28")
    by_h = {f["horizon_days"]: f for f in pred["forecasts"]}

    h1 = by_h[1]
    assert h1["target_date"] == "2026-10-29"
    assert h1["target_is_weekday_market"] is True
    assert h1["actual_observation_available"] is False
    assert h1["target_schedule_expectation"] == "WEEKDAY_PUBLICATION_SCHEDULE"
    assert h1["evaluation_reference"]["applies_directly_to_target_date"] is False
    assert h1["evaluation_reference"]["target_schedule_matches_evaluation_scope"] is True
    assert h1["target_confirmed_market_observation"] is None
    assert h1["target_source_record_present"] is False

    h3 = by_h[3]
    assert h3["target_date"] == "2026-10-31"
    assert h3["target_day_of_week"] == "Saturday"
    assert h3["target_is_weekday_market"] is False
    assert h3["actual_observation_available"] is False
    assert h3["target_schedule_expectation"] == "NON_SURVEY_WEEKEND_SCHEDULE"
    assert h3["target_observation_status"] == "NON_SURVEY_WEEKEND_CALENDAR_ESTIMATE"
    assert h3["evaluation_reference"]["applies_directly_to_target_date"] is False
    assert h3["evaluation_reference"]["evaluated_target_scope"] == "SOURCE_RECORD_WEEKDAY_PAIRS_ONLY"
    assert "akhir pekan" in h3["target_validation_disclosure"].lower()

    h7 = by_h[7]
    assert h7["target_date"] == "2026-11-04"
    assert h7["target_is_weekday_market"] is True
    assert h7["actual_observation_available"] is False
    assert h7["target_schedule_expectation"] == "WEEKDAY_PUBLICATION_SCHEDULE"


def test_int09_evaluation_eligibility_weekday_only():
    cal_df = enrich_calendar_provenance(pd.read_csv(PROCESSED_CSV_PATH))
    for h in HORIZONS:
        train_obs, val_obs, test_obs = build_horizon_splits(cal_df, h, strictly_observed=True)
        for split_df in (train_obs, val_obs, test_obs):
            assert (split_df["source_record_present"] == True).all()
            assert (split_df[f"target_source_record_present_{h}"] == True).all()
            assert (split_df["dow"] < 5).all()
            target_dows = pd.to_datetime(split_df[f"target_date_{h}"]).dt.day_of_week
            assert (target_dows < 5).all()

    _, _, test_h3 = build_horizon_splits(cal_df, 3, strictly_observed=True)
    assert len(test_h3) == 36
    assert sorted(int(x) for x in test_h3["dow"].unique()) == [0, 1, 4]


def test_int10_chronological_split_zero_overlap():
    cal_df = pd.read_csv(PROCESSED_CSV_PATH)
    expected_counts = {1: (479, 52, 50), 3: (358, 39, 36), 7: (594, 61, 58)}
    for h, (n_tr, n_va, n_te) in expected_counts.items():
        tr, va, te = build_horizon_splits(cal_df, h, strictly_observed=True)
        assert (len(tr), len(va), len(te)) == (n_tr, n_va, n_te)
        assert tr[f"target_date_{h}"].max() <= "2026-04-30"
        assert va["observation_date"].min() >= "2026-05-01"
        assert va[f"target_date_{h}"].max() <= "2026-07-31"
        assert te["observation_date"].min() >= "2026-08-01"
        assert te[f"target_date_{h}"].max() <= "2026-10-28"
        assert tr[f"target_date_{h}"].max() < va["observation_date"].min()
        assert va[f"target_date_{h}"].max() < te["observation_date"].min()


def test_int11_feature_causality_no_future_leakage():
    raw_df = extract_raw_records_from_json(RAW_JSON_PATH)
    val1, _ = validate_raw_dataframe(raw_df)
    val2 = val1.copy()
    val2.loc[val2["observation_date"] > "2026-05-01", "price"] = 123456.0

    cal1 = build_calendar_and_features(val1)
    cal2 = build_calendar_and_features(val2)

    sub1 = cal1.loc[cal1["observation_date"] <= "2026-05-01", FEATURE_COLUMNS]
    sub2 = cal2.loc[cal2["observation_date"] <= "2026-05-01", FEATURE_COLUMNS]
    pd.testing.assert_frame_equal(sub1, sub2)


def test_int12_model_artifact_stability_and_reproducibility():
    assert sha256_file(ARTIFACTS_DIR / "xgb_model_h1.json") == "9bef42d2d6c54d3471b432fe62ac6491437b91d9bf53005967d6a63088704cbb"
    assert sha256_file(ARTIFACTS_DIR / "xgb_model_h3.json") == "51ced7fc2c3ecba52aef6ab292e0c5b6527db5438e211dd2b447f7c82341904a"
    assert sha256_file(ARTIFACTS_DIR / "xgb_model_h7.json") == "a5525e0fefd12123f1d2bef226273cb22314fd3270cbd26e7821fccde633b6f2"
    assert sha256_file(EVAL_PREDICTIONS_JSON_PATH) == "f90fa52e0cdfbda76bb579db0cafc1d99e33df7ceeada425dc703a6576e02c3a"

    res = ForecastingService.get_forecasts(origin_date="2026-10-28")
    by_h = {f["horizon_days"]: f for f in res["forecasts"]}
    assert by_h[1]["predicted_price"] == 54606.76
    assert by_h[3]["predicted_price"] == 54995.28
    assert by_h[7]["predicted_price"] == 55250.0
    assert by_h[7]["xgboost_predicted_price"] == 51713.27


def test_int13_r3_model_selection_ignores_test_set_and_uses_explicit_override(tmp_path: Path):
    sel_no_override = select_model_for_horizon(7, val_naive_mae=6363.93, val_xgb_mae=6200.58, serving_overrides={})
    assert sel_no_override["validation_selected_model"] == "xgboost_residual_v1"
    assert sel_no_override["serving_model"] == "xgboost_residual_v1"
    assert sel_no_override["selection_governance_status"] == "VALIDATION_ONLY_SELECTION"
    assert sel_no_override["is_independent_holdout_for_revised_policy"] is True

    sel_with_override = select_model_for_horizon(
        7, val_naive_mae=6363.93, val_xgb_mae=6200.58, serving_overrides=SERVING_POLICY_OVERRIDES
    )
    assert sel_with_override["validation_selected_model"] == "xgboost_residual_v1"
    assert sel_with_override["serving_model"] == "naive_persistence_v1"
    assert sel_with_override["selection_governance_status"] == "POST_TEST_EXPLORATORY_POLICY_REVISION"
    assert sel_with_override["is_independent_holdout_for_revised_policy"] is False
    assert sel_with_override["serving_override_id"] == "OVERRIDE-ADR021-H7-NAIVE-SERVING-V1"

    cal_df = pd.read_csv(PROCESSED_CSV_PATH)
    perturbed = cal_df.copy()
    test_mask = perturbed["observation_date"] >= "2026-08-01"
    for h in HORIZONS:
        perturbed.loc[test_mask, f"target_price_{h}"] = perturbed.loc[test_mask, "price"]
        perturbed.loc[test_mask, f"target_delta_{h}"] = 0.0

    perturbed_csv = tmp_path / "perturbed_test.csv"
    perturbed.to_csv(perturbed_csv, index=False)

    out_art_dir = tmp_path / "artifacts"
    out_art_dir.mkdir()
    for h in HORIZONS:
        src_model = ARTIFACTS_DIR / f"xgb_model_h{h}.json"
        (out_art_dir / f"xgb_model_h{h}.json").write_bytes(src_model.read_bytes())
    (out_art_dir / "model_metadata.json").write_text(METADATA_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    meta_no_override = train_and_evaluate_pipeline(
        processed_csv_path=perturbed_csv,
        artifacts_dir=out_art_dir,
        preserve_existing_models=True,
        serving_overrides={},
    )
    for h_str in ("1", "3", "7"):
        ev = meta_no_override["evaluation_by_horizon"][h_str]
        assert ev["test_metrics"]["naive_mae"] == 0.0
        assert ev["test_metrics"]["relative_improvement_pct"] is None
        assert ev["test_metrics"]["xgboost_mae"] > 0.0
        assert ev["validation_selected_model"] == "xgboost_residual_v1"
        assert ev["selected_model"] == "xgboost_residual_v1"


def test_int14_api_metadata_and_target_disclosures():
    st = client.get("/forecasting/status")
    assert st.status_code == 200
    st_body = st.json()
    assert st_body["provenance_verification_status"] == PROVENANCE_VERIFICATION_STATUS
    assert st_body["validation_selected_models_by_horizon"] == {
        "1": "xgboost_residual_v1",
        "3": "xgboost_residual_v1",
        "7": "xgboost_residual_v1",
    }
    assert st_body["selected_models_by_horizon"] == {
        "1": "xgboost_residual_v1",
        "3": "xgboost_residual_v1",
        "7": "naive_persistence_v1",
    }

    hist = client.get("/forecasting/historical?limit=30&observed_only=false")
    assert hist.status_code == 200
    hist_body = hist.json()
    assert hist_body["provenance_verification_status"] == PROVENANCE_VERIFICATION_STATUS
    wk_pts = [p for p in hist_body["series"] if not p["is_observed"]]
    obs_pts = [p for p in hist_body["series"] if p["is_observed"]]
    assert len(wk_pts) > 0 and len(obs_pts) > 0
    assert all(p["observation_provenance"] == "PIPELINE_FORWARD_FILL" and p["confirmed_market_observation"] is False for p in wk_pts)
    assert all(p["observation_provenance"] == "UNKNOWN_OBSERVATION_PROVENANCE" and p["confirmed_market_observation"] is None for p in obs_pts)

    pred = client.get("/forecasting/predictions")
    assert pred.status_code == 200
    pred_body = pred.json()
    assert pred_body["provenance_verification_status"] == PROVENANCE_VERIFICATION_STATUS
    h3_item = [f for f in pred_body["forecasts"] if f["horizon_days"] == 3][0]
    assert h3_item["target_date"] == "2026-10-31"
    assert h3_item["target_schedule_expectation"] == "NON_SURVEY_WEEKEND_SCHEDULE"
    assert h3_item["actual_observation_available"] is False
    assert h3_item["evaluation_reference"]["applies_directly_to_target_date"] is False

    h7_item = [f for f in pred_body["forecasts"] if f["horizon_days"] == 7][0]
    assert h7_item["validation_selected_model"] == "xgboost_residual_v1"
    assert h7_item["selected_model"] == "naive_persistence_v1"
    assert h7_item["evaluation_reference"]["selection_governance_status"] == "POST_TEST_EXPLORATORY_POLICY_REVISION"
    assert h7_item["evaluation_reference"]["is_independent_holdout_for_revised_policy"] is False


def test_int15_missing_and_stale_data_safety(monkeypatch, tmp_path: Path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    monkeypatch.setattr(forecasting_service, "ARTIFACTS_DIR", empty_dir)
    monkeypatch.setattr(forecasting_service, "METADATA_PATH", empty_dir / "model_metadata.json")

    res_st = client.get("/forecasting/status")
    assert res_st.status_code == 200
    assert res_st.json()["available"] is False
    assert res_st.json()["status"] == "MODEL_NOT_TRAINED"

    res_pred = client.get("/forecasting/predictions")
    assert res_pred.status_code == 503


def test_int16_missing_weekday_causal_fill():
    raw = extract_raw_records_from_json()
    missing_date = "2024-01-09"
    raw = raw.loc[raw["observation_date"] != missing_date].copy()
    validated, audit = validate_raw_dataframe(raw)
    calendar = build_calendar_and_features(validated)
    changed = validated.copy()
    changed.loc[changed["observation_date"] > missing_date, "price"] = 999999.0
    changed_calendar = build_calendar_and_features(changed)
    row = calendar.loc[calendar["observation_date"] == missing_date].iloc[0]
    previous_price = validated.loc[validated["observation_date"] == "2024-01-08", "price"].iloc[0]
    assert row["dow"] == 1
    assert not bool(row["source_record_present"])
    assert row["confirmed_market_observation"] is False
    assert row["observation_provenance"] == "PIPELINE_FORWARD_FILL"
    assert row["days_since_observed"] == 1
    assert row["price"] == previous_price
    pd.testing.assert_frame_equal(
        calendar.loc[calendar["observation_date"] <= missing_date, FEATURE_COLUMNS],
        changed_calendar.loc[changed_calendar["observation_date"] <= missing_date, FEATURE_COLUMNS],
    )
    assert audit["missing_business_days_count"] == 1
    assert audit["pipeline_forward_fill_count"] == int((~calendar["source_record_present"]).sum())
    origin = calendar.loc[calendar["observation_date"] == "2024-01-08"].iloc[0]
    assert origin["target_price_1"] == previous_price
    assert origin["target_observation_provenance_1"] == "PIPELINE_FORWARD_FILL"
    assert origin["target_confirmed_market_observation_1"] is False


@pytest.mark.parametrize("horizon", HORIZONS)
def test_int17_unavailable_tail_target_provenance(horizon):
    validated, _ = validate_raw_dataframe(extract_raw_records_from_json())
    calendar = build_calendar_and_features(validated)
    for frame in (calendar, enrich_calendar_provenance(pd.read_csv(PROCESSED_CSV_PATH))):
        missing = frame[f"target_price_{horizon}"].isna()
        assert missing.sum() == horizon
        assert frame.loc[missing, f"target_observation_provenance_{horizon}"].eq("TARGET_UNAVAILABLE").all()
        assert frame.loc[missing, f"target_confirmed_market_observation_{horizon}"].isna().all()
        assert not frame.loc[missing, f"target_source_record_present_{horizon}"].any()
        available_fill = ~missing & ~frame[f"target_source_record_present_{horizon}"]
        assert frame.loc[available_fill, f"target_observation_provenance_{horizon}"].eq("PIPELINE_FORWARD_FILL").all()
        assert frame.loc[available_fill, f"target_confirmed_market_observation_{horizon}"].eq(False).all()
    inconsistent = calendar.copy()
    inconsistent.loc[inconsistent.index[-1], f"target_is_observed_{horizon}"] = True
    row = enrich_calendar_provenance(inconsistent).iloc[-1]
    assert row[f"target_observation_provenance_{horizon}"] == "TARGET_UNAVAILABLE"
    assert row[f"target_confirmed_market_observation_{horizon}"] is None


def test_int18_holiday_calendar_unknown_coverage():
    assert NATIONAL_HOLIDAY_WEEKDAYS_2024_2026 == {
        "2024-01-01": "Tahun Baru 2024 Masehi",
        "2024-04-10": "Hari Raya Idulfitri 1445 H",
        "2024-12-25": "Hari Raya Natal",
    }
    validated, audit = validate_raw_dataframe(extract_raw_records_from_json())
    assert audit["holiday_calendar_verification_status"] == HOLIDAY_CALENDAR_VERIFICATION_STATUS
    assert HOLIDAY_CALENDAR_VERIFICATION_STATUS == "AUDIT_SAMPLES_ONLY_NOT_COMPLETE_OFFICIAL_CALENDAR"
    calendar = build_calendar_and_features(validated)
    for frame in (validated, calendar):
        outside_samples = ~frame["observation_date"].isin(NATIONAL_HOLIDAY_WEEKDAYS_2024_2026)
        assert frame.loc[outside_samples, "is_national_holiday_weekday"].isna().all()
        assert frame.loc[outside_samples, "national_holiday_name"].isna().all()
    assert audit["national_holiday_weekdays_count"] == 3
    assert "unchanged_regular_weekdays_count" not in audit
    assert audit["unchanged_holiday_status_unknown_count"] == int(
        (validated["daily_diff"].eq(0) & ~validated["observation_date"].isin(NATIONAL_HOLIDAY_WEEKDAYS_2024_2026)).sum()
    )


def test_int19_raw_csv_exactly_matches_extraction():
    extracted = extract_raw_records_from_json()
    persisted = pd.read_csv(RAW_CSV_PATH, keep_default_na=False, dtype={"raw_price": str})
    pd.testing.assert_frame_equal(persisted, extracted, check_exact=True)


def test_int20_original_metadata_recoverable_from_historical_evidence(tmp_path: Path):
    evidence_path = Path(__file__).parent / "logs" / "m7b_experimental_evidence_20261028.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    original = evidence["model_metadata_and_evaluation"]
    recovered_path = tmp_path / "model_metadata.json"
    recovered_path.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")
    recovered = json.loads(recovered_path.read_text(encoding="utf-8"))
    assert recovered == original
    assert json.loads((ARTIFACTS_DIR / "model_metadata.json").read_text(encoding="utf-8")) == recovered
    for horizon in HORIZONS:
        artifact = ARTIFACTS_DIR / f"xgb_model_h{horizon}.json"
        assert sha256_file(artifact) == recovered["hyperparameters_by_horizon"][str(horizon)]["artifact_sha256"]
    assert sha256_file(EVAL_PREDICTIONS_JSON_PATH) == recovered["evaluation_predictions_sha256"]
