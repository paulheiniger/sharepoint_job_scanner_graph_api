#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
LOG_DIR="${SPRAYTEC_SERVICE_LOG_DIR:-${HOME}/spray-tec/logs}"
TOKEN_FILE="${CLOUDFLARE_TUNNEL_TOKEN_FILE:-${HOME}/.cloudflared/spraytec-streamlit.token}"
STREAMLIT_LABEL="com.spraytec.streamlit"
TUNNEL_LABEL="com.spraytec.streamlit-tunnel"
STREAMLIT_PLIST="${LAUNCH_AGENTS_DIR}/${STREAMLIT_LABEL}.plist"
TUNNEL_PLIST="${LAUNCH_AGENTS_DIR}/${TUNNEL_LABEL}.plist"
USER_ID="$(id -u)"
CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-$(command -v cloudflared || true)}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for macOS." >&2
  exit 1
fi

if [[ "$ROOT_DIR" == */Downloads/* ]]; then
  echo "Move the checkout outside Downloads before installing launchd services." >&2
  exit 1
fi

if [[ ! -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  echo "Missing ${ROOT_DIR}/.venv/bin/python. Create the application virtualenv first." >&2
  exit 1
fi

if [[ -z "$CLOUDFLARED_BIN" || ! -x "$CLOUDFLARED_BIN" ]]; then
  echo "cloudflared is not installed. Install it with: brew install cloudflared" >&2
  exit 1
fi

if [[ ! -r "$TOKEN_FILE" ]]; then
  echo "Missing tunnel token file: $TOKEN_FILE" >&2
  echo "Create it locally with mode 600; never add it to Git." >&2
  exit 1
fi

TOKEN_MODE="$(stat -f '%Lp' "$TOKEN_FILE")"
if (( (8#$TOKEN_MODE & 8#077) != 0 )); then
  echo "Tunnel token file permissions must be 600: $TOKEN_FILE" >&2
  exit 1
fi

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"
chmod +x \
  "${ROOT_DIR}/scripts/start_streamlit_dashboard.sh" \
  "${ROOT_DIR}/scripts/start_streamlit_cloudflare_tunnel.sh"

python3 - "$STREAMLIT_PLIST" "$STREAMLIT_LABEL" "$ROOT_DIR" "$LOG_DIR" <<'PY'
import plistlib
import sys
from pathlib import Path

path, label, root, log_dir = sys.argv[1:]
payload = {
    "Label": label,
    "ProgramArguments": [f"{root}/scripts/start_streamlit_dashboard.sh"],
    "WorkingDirectory": root,
    "RunAtLoad": True,
    "KeepAlive": {"SuccessfulExit": False},
    "ThrottleInterval": 10,
    "StandardOutPath": f"{log_dir}/streamlit.out.log",
    "StandardErrorPath": f"{log_dir}/streamlit.err.log",
}
with Path(path).open("wb") as handle:
    plistlib.dump(payload, handle, sort_keys=False)
PY

python3 - "$TUNNEL_PLIST" "$TUNNEL_LABEL" "$ROOT_DIR" "$LOG_DIR" "$TOKEN_FILE" "$CLOUDFLARED_BIN" <<'PY'
import plistlib
import sys
from pathlib import Path

path, label, root, log_dir, token_file, cloudflared_bin = sys.argv[1:]
payload = {
    "Label": label,
    "ProgramArguments": [f"{root}/scripts/start_streamlit_cloudflare_tunnel.sh"],
    "WorkingDirectory": root,
    "EnvironmentVariables": {
        "CLOUDFLARE_TUNNEL_TOKEN_FILE": token_file,
        "CLOUDFLARED_BIN": cloudflared_bin,
    },
    "RunAtLoad": True,
    "KeepAlive": {"SuccessfulExit": False},
    "ThrottleInterval": 10,
    "StandardOutPath": f"{log_dir}/streamlit-tunnel.out.log",
    "StandardErrorPath": f"{log_dir}/streamlit-tunnel.err.log",
}
with Path(path).open("wb") as handle:
    plistlib.dump(payload, handle, sort_keys=False)
PY

plutil -lint "$STREAMLIT_PLIST"
plutil -lint "$TUNNEL_PLIST"

launchctl bootout "gui/${USER_ID}" "$STREAMLIT_PLIST" 2>/dev/null || true
launchctl bootout "gui/${USER_ID}" "$TUNNEL_PLIST" 2>/dev/null || true
launchctl bootstrap "gui/${USER_ID}" "$STREAMLIT_PLIST"
launchctl bootstrap "gui/${USER_ID}" "$TUNNEL_PLIST"
launchctl kickstart -k "gui/${USER_ID}/${STREAMLIT_LABEL}"
launchctl kickstart -k "gui/${USER_ID}/${TUNNEL_LABEL}"

echo "Installed and started ${STREAMLIT_LABEL} and ${TUNNEL_LABEL}."
echo "Local health: http://127.0.0.1:8501/_stcore/health"
echo "Logs: $LOG_DIR"
