import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from provision_private import FILES, provision


def test_private_copy_checksums_and_refusal(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    hashes = {}
    for name in FILES:
        (source / name).write_bytes(b'fixture')
        hashes[name] = hashlib.sha256(b'fixture').hexdigest()
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(hashes))
    destination = tmp_path / 'private'
    provision(source, destination, manifest)
    assert all((destination / name).read_bytes() == b'fixture' for name in FILES)
    with pytest.raises(ValueError):
        provision(source, destination, manifest)
    (source / FILES[0]).write_bytes(b'tampered')
    with pytest.raises(ValueError):
        provision(source, tmp_path / 'bad', manifest)
    assert not (tmp_path / 'bad').exists()


def test_private_runtime_paths(monkeypatch, tmp_path):
    from app.services.forecasting_service import ForecastingService
    monkeypatch.setenv('PANGANNADI_FORECAST_ROOT', str(tmp_path))
    csv, metadata, artifacts = ForecastingService._resolve_paths()
    assert csv == tmp_path / FILES[0]
    assert metadata == tmp_path / 'model_metadata_m7b1.json'
    assert artifacts == tmp_path
    monkeypatch.setenv('PANGANNADI_FORECAST_ROOT', 'relative')
    with pytest.raises(ValueError):
        ForecastingService._resolve_paths()


def test_production_cors_requires_explicit_https(tmp_path):
    for value in ('', '*', 'http://localhost:5173', 'https://', 'https://user:pass@example.test', 'https://example.test/path'):
        env = dict(os.environ, PANGANNADI_ENV='production', PANGANNADI_CORS_ORIGINS=value,
                   PANGANNADI_DATABASE_URL='sqlite:///' + (tmp_path / 'isolated.sqlite3').as_posix())
        result = subprocess.run([sys.executable, '-B', '-c', 'import app.main'], env=env, capture_output=True, text=True)
        assert result.returncode != 0
        assert 'Production requires explicit HTTPS' in result.stderr
