# PanganNadi Adaptive

Decision-support prototype for food redistribution: verified physical demand, constrained allocation, human approval, dispatch accounting, receiving reconciliation, one recovery cycle, manual checkpoints and read-only price forecasting.

## Status and architecture

**NOT PUBLICLY READY.** Actors are simulated, not authenticated. CORS is browser policy, not authorization. Do not publish the API or enable a public tunnel. Public use requires a separately reviewed authenticated HTTPS reverse proxy protecting every API route, prevention of direct backend access, authorization/CSRF review and backup/restore verification. Production authentication is outside this milestone.

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

Run from `frontend`; replace the reserved example hostname with the reviewed HTTPS API endpoint. The build and runtime reject missing/non-HTTPS/loopback production values. `VITE_*` values are public bundle contents, never secrets. Hash routing needs no SPA rewrite config. Vercel project root would be `frontend`, command `npm run build`, output `dist`; no provisioning/config boilerplate added.

Backend production configuration: `PANGANNADI_ENV=production`, explicit comma-separated HTTPS origins in `PANGANNADI_CORS_ORIGINS`, absolute persistent file `PANGANNADI_DATABASE_URL`, external `PANGANNADI_FORECAST_ROOT`, one loopback-bound worker behind the future authenticated HTTPS proxy. CORS configuration does not supply authentication. Persistent disk required; ephemeral/serverless SQLite providers rejected. No deployment has occurred.

## Verification and limitations

From `frontend`: `npm test`; set a valid HTTPS `VITE_API_BASE_URL`, then `npm run build` (includes `tsc --noEmit`). No lint script/backend typecheck configured.

From `backend`: `.\.venv\Scripts\python.exe -B -m pytest tests/test_m7d_deployment.py tests/test_optimizer.py -q -p no:cacheprovider` runs source-only deployment/solver checks. Full suite depends on privately supplied historical datasets/artifacts; it is not a clean-source-only passing claim. Existing dependency ranges are not a reproducible lock. M7D evidence captures exact local package versions; ARM64 wheel availability is not actual Linux compatibility.

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

Local download resolved CPython 3.12 Linux aarch64 wheels, including `ortools==9.15.6755`, `xgboost==3.4.1`, with hashed artifacts. No Linux/Oracle machine, native libraries, actual cloud networking, auth, HTTPS or restore test was available. Windows runtime inference and CP-SAT checks are not ARM64 deployment validation.

## Source release

ADR-024 authorizes the audited source/config/test/README snapshot for `https://github.com/hanif-12-01/Hackathon-seno-ITS.git`, branch `main`, after the final local regression gate passed: backend 248, npm package 12 plus evidence utility 1, browser 6 and TypeScript/build. This is a public code-only release, not an application deployment or complete TKT3 claim. ADR-025 retains the separate hosting/private assets/security decision; no deployment or paid resources authorized.

No project license has been selected or granted; public source visibility alone does not grant reuse rights. Private datasets/models/evidence are excluded and carry no implied redistribution permission. Installed dependencies retain their own licenses and notices; review those obligations before distributing dependency binaries or a bundled application. Python dependency ranges are not locked; npm dependencies use the published package lock. No dependency code, binaries or invented project license is included.
