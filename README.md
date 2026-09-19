# ContractOS

AI-powered agreement lifecycle platform: generate, negotiate, approve, sign, and monitor agreements with a 69-type catalog (12 MVP + 57 extended), per-jurisdiction templates, SLA monitoring, and a multi-language translation pipeline.

**Stack**: FastAPI (Python 3.14) · Next.js 16 · Flutter · PostgreSQL · Redis · Celery

## Repository layout

| Directory | What it is |
|---|---|
| `backend/` | FastAPI API, SQLAlchemy 2.0 async models, Celery tasks, Jinja2 templates, Alembic migrations |
| `frontend/` | Next.js App Router UI (creation wizard, dashboards, signing, admin) |
| `mobile/` | Flutter app covering the mobile feature set |
| `e2e/` | Playwright suite — 36 tests including per-agreement-type wizard coverage |
| `scripts/` | `setup.sh`, `dev-local.sh`, `test.sh` (one-command test runner), `deploy.sh` |
| `.github/workflows/` | CI: migrations, backend, frontend, E2E (with Postgres service container), security scan, Docker builds |
| `monitoring/`, `k8s/` | Prometheus / Grafana / Alertmanager config; canary deployment manifests |

## Quick start

### Prerequisites

- Python 3.14, Node.js 18+, Flutter (for mobile), Docker (optional)
- A PostgreSQL instance (local or via `docker-compose`)

### Backend

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # then edit DATABASE_URL / JWT_SECRET_KEY
alembic upgrade head               # create schema
python seed_data.py                # seed jurisdictions, clauses, agreement catalog
uvicorn app.main:app --reload --port 8000
```

API docs: <http://localhost:8000/docs> · Health: <http://localhost:8000/api/v1/health>

### Frontend

```bash
cd frontend
npm ci
npm run dev                        # http://localhost:3000 (proxies /api/v1 → :8000)
```

### Everything at once (Docker)

```bash
docker-compose up -d               # postgres, redis, backend, frontend, celery, monitoring
```

## Testing

`make test` runs all three suites via `scripts/test.sh`:

```bash
make test              # backend → frontend (tsc + lint + build) → e2e
make test-backend      # pytest
make test-frontend     # npm ci + tsc --noEmit + eslint + vitest + next build
make test-e2e          # boots backend on :8000, seeds e2e fixtures, runs Playwright
```

Useful E2E environment flags (see `scripts/test.sh`):

| Flag | Effect |
|---|---|
| `SKIP_BACKEND_BOOT=1` | Assume a backend is already running on `:8000` |
| `BACKEND_LOG_FILE=path` | Write backend log to `path` (CI uses this for artifacts) |
| `KEEP_BACKEND_LOG=1` | Keep the temp backend log after the run |

E2E fixtures come from `backend/scripts/seed_e2e.py` (test user `test@example.com` / `password123`, idempotent, full agreement catalog; refuses to run when ENVIRONMENT=production).

## Common Make targets

| Target | Purpose |
|---|---|
| `make dev-backend` / `make dev-frontend` | Run servers with reload |
| `make migrate` | `alembic upgrade head` |
| `make seed` | Seed base data + agreement catalog |
| `make admin EMAIL=... NAME="..."` | Create or promote a platform admin |
| `make lint` | Frontend ESLint + backend flake8 |
| `make build` | Build backend & frontend Docker images |

## CI

`.github/workflows/ci.yml` runs on pushes to `main`/`develop` and PRs to `main`:

1. **Migration tests** — chain integrity, upgrade/downgrade, `alembic check` drift detection
2. **Backend tests** — pytest with coverage against a Postgres 15 service
3. **Frontend tests** — typecheck, lint, production build
4. **E2E** — Postgres 16 service container + `bash scripts/test.sh e2e` (backend boot → seed → Playwright → cleanup), uploads backend log / Playwright report / failure artifacts
5. **Security scan** — `safety` + `bandit`
6. **Docker builds** (main) and staging/production deploy stubs

## Key backend concepts

- **Agreement catalog** (`seed_data.py`): 69 types (12 MVP types with dedicated templates + 57 extended types across corporate/governance, commercial, services, procurement, dispute, employment, technology, financial, real-estate, marketing and supply-chain) — each with its own questionnaire schema; extended types render through per-kind domain templates (Board Resolution has a dedicated one).
- **Canonical state machine** (`app/domain/agreement_states.py`): the single definition of agreement statuses and legal transitions (spec §66/§67); seeds, the lifecycle service and route guards all derive from it. EXECUTED is reached only once *all* required signers have signed (`services/signing_completion.py`).
- **Answer provenance** (`app/domain/answer_provenance.py`): every questionnaire answer is tagged USER_PROVIDED / EXTRACTED / INFERRED / SYSTEM_DEFAULT / LEGAL_REQUIREMENT / REVIEW_REQUIRED (spec §70); missing required answers block sending instead of being defaulted.
- **RBAC** (`app/dependencies/rbac.py`): `require_permission("…")` guards mutating routes; the permission catalogue lives in `seed_data.PERMISSION_CATALOG`; `owner`/`admin` roles hold everything.
- **Configuration over hardcoding** (spec §71): base currency and FX table come from `DEFAULT_CURRENCY` / `FX_RATES_JSON` (`services/currency_service.py`); jurisdiction defaults come from the organisation's `Jurisdiction` record.
- **NL creation**: describe an agreement in plain language; the service resolves type (incl. NDA mutual/unilateral direction detection) and prefills answers.
- **SLA monitoring**: uptime / response-time metrics are extracted into obligations; an hourly sweep materialises review deadlines and flags breaches.
- **Audit chain**: hash-linked audit trail with evidence attachments and export.
- **Async everywhere**: all DB access is SQLAlchemy 2.0 async; background work runs through Celery (outbox delivery, reminders, SLA sweeps, email).

## License / status

Private project — all rights reserved. Work in progress; see the repository issues for the current roadmap.
# Contract-OS
