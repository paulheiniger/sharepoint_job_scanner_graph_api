#!/usr/bin/env bash
set -Eeuo pipefail

# Azure Container Apps Job wrapper for the existing daily refresh. The database
# remains the source of delta state; Azure Files preserves the scan manifests
# and cached job folders that the current incremental parsers still require.

PERSISTENT_ROOT="${SCANNER_PERSISTENT_ROOT:-/mnt/spraytec-scanner}"
REQUIRE_SEEDED_CACHE="${SCANNER_REQUIRE_SEEDED_CACHE:-1}"
DAILY_REFRESH_SCRIPT="${DAILY_REFRESH_SCRIPT:-/app/scripts/daily_refresh.sh}"

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "ERROR: required environment variable ${name} is not configured." >&2
    exit 64
  fi
}

require_env "MS_TENANT_ID"
require_env "MS_CLIENT_ID"
require_env "MS_CLIENT_SECRET"

if [[ -z "${DATABASE_URL:-${NEON_DATABASE_URL:-${NEON_PSQL_URL:-}}}" ]]; then
  echo "ERROR: configure DATABASE_URL, NEON_DATABASE_URL, or NEON_PSQL_URL." >&2
  exit 64
fi

mkdir -p \
  "${PERSISTENT_ROOT}/cache/sharepoint" \
  "${PERSISTENT_ROOT}/cache/office_timesheets/Data/Timesheets" \
  "${PERSISTENT_ROOT}/cache/warranty_sources" \
  "${PERSISTENT_ROOT}/cache/warranty_master" \
  "${PERSISTENT_ROOT}/locks" \
  "${PERSISTENT_ROOT}/output/refresh_logs"

if [[ ! -w "${PERSISTENT_ROOT}" ]]; then
  echo "ERROR: scanner persistent root is not writable: ${PERSISTENT_ROOT}" >&2
  exit 73
fi

export CACHE_ROOT="${CACHE_ROOT:-${PERSISTENT_ROOT}/cache/sharepoint}"
export TIMESHEET_CACHE_ROOT="${TIMESHEET_CACHE_ROOT:-${PERSISTENT_ROOT}/cache/office_timesheets/Data/Timesheets}"
export WARRANTY_SOURCE_CACHE="${WARRANTY_SOURCE_CACHE:-${PERSISTENT_ROOT}/cache/warranty_sources}"
export WARRANTY_MASTER_CACHE="${WARRANTY_MASTER_CACHE:-${PERSISTENT_ROOT}/cache/warranty_master}"
export OUTPUT_DIR="${OUTPUT_DIR:-${PERSISTENT_ROOT}/output}"
export LOG_DIR="${LOG_DIR:-${PERSISTENT_ROOT}/output/refresh_logs}"
export LOCK_DIR="${LOCK_DIR:-${PERSISTENT_ROOT}/locks/daily_refresh.lock}"

if [[ "${REQUIRE_SEEDED_CACHE}" == "1" ]]; then
  if ! find "${CACHE_ROOT}" -name .jobscan_manifest.json -print -quit | grep -q .; then
    echo "ERROR: the Azure scanner cache has not been seeded with SharePoint scan manifests." >&2
    echo "Seed the Azure Files volume from the current scanner cache before enabling the schedule." >&2
    exit 78
  fi
fi

echo "Azure scanner preflight passed."
echo "Persistent root: ${PERSISTENT_ROOT}"
echo "Cache root: ${CACHE_ROOT}"
echo "Output root: ${OUTPUT_DIR}"

if [[ "${SCANNER_PREFLIGHT_ONLY:-0}" == "1" ]]; then
  exit 0
fi

if [[ ! -x "${DAILY_REFRESH_SCRIPT}" ]]; then
  echo "ERROR: daily refresh script is not executable: ${DAILY_REFRESH_SCRIPT}" >&2
  exit 66
fi

exec "${DAILY_REFRESH_SCRIPT}"
