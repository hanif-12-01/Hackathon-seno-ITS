import os
from pathlib import Path
from starlette.applications import Starlette
from starlette.routing import Mount

os.environ['PANGANNADI_HOSTED'] = '1'

if not os.environ.get('PANGANNADI_FORECAST_ROOT'):
    bundle_candidate = (Path(__file__).parent.parent / 'private_runtime').resolve()
    if bundle_candidate.is_dir():
        os.environ['PANGANNADI_FORECAST_ROOT'] = str(bundle_candidate)

from app.main import app as backend

app = Starlette(routes=[Mount('/api', app=backend)])
