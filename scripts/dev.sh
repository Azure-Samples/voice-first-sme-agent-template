#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Backend setup
cd "$ROOT_DIR/backend"
if [ ! -d .venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt

# Frontend setup
cd "$ROOT_DIR/frontend"
npm install --silent

# Run both
echo "Starting backend (port 8000) and frontend (port 5173)..."
cd "$ROOT_DIR/backend"
.venv/bin/uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!

cd "$ROOT_DIR/frontend"
npm run dev &
FRONTEND_PID=$!

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
