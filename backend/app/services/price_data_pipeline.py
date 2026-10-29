import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RAW_JSON_PATH = PROJECT_ROOT / "data" / "raw" / "pihps_bi_jatim_cabai_rawit_merah_raw.json"
RAW_CSV_PATH = PROJECT_ROOT / "data" / "raw" / "pihps_bi_jatim_cabai_rawit_merah_raw.csv"
PROCESSED_CSV_PATH = PROJECT_ROOT / "data" / "processed" / "cabai_rawit_merah_jatim_daily_processed.csv"
PROCESSED_M7B1_CSV_PATH = PROJECT_ROOT / "data" / "processed" / "cabai_rawit_merah_jatim_daily_processed_m7b1.csv"
CANONICAL_CSV_PATH = PROJECT_ROOT / "data" / "chili_price_daily_2024_2026.csv"
AUDIT_JSON_PATH = PROJECT_ROOT / "data" / "processed" / "dataset_audit_and_provenance.json"
AUDIT_M7B1_JSON_PATH = PROJECT_ROOT / "data" / "processed" / "dataset_audit_and_provenance_m7b1.json"

PROVENANCE_VERIFICATION_STATUS = "PARTIALLY VERIFIED — SOURCE RECORD CONFIRMED, MARKET SURVEY STATUS UNKNOWN"

PROVENANCE_CATEGORIES: Tuple[str, ...] = (
    "SOURCE_RECORD_PRESENT",
    "CONFIRMED_MARKET_OBSERVATION",
    "SOURCE_PUBLISHED_CARRY_FORWARD",
    "PIPELINE_FORWARD_FILL",
    "UNKNOWN_OBSERVATION_PROVENANCE",
)

HOLIDAY_CALENDAR_VERIFICATION_STATUS = "AUDIT_SAMPLES_ONLY_NOT_COMPLETE_OFFICIAL_CALENDAR"

NATIONAL_HOLIDAY_WEEKDAYS_2024_2026: Dict[str, str] = {
    "2024-01-01": "Tahun Baru 2024 Masehi",
    "2024-04-10": "Hari Raya Idulfitri 1445 H",
    "2024-12-25": "Hari Raya Natal",
}

FEATURE_COLUMNS: List[str] = [
    "price",
    "lag_1",
    "diff_1",
    "lag_2",
    "diff_2",
    "lag_3",
    "diff_3",
    "lag_5",
    "diff_5",
    "lag_7",
    "diff_7",
    "lag_14",
    "diff_14",
    "rmean_7",
    "rstd_7",
    "rmean_14",
    "rstd_14",
    "dow",
    "month",
]

HORIZONS: Tuple[int, ...] = (1, 3, 7)


class DataValidationError(ValueError):
    """Raised when historical price data fails integrity or provenance checks."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_raw_records_from_json(raw_json_path: Path = RAW_JSON_PATH) -> pd.DataFrame:
    if not raw_json_path.exists():
        raise DataValidationError(f"Raw dataset JSON tidak ditemukan: {raw_json_path}")
    bundle = json.loads(raw_json_path.read_text(encoding="utf-8"))
    retrieved_at = bundle.get("retrieved_at_utc", "")
    endpoint = bundle.get("endpoint", "https://www.bi.go.id/hargapangan/WebSite/TabelHarga/GetGridDataDaerah")
    source_name = bundle.get("source", "PIHPS Nasional - Bank Indonesia")

    rows: List[Dict[str, Any]] = []
    for chunk in bundle.get("chunks", []):
        data_list = chunk.get("payload", {}).get("data", [])
        matches = [d for d in data_list if d.get("name") == "Cabai Rawit Merah"]
        if not matches:
            raise DataValidationError("Komoditas 'Cabai Rawit Merah' tidak ditemukan pada payload chunk.")
        item = matches[0]
        for key, val in item.items():
            if key in ("no", "name", "level"):
                continue
            rows.append(
                {
                    "raw_date": key,
                    "observation_date": datetime.datetime.strptime(key, "%d/%m/%Y").date().isoformat(),
                    "commodity": "Cabai Rawit Merah",
                    "geographic_scope": "Provinsi Jawa Timur",
                    "market_region_id": "PIHPS-PROV-16-PASAR-TRADISIONAL",
                    "raw_price": str(val),
                    "price": float(str(val).replace(",", "").strip()) if val not in (None, "-", "") else np.nan,
                    "currency": "IDR",
                    "unit": "Rp/kg",
                    "source": source_name,
                    "source_url": endpoint,
                    "retrieval_timestamp": retrieved_at,
                }
            )
    return pd.DataFrame(rows)


def validate_raw_dataframe(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    required_cols = {
        "observation_date",
        "commodity",
        "geographic_scope",
        "market_region_id",
        "price",
        "currency",
        "unit",
        "source",
        "retrieval_timestamp",
    }
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise DataValidationError(f"Kolom wajib tidak lengkap: {sorted(missing_cols)}")
    if df.empty:
        raise DataValidationError("Dataset kosong.")

    out = df.copy()
    try:
        parsed_dates = pd.to_datetime(out["observation_date"], format="%Y-%m-%d", errors="raise")
    except Exception as exc:
        raise DataValidationError(f"Format observation_date tidak valid: {exc}") from exc

    out["date"] = parsed_dates
    if out["date"].isna().any():
        raise DataValidationError("Ditemukan observation_date bernilai null/NaT.")

    # Geographic, commodity, currency, and unit consistency checks
    if out["commodity"].nunique(dropna=False) != 1 or out["commodity"].iloc[0] != "Cabai Rawit Merah":
        raise DataValidationError("Dataset mencampur komoditas atau bukan Cabai Rawit Merah.")
    if out["geographic_scope"].nunique(dropna=False) != 1:
        raise DataValidationError("Dataset mencampur cakupan geografis yang berbeda.")
    if out["market_region_id"].nunique(dropna=False) != 1:
        raise DataValidationError("Dataset mencampur market_region_id yang berbeda.")
    if set(out["currency"].dropna().unique()) != {"IDR"} or out["currency"].isna().any():
        raise DataValidationError("Mata uang tidak konsisten (wajib IDR).")
    if set(out["unit"].dropna().unique()) != {"Rp/kg"} or out["unit"].isna().any():
        raise DataValidationError("Satuan harga tidak konsisten (wajib Rp/kg).")

    # Duplicate check before sorting
    dup_count = int(out.duplicated(subset=["observation_date", "commodity", "geographic_scope"]).sum())
    if dup_count > 0:
        raise DataValidationError(f"Ditemukan {dup_count} duplikasi tanggal observasi.")

    # Price validity check
    prices = pd.to_numeric(out["price"], errors="coerce")
    if prices.isna().any() or not np.isfinite(prices.to_numpy()).all():
        raise DataValidationError("Ditemukan harga kosong/NaN/infinite pada observasi.")
    if (prices <= 0).any():
        raise DataValidationError("Ditemukan harga bernilai nol atau negatif.")
    out["price"] = prices.astype(float)

    # Chronological sorting
    out = out.sort_values("date").reset_index(drop=True)
    out["observation_date"] = out["date"].dt.strftime("%Y-%m-%d")
    out["weekday"] = out["date"].dt.day_of_week

    # Frequency and missing-date inspection
    min_date = out["date"].min()
    max_date = out["date"].max()
    all_calendar_days = pd.date_range(min_date, max_date, freq="D")
    all_business_days = pd.bdate_range(min_date, max_date, freq="B")
    observed_set = set(out["date"])
    missing_calendar = sorted(d.strftime("%Y-%m-%d") for d in (set(all_calendar_days) - observed_set))
    missing_business = sorted(d.strftime("%Y-%m-%d") for d in (set(all_business_days) - observed_set))

    # Outlier / volatility inspection (flagged, never silently deleted)
    out["daily_diff"] = out["price"].diff()
    out["daily_pct_change"] = out["price"].pct_change() * 100.0
    price_mean = float(out["price"].mean())
    price_std = float(out["price"].std(ddof=1))
    out["price_zscore"] = (out["price"] - price_mean) / (price_std if price_std > 0 else 1.0)
    out["volatility_spike_flag"] = out["daily_pct_change"].abs() > 25.0
    out["is_national_holiday_weekday"] = out["observation_date"].map(dict.fromkeys(NATIONAL_HOLIDAY_WEEKDAYS_2024_2026, True)).astype(object).where(lambda s: s.notna(), None)
    out["national_holiday_name"] = out["observation_date"].map(NATIONAL_HOLIDAY_WEEKDAYS_2024_2026)
    out["source_record_present"] = True
    out["confirmed_market_observation"] = None
    out["observation_provenance"] = "UNKNOWN_OBSERVATION_PROVENANCE"

    spikes = out.loc[out["volatility_spike_flag"], ["observation_date", "price", "daily_diff", "daily_pct_change"]].to_dict(
        orient="records"
    )

    unchanged_mask = out["daily_diff"] == 0
    holiday_mask = out["is_national_holiday_weekday"].fillna(False).astype(bool)

    sample_dates = [
        "2024-01-01",
        "2024-01-02",
        "2024-04-05",
        "2024-04-08",
        "2024-04-10",
        "2024-04-15",
        "2024-04-16",
        "2024-12-24",
        "2024-12-25",
        "2024-12-26",
        "2024-12-27",
        "2026-10-26",
        "2026-10-27",
        "2026-10-28",
    ]
    inspected_samples: List[Dict[str, Any]] = []
    for s_dt in sample_dates:
        m = out[out["observation_date"] == s_dt]
        if not m.empty:
            r = m.iloc[0]
            inspected_samples.append(
                {
                    "observation_date": s_dt,
                    "day_of_week": int(r["weekday"]),
                    "price": float(r["price"]),
                    "daily_diff": None if pd.isna(r["daily_diff"]) else float(r["daily_diff"]),
                    "source_record_present": True,
                    "is_observed": True,
                    "is_national_holiday_weekday": True if pd.notna(r["is_national_holiday_weekday"]) else None,
                    "national_holiday_name": r["national_holiday_name"] if pd.notna(r["national_holiday_name"]) else None,
                    "confirmed_market_observation": None,
                    "observation_provenance": "UNKNOWN_OBSERVATION_PROVENANCE",
                }
            )

    audit_summary: Dict[str, Any] = {
        "source_name": str(out["source"].iloc[0]),
        "source_url": str(out["source_url"].iloc[0]),
        "retrieval_timestamp": str(out["retrieval_timestamp"].iloc[0]),
        "commodity": str(out["commodity"].iloc[0]),
        "geographic_scope": str(out["geographic_scope"].iloc[0]),
        "market_region_id": str(out["market_region_id"].iloc[0]),
        "price_definition": "Harga rata-rata konsumen Pasar Tradisional Provinsi Jawa Timur (PIHPS Bank Indonesia)",
        "currency": "IDR",
        "unit": "Rp/kg",
        "start_date": min_date.strftime("%Y-%m-%d"),
        "end_date": max_date.strftime("%Y-%m-%d"),
        "observed_record_count": int(len(out)),
        "source_record_present_count": int(len(out)),
        "confirmed_market_observation_count": 0,
        "pipeline_forward_fill_count": len(missing_calendar),
        "unknown_observation_provenance_count": int(len(out)),
        "provenance_verification_status": PROVENANCE_VERIFICATION_STATUS,
        "holiday_calendar_verification_status": HOLIDAY_CALENDAR_VERIFICATION_STATUS,
        "holiday_calendar_scope": "Hanya tiga sampel audit; bukan kalender resmi lengkap. Tanggal di luar sampel memiliki status libur tidak diketahui.",
        "is_observed_field_semantics": (
            "is_observed=True berarti terdapat source record (SOURCE_RECORD_PRESENT), bukan bukti survei fisik. "
            "is_observed=False berarti tanggal tanpa source record dalam rentang data yang diisi secara kausal "
            "dari harga sumber terakhir oleh pipeline (PIPELINE_FORWARD_FILL), termasuk weekday yang hilang."
        ),
        "observation_provenance_taxonomy": {
            "SOURCE_RECORD_PRESENT": "Terdapat record harga pada payload PIHPS GetGridDataDaerah.",
            "CONFIRMED_MARKET_OBSERVATION": "Tidak diklaim (0 baris): payload publik PIHPS tidak menyertakan metadata eksekusi survei per tanggal atau jumlah responden pasar.",
            "SOURCE_PUBLISHED_CARRY_FORWARD": "Tidak disimpulkan secara otomatis hanya dari selisih harga 0 atau tanggal libur nasional karena ketiadaan flag carry-forward dari penyedia.",
            "PIPELINE_FORWARD_FILL": "Nilai dihasilkan secara kausal dari record sumber terakhir untuk tanggal tanpa record dalam rentang data.",
            "UNKNOWN_OBSERVATION_PROVENANCE": "Mekanisme survei vs carry-forward penyedia tidak dapat dibedakan dari payload.",
            "TARGET_UNAVAILABLE": "Harga target tidak tersedia; bukan hasil forward-fill dan status survei tidak diketahui.",
        },
        "calendar_day_span": int(len(all_calendar_days)),
        "business_day_span": int(len(all_business_days)),
        "weekdays_present": sorted(int(x) for x in out["weekday"].unique()),
        "missing_business_days_count": len(missing_business),
        "missing_calendar_days_count": len(missing_calendar),
        "missing_calendar_days_explanation": (
            "Tanggal kalender yang tidak memiliki record pada payload dihitung dalam rentang data; "
            "ketiadaan record tidak membuktikan status survei atau kalender libur."
        ),
        "duplicate_records_count": dup_count,
        "invalid_zero_or_negative_count": 0,
        "unchanged_consecutive_weekdays_count": int(unchanged_mask.sum()),
        "national_holiday_weekdays_count": int(holiday_mask.sum()),
        "unchanged_national_holiday_weekdays_count": int((unchanged_mask & holiday_mask).sum()),
        "unchanged_holiday_status_unknown_count": int((unchanged_mask & (~holiday_mask)).sum()),
        "changed_national_holiday_weekdays_count": int(((~unchanged_mask) & holiday_mask & out["daily_diff"].notna()).sum()),
        "inspected_date_samples": inspected_samples,
        "price_statistics": {
            "min": float(out["price"].min()),
            "max": float(out["price"].max()),
            "mean": round(price_mean, 2),
            "median": float(out["price"].median()),
            "std": round(price_std, 2),
        },
        "volatility_spikes_gt_25pct": spikes,
        "cleaning_decisions": [
            "Raw JSON dan Raw CSV disimpan utuh tanpa modifikasi nilai observasi.",
            "Harga string bertanda pemisah ribuan (contoh '70,300') dikonversi ke float Rp/kg.",
            "Perubahan harga antarrecord >25% ditandai dan dipertahankan; penyebab volatilitas maupun validitas survei tidak dapat ditentukan dari payload harga saja.",
            "Tanggal tanpa record dalam rentang data diisi menggunakan harga sumber terakhir secara kausal, tanpa backward-fill atau interpolasi masa depan.",
            "Field is_observed dipertahankan dengan semantik SOURCE_RECORD_PRESENT, bukan bukti survei fisik.",
            "Record sumber diklasifikasikan UNKNOWN_OBSERVATION_PROVENANCE (confirmed_market_observation=None); kesamaan harga atau tanggal libur bukan bukti SOURCE_PUBLISHED_CARRY_FORWARD.",
        ],
        "dataset_limitations": [
            "Payload tidak menyertakan flag pelaksanaan survei atau carry-forward penyedia; provenance observasi tetap tidak diketahui.",
            "Harga antarrecord yang tidak berubah dihitung dari data, bukan diasumsikan sebagai carry-forward penyedia.",
            "Kalender libur hanya mencakup sampel audit, bukan kalender resmi lengkap; tanggal di luar sampel tidak dapat diklasifikasikan sebagai hari biasa.",
            "Dataset bersifat univariat (harga historis) tanpa variabel eksogen cuaca/curah hujan atau volume pasokan fisik harian.",
        ],
        "forecasting_eligibility": "QUALIFIED",
    }
    return out, audit_summary


def enrich_calendar_provenance(cal: pd.DataFrame) -> pd.DataFrame:
    """
    Ensures explicit observation provenance columns exist on a calendar-day DataFrame
    without forcing confirmed_market_observation=True when survey proof is absent.
    """
    out = cal.copy()
    has_source = out["is_observed"].astype(bool)
    out["source_record_present"] = has_source
    out["source_record_status"] = np.where(has_source, "SOURCE_RECORD_PRESENT", "NO_SOURCE_RECORD")
    out["is_national_holiday_weekday"] = out["observation_date"].astype(str).map(
        dict.fromkeys(NATIONAL_HOLIDAY_WEEKDAYS_2024_2026, True)
    ).astype(object).where(lambda s: s.notna(), None)
    out["national_holiday_name"] = out["observation_date"].astype(str).map(NATIONAL_HOLIDAY_WEEKDAYS_2024_2026)
    out["confirmed_market_observation"] = pd.Series(
        [None if src else False for src in has_source],
        index=out.index,
        dtype=object,
    )
    out["observation_provenance"] = np.where(
        has_source,
        "UNKNOWN_OBSERVATION_PROVENANCE",
        "PIPELINE_FORWARD_FILL",
    )
    for h in HORIZONS:
        td_col = f"target_date_{h}"
        to_col = f"target_is_observed_{h}"
        if td_col in out.columns and to_col in out.columns:
            t_available = out[f"target_price_{h}"].notna()
            t_has_src = out[to_col].fillna(False).astype(bool) & t_available
            out[f"target_source_record_present_{h}"] = t_has_src
            out[f"target_is_national_holiday_weekday_{h}"] = out[td_col].map(
                dict.fromkeys(NATIONAL_HOLIDAY_WEEKDAYS_2024_2026, True)
            ).astype(object).where(lambda s: s.notna(), None)
            out[f"target_confirmed_market_observation_{h}"] = pd.Series(
                [False if available and not src else None for available, src in zip(t_available, t_has_src)],
                index=out.index,
                dtype=object,
            )
            out[f"target_observation_provenance_{h}"] = np.where(
                ~t_available,
                "TARGET_UNAVAILABLE",
                np.where(t_has_src, "UNKNOWN_OBSERVATION_PROVENANCE", "PIPELINE_FORWARD_FILL"),
            )
    return out


def build_calendar_and_features(validated_obs_df: pd.DataFrame) -> pd.DataFrame:
    """
    Constructs a daily calendar-day DataFrame with strictly causal features at origin date t
    and explicit calendar-day targets for horizons H+1, H+3, and H+7.
    """
    obs = validated_obs_df.sort_values("date").reset_index(drop=True)
    cal_dates = pd.date_range(obs["date"].min(), obs["date"].max(), freq="D")
    cal = pd.DataFrame({"date": cal_dates})
    cal["observation_date"] = cal["date"].dt.strftime("%Y-%m-%d")

    obs_subset = obs[
        [
            "date",
            "price",
            "commodity",
            "geographic_scope",
            "market_region_id",
            "currency",
            "unit",
            "source",
            "source_url",
            "retrieval_timestamp",
        ]
    ].rename(columns={"price": "observed_price"})

    cal = cal.merge(obs_subset, on="date", how="left")
    cal["is_observed"] = cal["observed_price"].notna()

    # Strictly causal forward fill from past observed market days only
    cal["price"] = cal["observed_price"].ffill()
    for meta_col in (
        "commodity",
        "geographic_scope",
        "market_region_id",
        "currency",
        "unit",
        "source",
        "source_url",
        "retrieval_timestamp",
    ):
        cal[meta_col] = cal[meta_col].ffill()

    # Days elapsed since last real market observation (0 on Mon-Fri, 1 on Sat, 2 on Sun)
    last_obs_date = cal["date"].where(cal["is_observed"]).ffill()
    cal["days_since_observed"] = (cal["date"] - last_obs_date).dt.days.astype(int)

    # Causal lag and difference features (using only prices at <= t)
    p = cal["price"]
    for lag in (1, 2, 3, 5, 7, 14):
        cal[f"lag_{lag}"] = p.shift(lag)
        cal[f"diff_{lag}"] = p - p.shift(lag)

    # Causal trailing rolling statistics (window [t-w+1, t], never looking past t)
    for window in (7, 14):
        cal[f"rmean_{window}"] = p.rolling(window=window, min_periods=window).mean()
        cal[f"rstd_{window}"] = p.rolling(window=window, min_periods=window).std(ddof=1)

    # Deterministic calendar features at origin t
    cal["dow"] = cal["date"].dt.day_of_week.astype(int)
    cal["month"] = cal["date"].dt.month.astype(int)

    # Explicit calendar-day horizon targets (H+1, H+3, H+7 calendar days)
    for h in HORIZONS:
        cal[f"target_date_{h}"] = cal["date"].shift(-h).dt.strftime("%Y-%m-%d")
        cal[f"target_price_{h}"] = cal["price"].shift(-h)
        cal[f"target_is_observed_{h}"] = cal["is_observed"].shift(-h).fillna(False).astype(bool)
        cal[f"target_delta_{h}"] = cal[f"target_price_{h}"] - cal["price"]

    return enrich_calendar_provenance(cal)


M7B_CANONICAL_COLUMNS: List[str] = [
    "date",
    "observation_date",
    "observed_price",
    "commodity",
    "geographic_scope",
    "market_region_id",
    "currency",
    "unit",
    "source",
    "source_url",
    "retrieval_timestamp",
    "is_observed",
    "price",
    "days_since_observed",
    *FEATURE_COLUMNS[1:],
    *(f"target_{field}_{h}" for h in HORIZONS for field in ("date", "price", "is_observed", "delta")),
]


def run_data_pipeline(
    raw_json_path: Path = RAW_JSON_PATH,
    raw_csv_path: Path = RAW_CSV_PATH,
    processed_csv_path: Path = PROCESSED_CSV_PATH,
    canonical_csv_path: Path = CANONICAL_CSV_PATH,
    audit_json_path: Path = AUDIT_JSON_PATH,
    processed_m7b1_csv_path: Path = PROCESSED_M7B1_CSV_PATH,
    audit_m7b1_json_path: Path = AUDIT_M7B1_JSON_PATH,
) -> Dict[str, Any]:
    raw_df = extract_raw_records_from_json(raw_json_path)
    if not raw_csv_path.exists():
        raw_csv_path.parent.mkdir(parents=True, exist_ok=True)
        raw_df.to_csv(raw_csv_path, index=False)

    validated_df, audit = validate_raw_dataframe(raw_df)
    cal_df = build_calendar_and_features(validated_df)

    if not processed_csv_path.exists():
        processed_csv_path.parent.mkdir(parents=True, exist_ok=True)
        cal_df[M7B_CANONICAL_COLUMNS].to_csv(processed_csv_path, index=False)
    if not canonical_csv_path.exists():
        canonical_csv_path.parent.mkdir(parents=True, exist_ok=True)
        cal_df[M7B_CANONICAL_COLUMNS].to_csv(canonical_csv_path, index=False)

    processed_m7b1_csv_path.parent.mkdir(parents=True, exist_ok=True)
    cal_df.to_csv(processed_m7b1_csv_path, index=False)

    audit["artifacts"] = {
        "raw_json_path": str(raw_json_path),
        "raw_json_sha256": sha256_file(raw_json_path),
        "raw_csv_path": str(raw_csv_path),
        "raw_csv_sha256": sha256_file(raw_csv_path),
        "processed_csv_path": str(processed_csv_path),
        "processed_csv_sha256": sha256_file(processed_csv_path),
        "canonical_csv_path": str(canonical_csv_path),
        "canonical_csv_sha256": sha256_file(canonical_csv_path),
        "processed_m7b1_csv_path": str(processed_m7b1_csv_path),
        "processed_m7b1_csv_sha256": sha256_file(processed_m7b1_csv_path),
    }
    audit["feature_columns"] = FEATURE_COLUMNS
    audit["horizons_days"] = list(HORIZONS)

    audit_m7b1_json_path.parent.mkdir(parents=True, exist_ok=True)
    audit_m7b1_json_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    if not audit_json_path.exists():
        audit_json_path.parent.mkdir(parents=True, exist_ok=True)
        audit_json_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit


if __name__ == "__main__":
    summary = run_data_pipeline()
    print(
        json.dumps(
            {
                "status": "ok",
                "observed_records": summary["observed_record_count"],
                "calendar_days": summary["calendar_day_span"],
                "date_range": f"{summary['start_date']}..{summary['end_date']}",
                "raw_sha256": summary["artifacts"]["raw_json_sha256"],
            },
            indent=2,
        )
    )
