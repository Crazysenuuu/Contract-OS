#!/usr/bin/env bash
# ContractOS local dev launcher — backend + frontend tied to this terminal.
# Closing the terminal (or Ctrl+C) kills both servers; nothing is left running.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BE_PID=""
FE_PID=""

kill_descendants() {
  local pid=$1
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    kill_descendants "$child"
  done
  kill -TERM "$pid" 2>/dev/null || true
}

cleanup() {
  trap - INT TERM HUP EXIT
  echo ""
  echo "Shutting down ContractOS..."
  # Try process-group kill first (works when started in its own group),
  # then fall back to per-PID so nothing survives even in odd launches.
  kill -- -$$ 2>/dev/null || true
  [ -n "$BE_PID" ] && kill_descendants "$BE_PID"
  [ -n "$FE_PID" ] && kill_descendants "$FE_PID"
  sleep 1
  [ -n "$BE_PID" ] && kill -KILL "$BE_PID" 2>/dev/null || true
  [ -n "$FE_PID" ] && kill -KILL "$FE_PID" 2>/dev/null || true
}
trap cleanup INT TERM HUP EXIT

echo "Starting backend at http://localhost:8000"
(cd "$ROOT/backend" && exec venv/bin/python -m uvicorn app.main:app --port 8000 --reload) &
BE_PID=$!

echo "Starting frontend at http://localhost:3000"
(cd "$ROOT/frontend" && exec npm run dev) &
FE_PID=$!

while kill -0 "$BE_PID" 2>/dev/null && kill -0 "$FE_PID" 2>/dev/null; do
  sleep 1
done

cleanup