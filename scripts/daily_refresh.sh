#!/usr/bin/env bash
set -Eeuo pipefail

# Daily Spray-Tec data refresh.
#
# This script intentionally uses the delta-driven incremental workflow for the
# normal scheduled path. Full rebuilds, identifier repair, and unrestricted
# document backfills remain explicit administrative commands.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

DATABASE_URL_EFFECTIVE="${DATABASE_URL:-${NEON_DATABASE_URL:-${NEON_PSQL_URL:-}}}"
if [[ -z "${DATABASE_URL_EFFECTIVE}" ]]; then
  echo "ERROR: Set DATABASE_URL, NEON_DATABASE_URL, or NEON_PSQL_URL." >&2
  exit 2
fi

SITE_URL="${SHAREPOINT_SITE_URL:-https://aro365531128.sharepoint.com/sites/Data}"
LIBRARY="${SHAREPOINT_LIBRARY:-Documents}"
SCAN_CONFIG="${SCAN_CONFIG:-config/sharepoint_scan_roots.yaml}"
OUTPUT_DIR="${OUTPUT_DIR:-output}"
CACHE_ROOT="${CACHE_ROOT:-.cache/sharepoint}"
TIMESHEET_CACHE_ROOT="${TIMESHEET_CACHE_ROOT:-.cache/office_timesheets/Data/Timesheets}"

LOG_DIR="${LOG_DIR:-output/refresh_logs}"
RUN_ID="${RUN_ID:-daily-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_FILE="${LOG_FILE:-${LOG_DIR}/${RUN_ID}.log}"
LOCK_DIR="${LOCK_DIR:-.cache/daily_refresh.lock}"

DOCUMENT_EXTRACT_LIMIT="${DOCUMENT_EXTRACT_LIMIT:-0}"
DOCUMENT_PROGRESS_EVERY="${DOCUMENT_PROGRESS_EVERY:-100}"
MAX_DOCUMENT_FAILURES="${MAX_DOCUMENT_FAILURES:-200}"

RUN_DOCUMENT_EXTRACTION="${RUN_DOCUMENT_EXTRACTION:-1}"
RUN_SQL_REFRESHES="${RUN_SQL_REFRESHES:-1}"
RUN_SHAREPOINT_JOB_INDEX_SYNC="${RUN_SHAREPOINT_JOB_INDEX_SYNC:-1}"
RUN_DOCUMENT_STATUS="${RUN_DOCUMENT_STATUS:-1}"
BACKFILL_DOCUMENT_METADATA="${BACKFILL_DOCUMENT_METADATA:-0}"

mkdir -p "$LOG_DIR" "$(dirname "$LOCK_DIR")"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "ERROR: another daily refresh appears to be running: ${LOCK_DIR}" >&2
  exit 75
fi
cleanup() {
  rm -rf "$LOCK_DIR"
}
trap cleanup EXIT

exec > >(tee -a "$LOG_FILE") 2>&1

run_step() {
  local label="$1"
  shift
  echo
  echo "===== ${label} ====="
  echo "Started: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  "$@"
  echo "Finished: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
}

run_sql_file() {
  local file="$1"
  if [[ ! -f "$file" ]]; then
    echo "Skipping missing SQL file: ${file}"
    return 0
  fi
  if ! command -v psql >/dev/null 2>&1; then
    echo "ERROR: psql is required for SQL refreshes. Set RUN_SQL_REFRESHES=0 to skip." >&2
    return 127
  fi
  psql "$DATABASE_URL_EFFECTIVE" -v ON_ERROR_STOP=1 -f "$file"
}

json_has_rows() {
  local path="$1"
  python3 - "$path" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(1)
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if isinstance(payload, list) and len(payload) > 0 else 1)
PY
}

echo "Spray-Tec daily refresh"
echo "Run ID: ${RUN_ID}"
echo "Root: ${ROOT_DIR}"
echo "Log: ${LOG_FILE}"
echo "Config: ${SCAN_CONFIG}"
echo "Output dir: ${OUTPUT_DIR}"
echo "Document extraction limit: ${DOCUMENT_EXTRACT_LIMIT}"

run_step "Incremental SharePoint delta scan and changed-only DB load" \
  python3 -m jobscan.incremental_scan \
    --delta \
    --site-url "$SITE_URL" \
    --library "$LIBRARY" \
    --config "$SCAN_CONFIG" \
    --output-dir "$OUTPUT_DIR" \
    --cache-root "$CACHE_ROOT" \
    --timesheet-cache-root "$TIMESHEET_CACHE_ROOT" \
    --database-url "$DATABASE_URL_EFFECTIVE" \
    --run-id "$RUN_ID"

if [[ "$BACKFILL_DOCUMENT_METADATA" == "1" ]]; then
  run_step "Backfill document metadata from cached SharePoint manifests" \
    python3 -m jobscan.document_extraction \
      --backfill-metadata \
      --cache-root "$CACHE_ROOT" \
      --limit 0 \
      --progress-every "$DOCUMENT_PROGRESS_EVERY" \
      --database-url "$DATABASE_URL_EFFECTIVE"
fi

if [[ "$RUN_DOCUMENT_EXTRACTION" == "1" ]]; then
  run_step "Extract pending estimator-relevant document content" \
    python3 -m jobscan.document_extraction \
      --pending \
      --estimator-relevant \
      --limit "$DOCUMENT_EXTRACT_LIMIT" \
      --cache-root "$CACHE_ROOT" \
      --progress-every "$DOCUMENT_PROGRESS_EVERY" \
      --max-document-failures "$MAX_DOCUMENT_FAILURES" \
      --database-url "$DATABASE_URL_EFFECTIVE"
fi

if [[ "$RUN_SQL_REFRESHES" == "1" ]]; then
  run_step "Ensure job tracking material fields" run_sql_file "db/add_job_tracking_foam_fields.sql"
  run_step "Ensure daily production tables" run_sql_file "db/add_daily_production_entries.sql"
  run_step "Refresh dashboard views" run_sql_file "db/dashboard_views.sql"
  run_step "Refresh job document signals" run_sql_file "db/refresh_job_document_signals.sql"
  run_step "Refresh job board static snapshot" run_sql_file "db/refresh_job_board_static_snapshot.sql"
  run_step "Refresh Power BI marts" run_sql_file "db/powerbi_marts.sql"
fi

if [[ "$RUN_SHAREPOINT_JOB_INDEX_SYNC" == "1" ]]; then
  CHANGED_JOBS_PATH="${OUTPUT_DIR}/changed_jobs.json"
  if json_has_rows "$CHANGED_JOBS_PATH"; then
    run_step "Sync changed jobs to SharePoint Job Index list" \
      python3 -m jobscan.sharepoint_list_sync \
        --input "$CHANGED_JOBS_PATH" \
        --site-url "$SITE_URL" \
        --list-name "${SHAREPOINT_JOB_INDEX_LIST_NAME:-Job Index}" \
        --continue-on-error \
        --url-fields-as-text \
        --diagnose-field-errors \
        --omit-rejected-fields
  else
    echo
    echo "===== Sync changed jobs to SharePoint Job Index list ====="
    echo "No changed job rows found at ${CHANGED_JOBS_PATH}; skipping SharePoint list sync."
  fi
fi

if [[ "$RUN_DOCUMENT_STATUS" == "1" ]]; then
  run_step "Document extraction status" \
    python3 -m jobscan.document_extraction \
      --status \
      --database-url "$DATABASE_URL_EFFECTIVE"
fi

echo
echo "Daily refresh completed successfully."
echo "Completed: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
