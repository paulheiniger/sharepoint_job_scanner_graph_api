#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

DASHBOARD_PYTHON="${DASHBOARD_PYTHON:-${ROOT_DIR}/.venv/bin/python}"
DASHBOARD_HOST="${DASHBOARD_HOST:-127.0.0.1}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8501}"

if [[ ! -x "$DASHBOARD_PYTHON" ]]; then
  echo "Dashboard Python is not executable: $DASHBOARD_PYTHON" >&2
  exit 1
fi

exec "$DASHBOARD_PYTHON" -m streamlit run dashboard/app.py \
  --server.address "$DASHBOARD_HOST" \
  --server.port "$DASHBOARD_PORT" \
  --server.headless true \
  --browser.gatherUsageStats false
