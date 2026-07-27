#!/usr/bin/env bash
# Agent Governance Layer — one command to bring up the control plane and console.
#
#   ./run.sh          control plane + console (dev, hot reload)
#   ./run.sh --build   build the console and serve everything from :8000
#
# Redis / PostgreSQL are optional: set REDIS_URL and DATABASE_URL to use them,
# otherwise the same interfaces run in-process on SQLite.
set -euo pipefail

cd "$(dirname "$0")"
BACKEND_PORT="${PORT:-8000}"
FRONTEND_PORT=5173
MODE="${1:-dev}"

info() { printf "\033[36m[agl]\033[0m %s\n" "$1"; }
fail() { printf "\033[31m[agl]\033[0m %s\n" "$1"; exit 1; }

command -v python3 >/dev/null || fail "python3 is required"
command -v node >/dev/null || fail "node is required"

# --- backend ---------------------------------------------------------------
if [ ! -d backend/.venv ]; then
  info "creating the backend virtualenv…"
  if command -v uv >/dev/null; then
    uv venv backend/.venv >/dev/null
    uv pip install --python backend/.venv/bin/python -r backend/requirements.txt >/dev/null
  else
    python3 -m venv backend/.venv
    backend/.venv/bin/pip install -q -r backend/requirements.txt
  fi
fi

# --- frontend --------------------------------------------------------------
if [ ! -d frontend/node_modules ]; then
  info "installing console dependencies…"
  (cd frontend && npm install --no-audit --no-fund >/dev/null)
fi

cleanup() {
  info "shutting down…"
  [ -n "${API_PID:-}" ] && kill "$API_PID" 2>/dev/null || true
  [ -n "${WEB_PID:-}" ] && kill "$WEB_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [ "$MODE" = "--build" ]; then
  info "building the console…"
  (cd frontend && npm run build >/dev/null)
  info "starting the control plane on :${BACKEND_PORT} (console served from the same origin)"
  (cd backend && exec .venv/bin/python -m uvicorn agl.main:app --host 0.0.0.0 --port "${BACKEND_PORT}") &
  API_PID=$!
  sleep 2
  info "console → http://localhost:${BACKEND_PORT}"
  wait $API_PID
else
  info "starting the control plane on :${BACKEND_PORT}…"
  (cd backend && exec .venv/bin/python -m uvicorn agl.main:app --host 0.0.0.0 --port "${BACKEND_PORT}" --reload) &
  API_PID=$!

  # Wait for the gateway to answer before bringing up the console.
  for _ in $(seq 1 40); do
    if curl -sf "http://localhost:${BACKEND_PORT}/health" >/dev/null 2>&1; then break; fi
    sleep 0.5
  done

  info "starting the operator console on :${FRONTEND_PORT}…"
  (cd frontend && exec npx vite --port "${FRONTEND_PORT}" --host) &
  WEB_PID=$!

  sleep 2
  echo
  info "console  → http://localhost:${FRONTEND_PORT}"
  info "api docs → http://localhost:${BACKEND_PORT}/docs"
  info "demo     → python demo/demo_scenario.py"
  echo
  wait $API_PID $WEB_PID
fi
