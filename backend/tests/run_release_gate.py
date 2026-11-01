"""Run unchanged regression tests with authentic, local historical fixtures.

Usage from worktree root: python -B backend/tests/run_release_gate.py --fixture-source PATH
Fixtures stay ignored; existing files must match byte-for-byte. No cloud access.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--fixture-source', type=Path, required=True)
args = parser.parse_args()
assert Path.cwd().resolve() == ROOT
source = args.fixture_source.resolve()
paths = [
    'data/raw/pihps_bi_jatim_cabai_rawit_merah_raw.json',
    'data/raw/pihps_bi_jatim_cabai_rawit_merah_raw.csv',
    'data/processed/cabai_rawit_merah_jatim_daily_processed.csv',
    'data/processed/cabai_rawit_merah_jatim_daily_processed_m7b1.csv',
    'data/processed/dataset_audit_and_provenance.json',
    'data/processed/dataset_audit_and_provenance_m7b1.json',
    'data/chili_price_daily_2024_2026.csv',
    'backend/tests/logs/m7b_experimental_evidence_20261028.json',
] + ['backend/artifacts/forecasting/' + name for name in (
    'model_metadata.json', 'model_metadata_m7b1.json',
    'evaluation_predictions.json', 'evaluation_predictions.csv',
    'xgb_model_h1.json', 'xgb_model_h3.json', 'xgb_model_h7.json')]
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
manifest = {}
for relative in paths:
    src, dst = source / relative, ROOT / relative
    if not src.is_file():
        raise SystemExit('Authentic fixture missing: ' + str(src))
    manifest[relative] = digest(src)
    if dst.exists() and digest(dst) != manifest[relative]:
        raise SystemExit('Refusing to overwrite different fixture: ' + str(dst))
for relative in paths:
    dst = ROOT / relative
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, dst)
run = ROOT / '.review' / ('release-' + uuid.uuid4().hex)
run.mkdir(parents=True)
env = {k: v for k, v in os.environ.items()
       if not k.startswith(('PANGANNADI_', 'VERCEL', 'M5A', 'M5B', 'M6_'))}
env.update(PANGANNADI_ENV='test', PANGANNADI_HOSTED='0', VERCEL='0',
           PYTHONDONTWRITEBYTECODE='1',
           PANGANNADI_DATABASE_URL='sqlite:///' + (run / 'main.sqlite3').as_posix(),
           PANGANNADI_FORECAST_ROOT=str(ROOT / 'backend/private_runtime'))
# Temp paths must actually be outside Git for the evidence/deployment contracts.
temp = Path(tempfile.gettempdir()) / ('pangannadi-release-' + uuid.uuid4().hex)
command = [sys.executable, '-B', '-m', 'pytest', 'tests', '-q',
           '-p', 'no:cacheprovider', '--basetemp', str(temp),
           '--junitxml', str(run / 'pytest.xml')]
result = subprocess.run(command, cwd=ROOT / 'backend', env=env,
                        capture_output=True, text=True, encoding='utf-8', errors='replace')
(run / 'pytest.log').write_text(result.stdout + result.stderr, encoding='utf-8')
assert all(digest(ROOT / p) == h and digest(source / p) == h for p, h in manifest.items())
summary = dict(command=command, cwd=str(ROOT / 'backend'), exit_code=result.returncode,
               fixture_source=str(source), fixture_sha256=manifest,
               fixtures_unchanged=True, database=env['PANGANNADI_DATABASE_URL'])
(run / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(str(run), flush=True)
print(result.stdout)
sys.exit(result.returncode)
