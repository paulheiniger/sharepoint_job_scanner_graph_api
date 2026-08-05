#!/usr/bin/env bash
set -Eeuo pipefail

CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-$(command -v cloudflared || true)}"
CLOUDFLARE_TUNNEL_TOKEN_FILE="${CLOUDFLARE_TUNNEL_TOKEN_FILE:-${HOME}/.cloudflared/spraytec-streamlit.token}"

if [[ -z "$CLOUDFLARED_BIN" || ! -x "$CLOUDFLARED_BIN" ]]; then
  echo "cloudflared is not installed or is not executable." >&2
  exit 1
fi

if [[ ! -r "$CLOUDFLARE_TUNNEL_TOKEN_FILE" ]]; then
  echo "Tunnel token file is not readable: $CLOUDFLARE_TUNNEL_TOKEN_FILE" >&2
  exit 1
fi

TOKEN_MODE="$(stat -f '%Lp' "$CLOUDFLARE_TUNNEL_TOKEN_FILE")"
if (( (8#$TOKEN_MODE & 8#077) != 0 )); then
  echo "Tunnel token file permissions must be 600: $CLOUDFLARE_TUNNEL_TOKEN_FILE" >&2
  exit 1
fi

exec "$CLOUDFLARED_BIN" tunnel \
  --no-autoupdate \
  run \
  --token-file "$CLOUDFLARE_TUNNEL_TOKEN_FILE"
