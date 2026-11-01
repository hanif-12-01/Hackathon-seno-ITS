"""Private CLI Preview staging. Never uploads, deploys, installs, or reads secrets.

python -B tools/package_preview.py --destination <NEW-EXTERNAL-DIRECTORY>
python -B tools/package_preview.py --verify <DIRECTORY> --dry-run
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROJECT = {'projectId': 'prj_QkwGa3GpjifKerW5yWtzrghEM8CZ',
           'orgId': 'team_znKVthxU4i1W00hhlJuWuRVy', 'projectName': 'pangannadi-adaptive'}
ASSETS = {
    'cabai_rawit_merah_jatim_daily_processed.csv': '151ac36c1e05791598bc93388b8ac21ee33faf5bb15c66620f2ca66b70d31ba8',
    'model_metadata_m7b1.json': '0c2d8d41dc1d2fdb1f2421c13f10744569303060c1499b7652b151accd85fe21',
    'evaluation_predictions.json': 'f90fa52e0cdfbda76bb579db0cafc1d99e33df7ceeada425dc703a6576e02c3a',
    'xgb_model_h1.json': '9bef42d2d6c54d3471b432fe62ac6491437b91d9bf53005967d6a63088704cbb',
    'xgb_model_h3.json': '51ced7fc2c3ecba52aef6ab292e0c5b6527db5438e211dd2b447f7c82341904a',
    'xgb_model_h7.json': 'a5525e0fefd12123f1d2bef226273cb22314fd3270cbd26e7821fccde633b6f2',
}
SOURCE = tuple('''vercel.json
backend/pyproject.toml
backend/requirements.txt
backend/requirements-vercel.txt
frontend/index.html
frontend/package.json
frontend/package-lock.json
frontend/tsconfig.json
frontend/vite.config.ts
backend/app/__init__.py
backend/app/main.py
backend/app/vercel.py
backend/app/api/__init__.py
backend/app/api/routes.py
backend/app/core/__init__.py
backend/app/core/ca-bundle.pem
backend/app/core/config.py
backend/app/core/database.py
backend/app/core/rate_limit.py
backend/app/core/security.py
backend/app/models/__init__.py
backend/app/models/db_models.py
backend/app/models/schemas.py
backend/app/services/__init__.py
backend/app/services/approval_service.py
backend/app/services/audit_service.py
backend/app/services/capacity.py
backend/app/services/case_service.py
backend/app/services/forecasting_service.py
backend/app/services/gate.py
backend/app/services/optimizer.py
backend/app/services/price_data_pipeline.py
backend/app/services/receiving_service.py
backend/app/services/replanning_service.py
backend/app/services/shipment_service.py
frontend/src/App.tsx
frontend/src/CaseForm.tsx
frontend/src/Charts.tsx
frontend/src/Checkpoints.tsx
frontend/src/DriverView.tsx
frontend/src/GovDashboard.tsx
frontend/src/LandingPage.tsx
frontend/src/Logo.tsx
frontend/src/Overview.tsx
frontend/src/Reports.tsx
frontend/src/Toast.tsx
frontend/src/components.tsx
frontend/src/main.tsx
frontend/src/types.ts
frontend/src/style.css
frontend/src/auth.css
frontend/src/charts.css
frontend/src/landing.css
frontend/src/portals.css
frontend/src/reports.css
frontend/src/lib/api.ts
frontend/src/lib/config.ts
frontend/src/lib/dashboard.ts
frontend/src/lib/operations.ts
frontend/public/brand/app-icon-192.png
frontend/public/brand/app-icon-512.png
frontend/public/brand/favicon-32.png
frontend/public/brand/pangannadi-logo-192.webp
frontend/public/brand/pangannadi-logo-96.webp
frontend/public/brand/pangannadi-logo.png
frontend/public/brand/pangannadi-ricefields-hero.webp'''.splitlines())


def regular(path):
    """Reject every linked/reparse component before resolve/read, including junctions."""
    path = Path(os.path.abspath(path))
    for component in (path, *path.parents):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked/reparse path refused')
    return path


def payloads(root=ROOT):
    root = regular(root)
    for name in SOURCE:
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name:
            raise ValueError('Unsafe allowlist path')
    data = {name: regular(root / name).read_bytes() for name in SOURCE if (root / name).is_file()}
    private = root / 'backend/private_runtime'
    if not private.is_dir() or {p.name for p in regular(private).iterdir()} != set(ASSETS):
        raise ValueError('Private input directory must contain exactly six authorized assets')
    for name, expected in ASSETS.items():
        content = regular(private / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError('Private asset hash mismatch: ' + name)
        data['backend/private_runtime/' + name] = content
    for name, content in data.items():
        if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|'
                     rb'(?:ghp_|github_pat_|AKIA|sk-proj-)[A-Za-z0-9_\-]{16,}|'
                     rb'postgres(?:ql)?(?:\+psycopg)?://[^\s"\x27]+:[^\s"\x27]+@', content):
            raise ValueError('Sensitive content pattern refused: ' + name)
    return data


def ignore_text(names):
    rules = {'*'}
    for name in names:
        path = Path(name)
        for parent in reversed(path.parents):
            if parent.as_posix() != '.':
                rules.add('!/' + parent.as_posix())
        rules.add('!/' + name)
    # Parents must precede children; no wildcard file allowlist.
    return '*\n' + '\n'.join(sorted(rules - {'*'}, key=lambda s: (s.count('/'), s))) + '\n'


def package_data(root=ROOT):
    data = payloads(root)
    data['.vercelignore'] = ignore_text(data).encode()
    data['.vercel/project.json'] = (json.dumps(PROJECT, sort_keys=True) + '\n').encode()
    return data


def secure_directory(path):
    path.mkdir(mode=0o700)
    if os.name == 'nt':
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                        os.environ['USERNAME'] + ':(OI)(CI)F', 'SYSTEM:(OI)(CI)F'],
                       check=True, capture_output=True)


def stage(destination, root=ROOT):
    destination = Path(os.path.abspath(destination))
    regular(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Destination must be new; nothing overwritten or deleted')
    if destination.is_relative_to(root.resolve()) or root.resolve().is_relative_to(destination):
        raise ValueError('Staging must be outside source repository and its ancestors')
    # Refuse staging inside any existing Git worktree (including another repository).
    probe = subprocess.run(['git', '-C', str(destination.parent), 'rev-parse', '--show-toplevel'],
                           capture_output=True)
    if probe.returncode == 0:
        raise ValueError('Staging parent is inside a Git repository')
    data = package_data(root)
    secure_directory(destination)
    for name, content in data.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(content)
        if os.name != 'nt':
            target.chmod(0o600)
    return verify(destination, root)


def verify(destination, root=ROOT, dry_run=False):
    destination = regular(destination)
    expected = package_data(root)
    expected_dirs = {parent.as_posix() for name in expected
                     for parent in Path(name).parents if parent.as_posix() != '.'}
    actual = set()
    for path in destination.rglob('*'):
        regular(path)
        if path.is_file():
            actual.add(path.relative_to(destination).as_posix())
        elif path.relative_to(destination).as_posix() not in expected_dirs:
            raise ValueError('Unexpected staging directory')
    if actual != set(expected):
        raise ValueError('Staging file set differs from exact allowlist')
    for name, content in expected.items():
        if (destination / name).read_bytes() != content:
            raise ValueError('Staging content changed: ' + name)
    uploaded = set(expected) - {'.vercel/project.json', '.vercelignore'}
    report = {'result': 'PASS', 'stage': str(destination), 'project': PROJECT['projectId'],
              'source_files': len([n for n in SOURCE if (root / n).is_file()]), 'private_assets': len(ASSETS),
              'expected_upload_files': len(uploaded),
              'expected_upload_bytes': sum(len(expected[n]) for n in uploaded),
              'manifest_sha256': hashlib.sha256(json.dumps({n: hashlib.sha256(v).hexdigest()
                  for n, v in sorted(expected.items())}, sort_keys=True).encode()).hexdigest(),
              'function_bundle': 'NOT_VERIFIED', 'hosted': 'NOT_VERIFIED'}
    if dry_run:
        command = ['vercel.cmd' if os.name == 'nt' else 'vercel', 'deploy', '--dry',
                   '--format=json', '--target=preview', '--non-interactive', '--cwd', str(destination)]
        # Positive exclusion probes use dummy bytes, never secrets. Remove only files created here.
        probes = ['.env', 'operator-backup.sqlite3', 'backend/app/upload-probe.db',
                  'frontend/src/.env', 'frontend/public/brand/private-model-probe.json',
                  'backend/private_runtime/unexpected-probe.json']
        created = []
        try:
            for name in probes:
                path = destination / name
                with path.open('xb') as stream:
                    stream.write(b'PACKAGING_TEST_ONLY_NOT_A_SECRET')
                created.append(path)
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        finally:
            for path in created:
                path.unlink()
        if result.returncode:
            raise ValueError('Vercel dry-run failed; raw output withheld')
        manifest = json.loads(result.stdout)
        paths = {row['path'].replace('\\', '/'): row['size'] for row in manifest['files']}
        if set(paths) != uploaded or any(paths[n] != len(expected[n]) for n in uploaded):
            raise ValueError('Actual CLI upload differs from exact path/size allowlist')
        if manifest['fileCount'] != len(uploaded):
            raise ValueError('CLI file count mismatch')
        if manifest['totalSize'] != report['expected_upload_bytes']:
            raise ValueError('CLI total byte size mismatch')
        # Recheck stage after CLI; it may not add secret/environment/build files.
        verify(destination, root)
        report.update(dry_run='PASS', framework=manifest['framework'],
                      forbidden_exclusion_probes=len(probes),
                      actual_upload_files=manifest['fileCount'],
                      actual_upload_bytes=manifest['totalSize'], ignored_count=manifest['ignoredCount'],
                      ignored=manifest['ignored'])
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--destination', type=Path)
    action.add_argument('--verify', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        if args.destination:
            if args.dry_run:
                parser.error('Create first, then verify --dry-run explicitly')
            report = stage(args.destination)
        else:
            report = verify(args.verify, dry_run=args.dry_run)
        print(json.dumps(report, indent=2))
    except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(json.dumps({'result': 'FAIL', 'reason': str(exc) if isinstance(exc, ValueError)
                          else type(exc).__name__}), file=sys.stderr)
        sys.exit(1)
