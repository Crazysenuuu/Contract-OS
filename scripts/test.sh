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
    npm run build
    echo "✅ Frontend tests passed!"
}

# ---------------------------------------------------------------------------
# E2E: one-command validation.
#
# Boots the FastAPI backend on :8000, seeds the e2e fixtures, waits for
# health, runs the Playwright suite (which starts the Next.js dev server
# itself), then stops the backend.
#
# Environment:
#   SKIP_BACKEND_BOOT=1  - assume a backend is already running on :8000
#   BACKEND_PORT         - backend port (default 8000)
# ---------------------------------------------------------------------------
BACKEND_PORT="${BACKEND_PORT:-8000}"
BACKEND_URL="http://localhost:${BACKEND_PORT}"
BACKEND_PID=""
BACKEND_LOG=""

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

cleanup() {
    if [ -n "$BACKEND_PID" ]; then
        echo ""
        echo "🧹 Stopping backend (pid $BACKEND_PID)..."
        kill "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
    fi
    # Temp logs are removed unless KEEP_BACKEND_LOG=1 or an explicit
    # BACKEND_LOG_FILE was given (CI uses that for artifact uploads).
    if [ -n "$BACKEND_LOG" ] && [ -f "$BACKEND_LOG" ] && [ "$KEEP_BACKEND_LOG" != "1" ] && [ -z "${BACKEND_LOG_FILE:-}" ]; then
        rm -f "$BACKEND_LOG"
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
    fi

    echo "🌱 Seeding e2e fixtures..."
    (
        cd "$ROOT_DIR/backend"
        "$PY" scripts/seed_e2e.py
    )

    # Frontend deps (Playwright's webServer runs `npm run dev` there).
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

    BACKEND_URL="$BACKEND_URL" npx playwright test
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
