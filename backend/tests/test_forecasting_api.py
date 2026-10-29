import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services import forecasting_service
from app.services.forecasting_service import ForecastingService


client = TestClient(app)


def test_api_forecasting_status_ready():
    res = client.get("/forecasting/status")
    assert res.status_code == 200
    body = res.json()
    assert body["available"] is True
    assert body["status"] in ("READY", "READY_STALE_DATA")
    assert body["commodity"] == "Cabai Rawit Merah"
    assert body["geographic_scope"] == "Provinsi Jawa Timur"
    assert body["data_source"] == "PIHPS Nasional - Bank Indonesia"
    assert body["model_version"] == "m7b-20261028-v1"
    assert body["latest_observation_date"] == "2026-10-28"
    assert body["horizons_days"] == [1, 3, 7]
    assert body["selected_models_by_horizon"] == {
        "1": "xgboost_residual_v1",
        "3": "xgboost_residual_v1",
        "7": "naive_persistence_v1",
    }


def test_api_forecasting_historical_retrieval_and_validation():
    res = client.get("/forecasting/historical?limit=60&observed_only=true")
    assert res.status_code == 200
    body = res.json()
    assert body["commodity"] == "Cabai Rawit Merah"
    assert body["returned_records_count"] == 60
    assert body["total_observed_records"] == 738
    assert body["series"][-1]["observation_date"] == "2026-10-28"
    assert body["series"][-1]["price"] == 55250.0
    assert all(pt["is_observed"] is True for pt in body["series"])

    # Invalid limit validation
    bad_low = client.get("/forecasting/historical?limit=0")
    assert bad_low.status_code == 422
    bad_high = client.get("/forecasting/historical?limit=5000")
    assert bad_high.status_code == 422


def test_api_forecasting_predictions_and_invalid_origin():
    res = client.get("/forecasting/predictions")
    assert res.status_code == 200
    body = res.json()
    assert body["forecast_origin_date"] == "2026-10-28"
    assert body["origin_observed_price"] == 55250.0
    assert body["model_version"] == "m7b-20261028-v1"
    assert len(body["forecasts"]) == 3

    horizons = {f["horizon_days"]: f for f in body["forecasts"]}
    assert set(horizons.keys()) == {1, 3, 7}
    assert horizons[1]["target_date"] == "2026-10-29"
    assert horizons[3]["target_date"] == "2026-10-31"
    assert horizons[7]["target_date"] == "2026-11-04"

    assert horizons[1]["selected_model"] == "xgboost_residual_v1"
    assert horizons[1]["predicted_price"] == horizons[1]["xgboost_predicted_price"]
    assert horizons[7]["selected_model"] == "naive_persistence_v1"
    assert horizons[7]["predicted_price"] == horizons[7]["naive_predicted_price"] == 55250.0
    assert horizons[7]["xgboost_predicted_price"] > 0

    # Specific historical origin date
    res_hist = client.get("/forecasting/predictions?origin_date=2026-10-20")
    assert res_hist.status_code == 200
    assert res_hist.json()["forecast_origin_date"] == "2026-10-20"
    assert res_hist.json()["origin_observed_price"] == 63000.0

    # Unknown or early-unwarmed origin date -> 422
    res_unknown = client.get("/forecasting/predictions?origin_date=2019-01-01")
    assert res_unknown.status_code == 422
    res_unwarmed = client.get("/forecasting/predictions?origin_date=2024-01-02")
    assert res_unwarmed.status_code == 422


def test_api_forecasting_evaluation_and_metadata():
    res = client.get("/forecasting/evaluation")
    assert res.status_code == 200
    body = res.json()
    assert body["model_version"] == "m7b-20261028-v1"
    assert len(body["feature_schema"]) == 19
    assert body["evaluation_by_horizon"]["1"]["test_metrics"]["naive_mae"] == 1022.0
    assert body["evaluation_by_horizon"]["1"]["test_metrics"]["xgboost_mae"] == 911.74
    assert body["evaluation_by_horizon"]["3"]["test_metrics"]["naive_mae"] == 2459.72
    assert body["evaluation_by_horizon"]["3"]["test_metrics"]["xgboost_mae"] == 2455.56
    assert body["evaluation_by_horizon"]["7"]["test_metrics"]["naive_mae"] == 4241.38
    assert body["evaluation_by_horizon"]["7"]["test_metrics"]["xgboost_mae"] == 4355.01
    assert len(body["test_predictions_vs_actual"]) == 144


def test_api_missing_model_artifact_returns_503_without_fake_data(monkeypatch, tmp_path: Path):
    empty_dir = tmp_path / "missing_artifacts"
    empty_dir.mkdir()
    monkeypatch.setattr(forecasting_service, "ARTIFACTS_DIR", empty_dir)
    monkeypatch.setattr(forecasting_service, "METADATA_PATH", empty_dir / "model_metadata.json")

    st = client.get("/forecasting/status")
    assert st.status_code == 200
    assert st.json()["available"] is False
    assert st.json()["status"] == "MODEL_NOT_TRAINED"

    for endpoint in ("/forecasting/historical", "/forecasting/predictions", "/forecasting/evaluation"):
        err_res = client.get(endpoint)
        assert err_res.status_code == 503
        assert err_res.json()["detail"]["code"] == "MODEL_NOT_TRAINED"


def test_api_stale_dataset_behavior(monkeypatch):
    orig_check = ForecastingService.check_freshness
    monkeypatch.setattr(
        ForecastingService,
        "check_freshness",
        classmethod(lambda cls, latest_obs_date_str, reference_date=None: orig_check(latest_obs_date_str, datetime.date(2026, 11, 20))),
    )
    st = client.get("/forecasting/status")
    assert st.status_code == 200
    assert st.json()["status"] == "READY_STALE_DATA"
    assert st.json()["data_freshness"]["freshness_status"] == "STALE"
    assert st.json()["latest_observation_date"] == "2026-10-28"

    pred = client.get("/forecasting/predictions")
    assert pred.status_code == 200
    assert pred.json()["data_freshness"]["freshness_status"] == "STALE"
    assert pred.json()["latest_observation_date"] == "2026-10-28"
