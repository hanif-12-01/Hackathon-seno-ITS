import os
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from app.core.security import DemoPerimeter, OperatorPerimeter, hosted_mode, trusted_origins, validate_hosted_database

raw_env = os.environ.get('PANGANNADI_ENV', 'development')
cleaned_env = raw_env.strip().strip('\'"').lower() if raw_env else 'development'
if cleaned_env not in ('development', 'production', 'test'):
    raise ValueError('PANGANNADI_ENV must be development, test or production')
os.environ['PANGANNADI_ENV'] = cleaned_env

hosted = hosted_mode()
production = cleaned_env == 'production' or hosted
cors_origins = trusted_origins(production)
perimeter = OperatorPerimeter if hosted else DemoPerimeter
perimeter_options = {'origins': cors_origins}
if not hosted:
    perimeter_options['production'] = production
perimeter(None, **perimeter_options)
if hosted:
    validate_hosted_database()

from app.core import database
from app.core import rate_limit
from app.api.routes import router as api_router
from app.services.forecasting_service import ForecastingService

database.initialize_database()

app = FastAPI(
    title="PanganNadi Adaptive API",
    description="Sistem Pendukung Keputusan Redistribusi Pangan Adaptif Berbasis OR-Tools CP-SAT",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(perimeter, **perimeter_options)
app.include_router(api_router)


@app.get("/health")
def health_check():
    return {"status": "ok", "app": "PanganNadi Adaptive Backend", "mode": "Modular Monolith"}


@app.get("/ready")
def readiness_check():
    try:
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        ForecastingService._load_verified_bundle()
        evaluation = ForecastingService.get_evaluation_and_metadata()
        if not evaluation["test_predictions_vs_actual"]:
            raise ValueError("Evaluation unavailable")
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {"status": "ready", "public_readiness": "NOT PUBLICLY READY"}
