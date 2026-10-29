from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from backup_sqlite import backup


BACKEND = Path(__file__).resolve().parents[1]


def rows(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        return {name: db.execute('SELECT * FROM "' + name.replace('"', '""') + '" ORDER BY rowid').fetchall()
                for name, in tables}


def test_wal_snapshot_and_disposable_restore(tmp_path):
    source, destination = tmp_path / 'live #?.sqlite3', tmp_path / 'backup #?.sqlite3'
    if os.name == 'nt':
        source, destination = tmp_path / 'live #.sqlite3', tmp_path / 'backup #.sqlite3'
    with closing(sqlite3.connect(source)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.executescript('CREATE TABLE parent(id INTEGER PRIMARY KEY); CREATE TABLE child(id INTEGER PRIMARY KEY, parent_id REFERENCES parent(id));')
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        writer.execute('INSERT INTO parent VALUES(1)')
        writer.execute('INSERT INTO child VALUES(1, 1)')
        writer.commit()
        assert Path(str(source) + '-wal').stat().st_size > 0
        main_only = tmp_path / 'main-only.sqlite3'
        shutil.copyfile(source, main_only)
        assert rows(main_only)['parent'] == []
        expected = rows(source)
        writer.execute('INSERT INTO parent VALUES(2)')
        backup(source, destination)
        assert rows(destination) == expected
        writer.rollback()
        assert rows(source) == expected
    restored = tmp_path / 'disposable-restored.sqlite3'
    backup(destination, restored)
    assert rows(restored) == expected
    assert source.exists()
    if os.name != 'nt':
        assert destination.stat().st_mode & 0o777 == 0o600


def test_no_overwrite_same_path_and_sidecars(tmp_path):
    source, destination = tmp_path / 'source.sqlite3', tmp_path / 'existing.sqlite3'
    with closing(sqlite3.connect(source)) as db:
        db.execute('CREATE TABLE sample(id INTEGER PRIMARY KEY)')
    original = source.read_bytes()
    destination.write_bytes(b'keep me')
    with pytest.raises(FileExistsError):
        backup(source, destination)
    with pytest.raises(ValueError):
        backup(source, source)
    hardlink = tmp_path / 'hardlink.sqlite3'
    os.link(source, hardlink)
    with pytest.raises(FileExistsError):
        backup(source, hardlink)
    fresh = tmp_path / 'fresh.sqlite3'
    Path(str(fresh) + '-wal').write_bytes(b'keep sidecar')
    with pytest.raises(FileExistsError):
        backup(source, fresh)
    assert not fresh.exists()
    assert destination.read_bytes() == b'keep me'
    assert source.read_bytes() == original
    assert Path(str(fresh) + '-wal').read_bytes() == b'keep sidecar'


def test_missing_source_cli_creates_nothing(tmp_path):
    source, destination = tmp_path / 'missing.sqlite3', tmp_path / 'backup.sqlite3'
    result = subprocess.run([sys.executable, '-B', str(BACKEND / 'backup_sqlite.py'),
                             '--source', str(source), '--destination', str(destination)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert 'Backup failed:' in result.stderr
    assert not source.exists() and not destination.exists()


def test_reject_linked_paths(tmp_path):
    source = tmp_path / 'source.sqlite3'
    with closing(sqlite3.connect(source)) as db:
        db.execute('CREATE TABLE sample(id)')
    linked = tmp_path / 'linked.sqlite3'
    try:
        linked.symlink_to(source)
    except OSError as exc:
        pytest.skip(f'OS symlink privilege unavailable: {exc}')
    with pytest.raises(ValueError):
        backup(linked, tmp_path / 'new.sqlite3')
    with pytest.raises(ValueError):
        backup(source, linked)
    directory = tmp_path / 'linked-directory'
    directory.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        backup(source, directory / 'new.sqlite3')
    assert not (tmp_path / 'new.sqlite3').exists()


@pytest.mark.parametrize('invalid', ['corrupt', 'foreign_key'])
def test_invalid_backup_never_reported_success(tmp_path, invalid):
    source, destination = tmp_path / 'bad.sqlite3', tmp_path / 'backup.sqlite3'
    if invalid == 'corrupt':
        source.write_bytes(b'not a database')
    else:
        with closing(sqlite3.connect(source)) as db:
            db.executescript('CREATE TABLE parent(id PRIMARY KEY); CREATE TABLE child(parent_id REFERENCES parent(id)); INSERT INTO child VALUES(999);')
    before = source.read_bytes()
    with pytest.raises((sqlite3.DatabaseError, ValueError)):
        backup(source, destination)
    assert source.read_bytes() == before
    if invalid == 'foreign_key':
        assert destination.exists()


def test_demo_backup_persists_across_process_restart(tmp_path):
    source, destination, restored = [tmp_path / name for name in ('demo.sqlite3', 'backup.sqlite3', 'restored.sqlite3')]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PANGANNADI_ENV='development',
               PANGANNADI_FORECAST_ROOT=str(tmp_path / 'no-private-assets'))
    for name in ('M5A_EVIDENCE_DB', 'M5A1_EVIDENCE_DIR', 'M5B_EVIDENCE_DB', 'M6_EVIDENCE_DB', 'PANGANNADI_DATABASE_URL'):
        env.pop(name, None)
    demo = subprocess.run([sys.executable, '-B', 'demo.py', '--database', str(source)],
                          cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    print(demo.stdout, demo.stderr)
    assert demo.returncode == 0, demo.stdout + demo.stderr
    payload = json.loads(demo.stdout)
    assert payload['recovery_status'] == 'RESERVED_NOT_DELIVERED'
    expected = rows(source)
    assert expected['distribution_cases'] and expected['shipments'] and expected['reconciliations']
    for origin, target in ((source, destination), (destination, restored)):
        result = subprocess.run([sys.executable, '-B', 'backup_sqlite.py', '--source', str(origin), '--destination', str(target)],
                                cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60)
        print(result.stdout, result.stderr)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'integrity_check=ok; foreign_key_check=clean' in result.stdout
    assert rows(restored) == expected
    env['PANGANNADI_DATABASE_URL'] = 'sqlite:///' + restored.as_posix()
    code = '''from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    case = client.get('/cases/' + __import__('sys').argv[1])
    assert case.status_code == 200, case.text
    reconciliation = client.get('/cases/' + __import__('sys').argv[1] + '/reconciliation')
    assert reconciliation.status_code == 200, reconciliation.text
    assert reconciliation.json()['unmet_demand_kg'] == 290
    plan = client.get('/plans/' + __import__('sys').argv[2])
    assert plan.status_code == 200, plan.text
    assert all(s['actual_dispatched_quantity_kg'] == 0 for s in plan.json()['shipments'])
    print('Restart restored API: case/reconciliation290/recovery undispatched PASS')
'''
    restart = subprocess.run([sys.executable, '-B', '-c', code, payload['case_id'], payload['v2']['plan_id']],
                             cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    print(restart.stdout, restart.stderr)
    assert restart.returncode == 0, restart.stdout + restart.stderr
    assert rows(restored) == expected
    assert rows(source) == expected
