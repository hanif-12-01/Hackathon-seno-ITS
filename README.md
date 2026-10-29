# PanganNadi Adaptive

Decision-support prototype for food redistribution: verified physical demand, constrained allocation, human approval, dispatch accounting, receiving reconciliation, one recovery cycle, manual checkpoints and read-only price forecasting.

## Status and architecture

**NOT PUBLICLY READY.** M7E verifies a local protected same-origin Caddy HTTPS demo, not a hosted deployment. Production requires a shared-operator HTTP Basic perimeter on every backend route; actors remain simulated, not authenticated identities or RBAC. CORS is browser policy, not authorization. Keep backend loopback-only; no public API/tunnel. Public exposure still requires owner approval, reviewed edge rate limiting, trusted TLS/DNS/firewall, authorization review and operational restore acceptance. Shared credentials, same-origin XSS and browser credential caching remain limitations.

Architecture remains React 19 / TypeScript / Vite 7, FastAPI modular monolith, SQLAlchemy / file SQLite and OR-Tools CP-SAT. Forecasting uses saved XGBoost/Naive artifacts; it never authorizes physical allocation or automatic dispatch. No PostgreSQL migration, microservices or serverless backend conversion.

## Local setup (PowerShell)

Prerequisites: Python 3.12, Node compatible with Vite 7 (20.19+ or 22.12+), npm. Use a virtual environment; no global Python installation changes.

From `backend`:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PANGANNADI_DATABASE_URL = 'sqlite:///' + (Join-Path (Get-Location) 'local-demo-NEW.sqlite3').Replace('\','/')
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Use a new filename, never delete/reset an existing database. For an asserted simulation lifecycle instead, before starting the server:

```powershell
.\.venv\Scripts\python.exe demo.py --database local-demo-NEW.sqlite3
```

From `frontend`:

```powershell
npm ci
npm run dev
```

Development defaults to loopback API port 8000. `/health` is liveness; `/ready` checks database connectivity, saved model loading and nonempty evaluation, not security or scientific readiness. Missing private assets yield readiness 503 while operational endpoints remain available.

## Private inference assets

Source-only checkout intentionally omits datasets, weights, operational databases and private evidence. Obtain authorized assets privately from the owner; do not train merely to make startup pass, commit them, place them in frontend/public, or upload them to a static host. Set `PANGANNADI_FORECAST_ROOT` to an absolute directory outside the source tree. It must contain exactly these runtime inputs (additional unrelated files are not consumed):

- `cabai_rawit_merah_jatim_daily_processed.csv` (19-feature calendar-series input)
- `model_metadata_m7b1.json` (active governance/evaluation metadata)
- `evaluation_predictions.json` (saved evaluation series)
- `xgb_model_h1.json`, `xgb_model_h3.json`, `xgb_model_h7.json`

Raw acquisitions, legacy metadata, audit reports and evaluation CSV are not read by API inference. Without the env variable, existing local developer paths remain unchanged. Paths are resolved at request time; no copying/training happens on API calls.

Optional private offline copy, from `backend`:

```powershell
.\.venv\Scripts\python.exe provision_private.py --source <private-staging-directory> --destination <new-private-runtime-directory> --manifest <trusted-checksums.json>
```

Manifest: JSON filename-to-SHA256 map for all six files, received through a trusted independent channel. CLI validates all source bytes before creating a destination, refuses existing destinations/symlinks, copies exclusively, verifies destination checksums. No archives/network/upload/training. Incomplete destinations are retained on IO failure; investigate, choose a new destination. On Linux directory/file modes are 0700/0600; Windows requires owner-managed ACLs. Keep runtime files read-only to the service, DB writable only by its service account, backups private and restore-tested.

## Build and deployment gates

```powershell
$env:VITE_API_BASE_URL = 'https://api.example.test'
npm run build
```

Run from `frontend`; for the protected gateway set `VITE_API_BASE_URL` to the exact HTTPS frontend origin plus `/api`, not a separate API origin. Missing/non-HTTPS production values, URL credentials/query/fragment are rejected; explicit HTTPS loopback gateways are supported for local TLS verification. `VITE_*` values are public bundle contents, never secrets. Hash routing needs no SPA rewrite config.

### Protected same-origin Caddy procedure

1. Preserve an existing DB; choose a new absolute persistent SQLite filename for a new demo. Configure the backend process with `PANGANNADI_ENV=production`, `PANGANNADI_DATABASE_URL`, external `PANGANNADI_FORECAST_ROOT`, `PANGANNADI_CORS_ORIGINS` equal the exact canonical HTTPS frontend origin (no path/trailing slash), and `PANGANNADI_OPERATOR_USERNAME`/`PANGANNADI_OPERATOR_PASSWORD_HASH`. Unknown environment modes or missing/invalid production perimeter configuration fail startup. Development/test without operator configuration remains unprotected, loopback-only.
2. Generate the password hash interactively from `backend` in a private terminal using the command below. `getpass` does not echo the password; never put plaintext passwords in arguments, shell history, source, `VITE_*` or evidence. Transfer the generated hash into a private service environment file; do not publish terminal output. Hashes are sensitive offline-verification material. Use a high-entropy owner-managed password; rotate by replacing the hash and restarting.
3. Build from `frontend` with `VITE_API_BASE_URL` equal the same HTTPS origin plus `/api`. Start one backend worker with `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers`. Never bind the backend publicly; block external port 8000.
4. In the separate Caddy process environment set `PANGANNADI_PUBLIC_HOST` to the approved hostname and `PANGANNADI_FRONTEND_DIST` to the absolute built `dist` directory. Run `caddy adapt --config Caddyfile.m7e --adapter caddyfile --validate`, then `caddy run --config Caddyfile.m7e --adapter caddyfile` only in the approved environment. The template uses automatic HTTPS: real DNS, reachable 80/443 and persistent private certificate storage are prerequisites, not proved cloud configuration.
5. Navigate to the HTTPS root, complete the native Basic authentication dialog. Caddy `forward_auth /health` protects static files and API access; `/api/*` strips `/api` before loopback proxying. Backend independently checks credentials. Unchanged same-origin fetch uses browser-cached credentials. No guaranteed application logout: close the private browser session; credential rotation revokes the old password.

```powershell
.\.venv\Scripts\python.exe -B -c "from getpass import getpass; from app.core.security import password_hash; print(password_hash(getpass('Operator password: ')))"
```

Hash format: `pbkdf2_sha256$600000$32-hex-salt$64-hex-digest`. No plaintext default. Linux uses the equivalent private venv Python command. Keep authorization headers out of logs. Missing/wrong/duplicate Authorization returns 401; authenticated mutations require exactly one `Origin` matching the configured HTTPS origin, otherwise 403 (including absent/null/foreign/duplicate Origin). OPTIONS/docs/health/readiness are not authentication exemptions. Direct API clients must supply the trusted Origin for mutations; CORS does not replace this CSRF check. Protected responses use no-store. Shared operator credentials protect the demo perimeter, not actor attribution or per-user authorization.

Actual local verification used Caddy 2.11.7 with a temporary derived loopback-only configuration: admin/automatic redirects off, `tls internal`, `skip_install_trust`, private temporary certificate storage and a fresh production backend DB. Browser tests ignored only that temporary untrusted local certificate; no trust-store install or public bind. Do not copy test certificate bypasses into deployment clients. The published template was adapted; the derived local config exercised HTTPS, not public DNS/certificate issuance.

**Vercel is separate and blocked:** latest account check returned `No existing credentials found`; no login/deployment attempted. Static Vite support is not authenticated integration: unchanged cross-origin fetch does not include Basic credentials. Do not add `credentials: include`, weaken CSRF/CORS, or claim the separate frontend works with this perimeter.

### Persistent Linux candidate

`backend/requirements-linux-x86_64-py312.txt` contains the 40 exact installed runtime/test pins verified on WSL Ubuntu x86_64 CPython 3.12.3; install into a new venv, then `python -m pip check`. This is a platform-specific version snapshot, not a hash-locked supply-chain guarantee or ARM lock. Windows environment remains separate. Ubuntu without ensurepip needs an independently checksum-verified pip bootstrap; do not install into system Python.

`pangannadi-backend.service.m7e` passed `systemd-analyze verify`; it was not installed/enabled/started. Owner provisions `pangannadi` user/group, source `/opt/pangannadi/backend`, venv `/opt/pangannadi/venv`, root-owned 0600 `/etc/pangannadi/backend.env` with operator configuration, canonical CORS origin and `PANGANNADI_FORECAST_ROOT`. Use persistent external assets readable only by the service (directory 0700/files 0600); not `/tmp` or `/home` under this unit's sandbox. Unit forces production, one loopback worker without proxy-header trust, SQLite `sqlite:////var/lib/pangannadi/pangannadi.sqlite3`, StateDirectory 0700/UMask 0077, read-only filesystem except state. Persist Caddy certificate storage separately; give Caddy read access only to the frontend build, never DB/private assets. Service lifecycle, cloud firewall and public TLS remain unverified. Ephemeral/serverless SQLite storage is rejected.

### SQLite backup and disposable restore

From `backend`, using the environment's Python (PowerShell example):

```powershell
.\.venv\Scripts\python.exe -B backup_sqlite.py --source "<existing-database.sqlite3>" --destination "<new-private-backup.sqlite3>"
.\.venv\Scripts\python.exe -B backup_sqlite.py --source "<verified-private-backup.sqlite3>" --destination "<new-disposable-restored.sqlite3>"
```

Use an existing owner-only parent directory and a new filename every time. The CLI opens the source read-only, uses SQLite online backup (includes committed WAL), creates destination exclusively, refuses existing targets/sidecars and symlink/junction paths. Exit 0 requires `integrity_check=ok; foreign_key_check=clean`; failure retains incomplete destinations for investigation, never delete authoritative state. Linux creates 0600 files; Windows chmod is not ACL protection: configure owner-only directory/file ACLs. Prevent concurrent path replacement by other users. Do not raw-copy a live main DB or use immutable mode; read-only WAL access may need existing sidecars/directory permissions. No scheduling, retention, encryption or hostile-directory race guarantee.

For restore acceptance stop application writers, restore into a NEW disposable file, point a separate local process's `PANGANNADI_DATABASE_URL` there, verify rows and business API before any owner-approved cutover. Never overwrite the authoritative DB or sidecars. Tested CLI drill: demo process exit, online backup, second backup into disposable restore, new application process; exact table equivalence, unmet demand 290 and recovery still undispatched. Not hosted disaster-recovery certification.

## Verification and limitations

From `frontend`: `npm test`; set a valid HTTPS `VITE_API_BASE_URL`, then `npm run build` (includes `tsc --noEmit`). No lint script/backend typecheck configured.

From `backend`: `.\.venv\Scripts\python.exe -B -m pytest tests/test_m7e_security.py tests/test_backup_sqlite.py tests/test_m7d_deployment.py tests/test_optimizer.py -q -p no:cacheprovider` runs source-only security/backup/deployment/solver checks. Windows may skip symlink integration without OS privilege. Full suite depends on privately supplied historical datasets/artifacts; it is not a clean-source-only passing claim. Select only `tests/test_*.py`, never recursively collect archived evidence source copies. Use a fresh external pytest temporary directory, not a repository-local one. Generic dependency ranges are not a reproducible lock; Linux x86_64 pins are separate from ARM64 validation.

Latest sealed full evidence: Linux x86_64 259 passed/0 skipped (115.53s); Windows 258 passed/1 skipped (symlink privilege), including real production startup/security and backup/restore. Frontend package 12, evidence utilities 2, TypeScript passed. Separate actual protected same-origin HTTPS Chrome run: 6 passed/0 unexpected/skipped/flaky (307.674096s), all 26 perimeter probes passed. These are private-asset local verification results, not clean-checkout/cloud/ARM claims. Historical M7D backend 248 and utility 1 describe the earlier release, not current totals. No frontend business features/assertions were removed: only HTTPS loopback gateway configuration and evidence `/api` support changed. No configured lint or backend typecheck command exists.

Internal evidence/deployment runners are intentionally excluded from the public source snapshot, as are the frontend-specific README and all vault notes. Their local commands are not checkout entry points. Published tests retain their original assertions: historical forecasting/integrity tests require authorized private raw/processed datasets, audit metadata and saved artifacts in their expected repository-relative locations; the six-file runtime bundle alone does not satisfy the full historical suite. Evidence helper tests create temporary synthetic files; those fixtures do not redistribute private assets. Never run historical fixed-output evidence runners. Full regression/browser verification belongs to the parent release gate.

Forecasting limitations: historical test-influenced selection, H+7 exploratory override, no independent prospective holdout yet, unknown survey provenance and incomplete official calendar. Manual checkpoints are not GPS/arrival/delivery. LocalStorage ambiguity/cross-tab and accessibility audit limits remain. No production or complete TKT3 claim.

## Hosting assessment (official pages accessed 2026-10-29)

| Candidate | Assessment | Gate / caveat |
|---|---|---|
| Vercel static frontend | Vite supported; Hobby free for personal noncommercial use only | Team/hackathon eligibility not assumed; quota pauses possible; never host private backend assets |
| Oracle Always Free A1 | Persistent VM candidate, not provisioned | Home region, identity/card verification, capacity availability; idle reclamation; no uptime/eligibility guarantee |
| Tailscale Funnel | All plans including eligible free Personal; beta public TLS tunnel | Personal noncommercial eligibility required; internet visitors unauthenticated; NOT enabled; prefer private tailnet access |
| Ephemeral backend/serverless disk | Rejected | Cannot preserve authoritative SQLite and private runtime assets reliably |

Oracle's accessed pages currently state A1 1,500 OCPU-hours / 9,000 GB-hours monthly (2 OCPUs / 12 GB), not older 4/24 advice; verify console quotas before any later action. Idle thresholds over seven days include CPU/network <20%, A1 memory <20%. Most signups need card and phone; no charge unless upgraded per official Free Tier page, but paid/misconfigured resources must never be assumed free.

Official references:
- https://vercel.com/docs/frameworks/frontend/vite
- https://vercel.com/docs/plans/hobby
- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm
- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- https://tailscale.com/kb/1223/funnel
- https://tailscale.com/pricing
- https://pypi.org/pypi/ortools/9.15.6755/json
- https://xgboost.readthedocs.io/en/stable/install.html

Earlier download resolved CPython 3.12 Linux aarch64 wheels, including `ortools==9.15.6755`, `xgboost==3.4.1`, with hashed artifacts. M7E subsequently verified actual WSL Ubuntu Linux x86_64 native dependencies, saved inference, CP-SAT, persistent SQLite restart and backup/restore; local Windows Caddy HTTPS/auth/browser checks passed separately. **Actual Oracle A1 ARM execution is NOT verified.** No Oracle VM, cloud networking, public TLS or hosted service lifecycle has been validated. Current Oracle reference remains 2 OCPUs / 12 GB, not historical 4/24.

## Source release

ADR-024 authorizes the audited source/config/test/README snapshot for `https://github.com/hanif-12-01/Hackathon-seno-ITS.git`, branch `main`, after the final local regression gate passed: backend 248, npm package 12 plus evidence utility 1, browser 6 and TypeScript/build. This is a public code-only release, not an application deployment or complete TKT3 claim. ADR-025 retains the separate hosting/private assets/security decision; no deployment or paid resources authorized.

No project license has been selected or granted; public source visibility alone does not grant reuse rights. Private datasets/models/evidence are excluded and carry no implied redistribution permission. Installed dependencies retain their own licenses and notices; review those obligations before distributing dependency binaries or a bundled application. Generic Python dependency ranges are not locked; the M7E Linux x86_64/Python 3.12 snapshot pins versions but not artifact hashes. npm dependencies use the published package lock. No dependency code, binaries or invented project license is included.
