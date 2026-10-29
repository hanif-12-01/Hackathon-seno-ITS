import os
from urllib.parse import urlsplit
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from app.services.forecasting_service import ForecastingService
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import engine, Base, upgrade_integrity_schema, upgrade_dispatch_schema, upgrade_receiving_schema, upgrade_replanning_schema, upgrade_checkpoint_schema
from app.api.routes import router as api_router

# Inisialisasi skema database SQLite
upgrade_integrity_schema()
upgrade_dispatch_schema()
upgrade_receiving_schema()
upgrade_replanning_schema()
upgrade_checkpoint_schema()
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="PanganNadi Adaptive API",
    description="Sistem Pendukung Keputusan Redistribusi Pangan Adaptif Berbasis OR-Tools CP-SAT",
    version="1.0.0",
)

production = os.environ.get("PANGANNADI_ENV") == "production"
cors_value = os.environ.get("PANGANNADI_CORS_ORIGINS", "" if production else "http://localhost:5173,http://127.0.0.1:5173")
cors_origins = [origin.strip() for origin in cors_value.split(",") if origin.strip()]
if production and (not cors_origins or any(
    urlsplit(origin).scheme != "https" or not urlsplit(origin).hostname
    or urlsplit(origin).username or urlsplit(origin).password
    or urlsplit(origin).path or urlsplit(origin).query or urlsplit(origin).fragment
    or "*" in origin for origin in cors_origins
)):
    raise ValueError("Production requires explicit HTTPS PANGANNADI_CORS_ORIGINS; CORS is not authentication")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health")
def health_check():
    return {"status": "ok", "app": "PanganNadi Adaptive Backend", "mode": "Modular Monolith"}


@app.get("/ready")
def readiness_check():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        ForecastingService._load_verified_bundle()
        evaluation = ForecastingService.get_evaluation_and_metadata()
        if not evaluation["test_predictions_vs_actual"]:
            raise ValueError("Evaluation unavailable")
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {"status": "ready", "public_readiness": "NOT PUBLICLY READY"}
