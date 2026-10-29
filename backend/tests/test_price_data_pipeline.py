import datetime
import pandas as pd
import pytest

from app.services.price_data_pipeline import (
    AUDIT_JSON_PATH,
    CANONICAL_CSV_PATH,
    FEATURE_COLUMNS,
    PROCESSED_CSV_PATH,
    RAW_CSV_PATH,
    RAW_JSON_PATH,
    DataValidationError,
    build_calendar_and_features,
    extract_raw_records_from_json,
    validate_raw_dataframe,
)


def _make_valid_fixture(n_days: int = 25) -> pd.DataFrame:
    base_date = datetime.date(2026, 1, 5)  # Monday
    rows = []
    cur = base_date
    for i in range(n_days):
        if cur.weekday() < 5:
            rows.append(
                {
                    "observation_date": cur.isoformat(),
                    "commodity": "Cabai Rawit Merah",
                    "geographic_scope": "Provinsi Jawa Timur",
                    "market_region_id": "PIHPS-PROV-16-PASAR-TRADISIONAL",
                    "price": 50000.0 + (i * 500.0),
                    "currency": "IDR",
                    "unit": "Rp/kg",
                    "source": "TEST_FIXTURE_ONLY",
                    "source_url": "https://www.bi.go.id/hargapangan",
                    "retrieval_timestamp": "2026-10-28T00:00:00+00:00",
                }
            )
        cur += datetime.timedelta(days=1)
    return pd.DataFrame(rows)


def test_data_pipeline_rejects_invalid_date():
    df = _make_valid_fixture()
    df.loc[0, "observation_date"] = "2026-13-45"
    with pytest.raises(DataValidationError, match="observation_date"):
        validate_raw_dataframe(df)


def test_data_pipeline_rejects_duplicate_records():
    df = _make_valid_fixture()
    dup = pd.concat([df, df.iloc[[2]]], ignore_index=True)
    with pytest.raises(DataValidationError, match="duplikasi"):
        validate_raw_dataframe(dup)


@pytest.mark.parametrize("bad_price", [0.0, -5000.0, float("nan"), float("inf")])
def test_data_pipeline_rejects_zero_negative_or_nonfinite_prices(bad_price: float):
    df = _make_valid_fixture()
    df.loc[3, "price"] = bad_price
    with pytest.raises(DataValidationError, match="harga"):
        validate_raw_dataframe(df)


def test_data_pipeline_rejects_unit_or_currency_mismatch():
    df_unit = _make_valid_fixture()
    df_unit.loc[1, "unit"] = "Rp/ton"
    with pytest.raises(DataValidationError, match="Satuan"):
        validate_raw_dataframe(df_unit)

    df_curr = _make_valid_fixture()
    df_curr.loc[1, "currency"] = "USD"
    with pytest.raises(DataValidationError, match="Mata uang"):
        validate_raw_dataframe(df_curr)


def test_data_pipeline_rejects_mixed_geography_or_commodity():
    df_geo = _make_valid_fixture()
    df_geo.loc[1, "geographic_scope"] = "Nasional"
    with pytest.raises(DataValidationError, match="cakupan geografis"):
        validate_raw_dataframe(df_geo)

    df_comm = _make_valid_fixture()
    df_comm.loc[1, "commodity"] = "Cabai Merah Besar"
    with pytest.raises(DataValidationError, match="komoditas"):
        validate_raw_dataframe(df_comm)


def test_data_pipeline_chronological_sorting_and_missing_weekend_handling():
    df = _make_valid_fixture(30).sample(frac=1.0, random_state=42).reset_index(drop=True)
    validated, audit = validate_raw_dataframe(df)
    assert validated["date"].is_monotonic_increasing
    assert audit["missing_business_days_count"] == 0
    assert audit["missing_calendar_days_count"] > 0

    cal = build_calendar_and_features(validated)
    assert cal["date"].is_monotonic_increasing
    assert len(cal) == 30
    # Weekend days must carry forward Friday's price causally, never Saturday/Monday future
    sat_row = cal.loc[cal["date"].dt.day_of_week == 5].iloc[0]
    fri_row = cal.loc[cal["date"] == sat_row["date"] - pd.Timedelta(days=1)].iloc[0]
    assert sat_row["is_observed"] == False
    assert sat_row["price"] == fri_row["price"]
    assert sat_row["days_since_observed"] == 1


def test_causal_features_have_zero_future_leakage():
    df1 = _make_valid_fixture(35)
    df2 = df1.copy()
    # Mutate future prices after index 10 drastically
    df2.loc[df2.index > 10, "price"] = 999999.0

    val1, _ = validate_raw_dataframe(df1)
    val2, _ = validate_raw_dataframe(df2)
    cal1 = build_calendar_and_features(val1)
    cal2 = build_calendar_and_features(val2)

    cutoff_date = val1.loc[10, "date"]
    sub1 = cal1.loc[cal1["date"] <= cutoff_date, FEATURE_COLUMNS]
    sub2 = cal2.loc[cal2["date"] <= cutoff_date, FEATURE_COLUMNS]
    pd.testing.assert_frame_equal(sub1, sub2)


def test_real_pihps_dataset_provenance_and_integrity():
    assert RAW_JSON_PATH.exists()
    assert RAW_CSV_PATH.exists()
    assert PROCESSED_CSV_PATH.exists()
    assert CANONICAL_CSV_PATH.exists()
    assert AUDIT_JSON_PATH.exists()

    raw_df = extract_raw_records_from_json(RAW_JSON_PATH)
    validated, audit = validate_raw_dataframe(raw_df)
    assert len(validated) == 738
    assert audit["start_date"] == "2024-01-01"
    assert audit["end_date"] == "2026-10-28"
    assert audit["duplicate_records_count"] == 0
    assert audit["invalid_zero_or_negative_count"] == 0
    assert audit["missing_business_days_count"] == 0
    assert audit["commodity"] == "Cabai Rawit Merah"
    assert audit["geographic_scope"] == "Provinsi Jawa Timur"
