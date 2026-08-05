#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env.sam2" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env.sam2"
  set +a
fi

if [[ -n "${SAM2_API_KEY_FILE:-}" ]]; then
  if [[ ! -r "$SAM2_API_KEY_FILE" ]]; then
    echo "SAM2_API_KEY_FILE is not readable: $SAM2_API_KEY_FILE" >&2
    exit 1
  fi
  SAM2_API_KEY="$(<"$SAM2_API_KEY_FILE")"
  export SAM2_API_KEY
fi

SAM2_PYTHON="${SAM2_PYTHON:-${ROOT_DIR}/.venv-sam2/bin/python}"
SAM2_REPO_PATH="${SAM2_REPO_PATH:-${ROOT_DIR}/sam2}"
SAM2_CHECKPOINT="${SAM2_CHECKPOINT:-${ROOT_DIR}/sam2/checkpoints/sam2.1_hiera_tiny.pt}"
SAM2_MODEL_CONFIG="${SAM2_MODEL_CONFIG:-configs/sam2.1/sam2.1_hiera_t.yaml}"
SAM2_DEVICE="${SAM2_DEVICE:-auto}"
SAM2_HOST="${SAM2_HOST:-127.0.0.1}"
SAM2_PORT="${SAM2_PORT:-8765}"

export SAM2_REPO_PATH SAM2_CHECKPOINT SAM2_MODEL_CONFIG SAM2_DEVICE

exec "$SAM2_PYTHON" -m uvicorn services.roof_sam2.server:app \
  --host "$SAM2_HOST" \
  --port "$SAM2_PORT"
