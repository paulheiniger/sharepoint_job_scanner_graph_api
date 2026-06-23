from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


def detect_source_type(value) -> str:
    if hasattr(value, "name") and (hasattr(value, "read") or hasattr(value, "getvalue")):
        return "upload"
    if value is None:
        return "unknown"
    text = str(value).strip()
    if not text:
        return "unknown"
    parsed = urlparse(text)
    host = parsed.netloc.lower()
    if parsed.scheme in {"http", "https"} and ("sharepoint.com" in host or "onedrive.live.com" in host):
        return "sharepoint_url"
    if parsed.scheme in {"http", "https"}:
        return "unknown"
    try:
        if Path(text).expanduser().exists():
            return "local_path"
    except (OSError, ValueError):
        return "unknown"
    return "unknown"
