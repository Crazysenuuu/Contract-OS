#!/bin/bash

# ContractOS Test Script
# Usage: ./scripts/test.sh [backend|frontend|e2e|all]

set -e

TEST_TYPE=${1:-all}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# Resolve a Python interpreter: prefer the backend venv (it has all deps),
# then python3 (CI's setup-python), then bare python. Bare `python` does not
# exist on many systems (macOS, some CI images), so never rely on it alone.
resolve_python() {
    if [ -x "$ROOT_DIR/backend/venv/bin/python" ]; then
        echo "$ROOT_DIR/backend/venv/bin/python"
    elif command -v python3 > /dev/null 2>&1; then
        echo "python3"
    else
        echo "python"
    fi
}
PY="$(resolve_python)"

echo "🧪 Running $TEST_TYPE tests..."

run_backend_tests() {
    echo "📦 Running backend tests..."
    cd "$ROOT_DIR/backend"
    "$PY" -m pytest tests/ -v --tb=short
    echo "✅ Backend tests passed!"
}

run_frontend_tests() {
    echo "🎨 Running frontend tests..."
    cd "$ROOT_DIR/frontend"
    npm ci
    npx tsc --noEmit
    npm run lint
    npm test
    npm run build
    echo "✅ Frontend tests passed!"
}

# ---------------------------------------------------------------------------
# E2E: one-command validation.
#
# Boots the FastAPI backend on :8000 and the Next.js dev server on :3000,
# starts the fake SMS gateway, seeds the e2e fixtures, pre-warms the hot
# frontend routes, runs the Playwright suite (reusing the dev server), then
# stops everything it started.
#
# Environment:
#   SKIP_BACKEND_BOOT=1   - assume a backend is already running on :8000
#   BACKEND_PORT          - backend port (default 8000)
#   SKIP_FRONTEND_BOOT=1  - assume a dev server is already running on :3000
#   BACKEND_LOG_FILE      - path for the backend log (CI: artifact upload)
#   FRONTEND_LOG_FILE     - path for the frontend log (CI: artifact upload)
#   KEEP_BACKEND_LOG=1    - keep the temp backend/frontend logs after the run
#   E2E_WORKERS=N         - pass --workers=N to Playwright (CI defaults to 1
#                           via playwright.config.ts)
#   PLAYWRIGHT_ARGS       - extra args passed to `npx playwright test`
#                           (word-split; e.g. a spec path or --grep)
#
# CI parity notes (2026-09 e2e hardening): test.sh owns the FULL stack —
# backend, fake SMS gateway, and the Next.js dev server — in both local and
# CI runs. Every suite failure traced back to per-route cold compiles on the
# dev server behind Playwright's webServer (hydration-wait loops blowing
# their timeouts), so the hot-route warm-up below must run BEFORE Playwright
# boots — and only the process that boots the server can warm it in time.
# playwright.config.ts therefore sets reuseExistingServer: true.
# ---------------------------------------------------------------------------
BACKEND_PORT="${BACKEND_PORT:-8000}"
BACKEND_URL="http://localhost:${BACKEND_PORT}"
BACKEND_PID=""
BACKEND_LOG=""
FRONTEND_PID=""
FRONTEND_LOG=""
FAKE_SMS_PID=""

# The sms-channel specs require the fake SMS gateway on 127.0.0.1:9911
# (see e2e/tests/sms-channel.spec.ts prerequisites) and the backend must
# point at it. Started here so `test.sh e2e` remains a one-command target.
start_fake_sms_gateway() {
    if curl -sf -m 2 http://127.0.0.1:9911/health > /dev/null 2>&1; then
        echo "📡 Fake SMS gateway already running on :9911"
        return 0
    fi
    (
        cd "$ROOT_DIR/backend"
        "$PY" scripts/fake_sms_gateway.py
    ) > /tmp/contractos-fake-sms.log 2>&1 &
    FAKE_SMS_PID=$!
    for _ in $(seq 1 10); do
        if curl -sf -m 2 http://127.0.0.1:9911/health > /dev/null 2>&1; then
            echo "📡 Fake SMS gateway started (pid $FAKE_SMS_PID)"
            return 0
        fi
        sleep 0.5
    done
    echo "❌ Fake SMS gateway failed to start. Log tail:"
    tail -n 10 /tmp/contractos-fake-sms.log || true
    return 1
}

backend_healthy() {
    curl -sf -m 3 "${BACKEND_URL}/api/v1/health" > /dev/null 2>&1
}

wait_backend_healthy() {
    local attempts="${1:-60}"
    for _ in $(seq 1 "$attempts"); do
        if backend_healthy; then
            return 0
        fi
        sleep 1
    done
    return 1
}

frontend_healthy() {
    curl -sf -m 3 -o /dev/null "http://localhost:3000/login" 2>/dev/null
}

# Start the Next.js dev server owned by this script. Playwright's webServer
# reuses it (reuseExistingServer: true in playwright.config.ts) — owning it
# here is what makes CI parity possible: the hot-route warm-up below must
# run BEFORE Playwright boots, and only the process that boots the server
# can warm it in time.
start_frontend() {
    if frontend_healthy; then
        echo "🌐 Next.js dev server already running on :3000 — using it as-is."
        return 0
    fi
    if [ -n "${FRONTEND_LOG_FILE:-}" ]; then
        FRONTEND_LOG="$FRONTEND_LOG_FILE"
        mkdir -p "$(dirname "$FRONTEND_LOG")"
    else
        FRONTEND_LOG="$(mktemp -t contractos-e2e-frontend.XXXXXX)"
    fi
    (
        cd "$ROOT_DIR/frontend"
        # Same rewrite target playwright.config.ts sets for its own webServer.
        export BACKEND_INTERNAL_URL="$BACKEND_URL"
        exec npm run dev
    ) > "$FRONTEND_LOG" 2>&1 &
    FRONTEND_PID=$!

    echo "⏳ Waiting for Next.js dev server on :3000..."
    for _ in $(seq 1 120); do
        if frontend_healthy; then
            echo "✅ Next.js dev server ready."
            return 0
        fi
        sleep 1
    done
    echo "❌ Frontend failed to start. Log tail:"
    tail -n 40 "$FRONTEND_LOG" || true
    return 1
}

# Pre-warm the hot routes. Every suite failure traced back to per-route cold
# compiles on the dev server (hydration-wait loops blowing their timeouts;
# warming the dynamic detail route alone turned 3 catalog tests from failing
# to passing). A plain GET is enough — a redirect/404 still forces the route
# module to compile. Best-effort: a failure here must never fail the run
# (tests retry waits internally; this only removes the cold-start tax).
warm_frontend() {
    echo "🔥 Pre-warming Next.js routes..."
    # NOTE: bare /agreements intentionally absent — that page does not exist
    # in the frontend (only /agreements/new, /agreements/[id] and subroutes),
    # and no spec navigates to it.
    for route in /login /dashboard /agreements/new /admin/feature-flags; do
        if curl -sf -m 60 -o /dev/null "http://localhost:3000${route}" 2>/dev/null; then
            echo "  ✓ ${route}"
        else
            echo "  (skipped ${route})"
        fi
    done
    # The agreement detail route is per-uuid in Next's dev cache: warm it
    # with a REAL id (login via API, take the first agreement).
    local token aid
    token="$(curl -s -m 10 -X POST "${BACKEND_URL}/api/v1/auth/login" \
        -H 'Content-Type: application/json' \
        -d '{"email":"test@example.com","password":"password123"}' 2>/dev/null \
        | "$PY" -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)"
    if [ -n "$token" ]; then
        aid="$(curl -s -m 10 -H "Authorization: Bearer ${token}" \
            "${BACKEND_URL}/api/v1/agreements?page=1&page_size=1" 2>/dev/null \
            | "$PY" -c "import sys,json;d=json.load(sys.stdin);items=d.get('items') or d.get('agreements') or [];print(items[0]['id'] if items else '')" 2>/dev/null || true)"
        if [ -n "$aid" ]; then
            if curl -sf -m 90 -o /dev/null "http://localhost:3000/agreements/${aid}" 2>/dev/null; then
                echo "  ✓ /agreements/${aid}"
            else
                echo "  (skipped detail route)"
            fi
        else
            echo "  (no agreements in DB — detail route will compile on first test hit)"
        fi
    else
        echo "  (could not login via API — detail route will compile on first test hit)"
    fi
}

cleanup() {
    if [ -n "$FRONTEND_PID" ]; then
        echo ""
        echo "🧹 Stopping Next.js dev server (pid $FRONTEND_PID)..."
        kill "$FRONTEND_PID" 2>/dev/null || true
        # npm -> sh -c "next dev" -> node: signal forwarding is unreliable
        # across that chain, so reap the actual dev-server processes too
        # (guarded: only when this script started the server).
        pkill -P "$FRONTEND_PID" 2>/dev/null || true
        pkill -f "next dev" 2>/dev/null || true
        wait "$FRONTEND_PID" 2>/dev/null || true
    fi
    if [ -n "$FAKE_SMS_PID" ]; then
        kill "$FAKE_SMS_PID" 2>/dev/null || true
    fi
    if [ -n "$BACKEND_PID" ]; then
        echo ""
        echo "🧹 Stopping backend (pid $BACKEND_PID)..."
        kill "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
        # The 'restart' project daemonizes a REPLACEMENT backend so it outlives
        # the test that spawned it — sweep it, or it lingers on :8000 and the
        # next run reuses a stale process. Only when this script booted the
        # backend (SKIP_BACKEND_BOOT=1 means the backend belongs to the user).
        pkill -f "uvicorn app.main:app" 2>/dev/null || true
    fi
    # Temp logs are removed unless KEEP_BACKEND_LOG=1 or an explicit
    # BACKEND_LOG_FILE was given (CI uses that for artifact uploads).
    if [ -n "$BACKEND_LOG" ] && [ -f "$BACKEND_LOG" ] && [ "$KEEP_BACKEND_LOG" != "1" ] && [ -z "${BACKEND_LOG_FILE:-}" ]; then
        rm -f "$BACKEND_LOG"
    fi
    if [ -n "$FRONTEND_LOG" ] && [ -f "$FRONTEND_LOG" ] && [ "$KEEP_BACKEND_LOG" != "1" ] && [ -z "${FRONTEND_LOG_FILE:-}" ]; then
        rm -f "$FRONTEND_LOG"
    fi
    return 0
}
trap cleanup EXIT INT TERM

run_e2e_tests() {
    echo "🔍 Running E2E tests..."

    if [ "${SKIP_BACKEND_BOOT:-0}" != "1" ]; then
        if backend_healthy; then
            echo "⚠️  Backend already running on :${BACKEND_PORT} — using it as-is."
        else
            echo "🚀 Booting backend on :${BACKEND_PORT}..."
            if [ -n "${BACKEND_LOG_FILE:-}" ]; then
                BACKEND_LOG="$BACKEND_LOG_FILE"
                mkdir -p "$(dirname "$BACKEND_LOG")"
            else
                BACKEND_LOG="$(mktemp -t contractos-e2e-backend.XXXXXX)"
            fi
            (
                cd "$ROOT_DIR/backend"
                # Point the SMS channel at the fake gateway (sms-channel specs).
                export SMS_API_BASE_URL="http://127.0.0.1:9911"
                export SMS_API_KEY="test-gateway-key-123"
                exec "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT"
            ) > "$BACKEND_LOG" 2>&1 &
            BACKEND_PID=$!

            echo "⏳ Waiting for backend health..."
            if ! wait_backend_healthy 60; then
                echo "❌ Backend failed to become healthy. Log tail:"
                tail -n 40 "$BACKEND_LOG" || true
                exit 1
            fi
            echo "✅ Backend healthy."
        fi
    else
        echo "⏭️  SKIP_BACKEND_BOOT=1 — assuming backend already on :${BACKEND_PORT}."
        echo "⚠️  sms-channel specs need that backend started with SMS_API_BASE_URL=http://127.0.0.1:9911 and SMS_API_KEY=test-gateway-key-123"
    fi

    start_fake_sms_gateway || exit 1

    echo "🌱 Seeding e2e fixtures..."
    (
        cd "$ROOT_DIR/backend"
        "$PY" scripts/seed_e2e.py
    )

    # Frontend deps (the dev server started below runs `npm run dev` there).
    if [ ! -d "$ROOT_DIR/frontend/node_modules" ]; then
        echo "📦 Installing frontend dependencies..."
        (cd "$ROOT_DIR/frontend" && npm ci)
    fi

    cd "$ROOT_DIR/e2e"
    if [ ! -d node_modules ]; then
        npm ci
    fi
    if ! npx playwright --version > /dev/null 2>&1; then
        npx playwright install chromium
    fi
    # Ensure the chromium browser binary exists (fast no-op when present).
    npx playwright install chromium

    # Frontend dev server: test.sh owns it (local AND CI) so the hot routes
    # can be pre-warmed before Playwright starts — see warm_frontend above.
    if [ "${SKIP_FRONTEND_BOOT:-0}" != "1" ]; then
        start_frontend || exit 1
    else
        echo "⏭️  SKIP_FRONTEND_BOOT=1 — assuming dev server already on :3000."
    fi
    warm_frontend

    # The 'restart' project's spec kills and relaunches the live backend with
    # the environment captured from its process (ps eww in
    # e2e/scripts/restart_backend.sh) — DATABASE_URL must be part of it.
    if [ -z "${DATABASE_URL:-}" ]; then
        echo "⚠️  DATABASE_URL is not exported in this shell; the 'restart' project (flag-restart-persistence) will fail: restart_backend.sh captures the backend env from the live process and requires DATABASE_URL."
    fi

    # Default to a single worker for CI parity: all spec files share one dev
    # server and one seeded database, and parallel files are the fastest way
    # to flake this suite. Override with E2E_WORKERS=N.
    E2E_WORKERS="${E2E_WORKERS:-1}"
    WORKERS_ARGS=(--workers="$E2E_WORKERS")
    # PLAYWRIGHT_ARGS is intentionally unquoted (word-split args, e.g. specs).
    BACKEND_URL="$BACKEND_URL" npx playwright test "${WORKERS_ARGS[@]}" ${PLAYWRIGHT_ARGS:-}
    echo "✅ E2E tests passed!"
}

case $TEST_TYPE in
    backend)
        run_backend_tests
        ;;
    frontend)
        run_frontend_tests
        ;;
    e2e)
        run_e2e_tests
        ;;
    all)
        run_backend_tests
        run_frontend_tests
        # E2E boots its own backend, so it now runs by default.
        if [ "$SKIP_E2E" != "1" ]; then
            run_e2e_tests
        fi
        ;;
    *)
        echo "❌ Invalid test type: $TEST_TYPE"
        echo "Usage: ./scripts/test.sh [backend|frontend|e2e|all]"
        exit 1
        ;;
esac

echo "🎉 All tests completed!"
