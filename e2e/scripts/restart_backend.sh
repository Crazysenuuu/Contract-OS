#!/usr/bin/env bash
# Restart the e2e backend (uvicorn app.main:app) with the exact environment of
# the currently running process, then wait for /health to return 200.
#
# Used by the flag-restart-persistence e2e spec: the restart must target the
# same DATABASE_URL / ENVIRONMENT / SMS settings the suite is exercising, so
# the environment is captured from the live process (ps eww) rather than
# assumed from .env files (the e2e backend intentionally runs with a different
# DATABASE_URL than backend/.env).
#
# Usage: restart_backend.sh [port] [logfile]
set -euo pipefail

PORT="${1:-8000}"
LOG="${2:-/tmp/backend_e2e_restart.log}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BACKEND_DIR="$ROOT/backend"
PYTHON="$BACKEND_DIR/venv/bin/python"
PATTERN="uvicorn app.main:app"

PID="$(pgrep -f "$PATTERN" | head -1 || true)"
if [ -z "$PID" ]; then
  echo "restart_backend: no running backend matching '$PATTERN'" >&2
  exit 1
fi

# Capture app-relevant env from the live process. ps eww works on macOS and
# Linux for own processes; values containing spaces cannot round-trip through
# ps output, but no app setting used by this backend contains spaces.
ENV_PAIRS="$(ps eww "$PID" | tr ' ' '\n' \
  | grep -E '^[A-Z_][A-Z0-9_]*=' \
  | grep -vE '^(PATH|HOME|SHELL|USER|LOGNAME|TMPDIR|PWD|OLDPWD|SHLVL|TERM|LANG|LC_|SSH|XPC|__CF|SECURITY|DISPLAY|ITERM|COLORFGBG|COMMAND_MODE|MANPAGER|PAGER|EDITOR|VISUAL|HISTFILE|_)=' || true)"
if ! grep -q '^DATABASE_URL=' <<<"$ENV_PAIRS"; then
  echo "restart_backend: could not capture DATABASE_URL from pid $PID" >&2
  exit 1
fi

kill "$PID"
for _ in $(seq 1 20); do
  kill -0 "$PID" 2>/dev/null || break
  sleep 0.5
done
kill -9 "$PID" 2>/dev/null || true

# Export captured env, then daemonize so the new backend outlives this script.
export $ENV_PAIRS
cd "$BACKEND_DIR"
"$PYTHON" scripts/daemonize.py "$LOG" "$PYTHON" -m uvicorn app.main:app --port "$PORT"

# Wait for health.
for _ in $(seq 1 60); do
  if curl -sf -m 2 "http://localhost:$PORT/health" >/dev/null 2>&1; then
    NEW_PID="$(pgrep -f "$PATTERN" | head -1)"
    echo "restart_backend: healthy (pid $NEW_PID)"
    exit 0
  fi
  sleep 0.5
done
echo "restart_backend: backend did not become healthy" >&2
exit 1
