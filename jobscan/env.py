from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


GRAPH_ENV_KEYS = ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET")


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_project_env(dotenv_path: Path | str | None = None) -> bool:
    """Load the repository .env file without overriding already-set values."""
    path = Path(dotenv_path) if dotenv_path is not None else project_root() / ".env"
    return load_dotenv(dotenv_path=path)


def graph_env_status(env: Mapping[str, str] | None = None) -> dict[str, str]:
    source = env if env is not None else os.environ
    return {key: "FOUND" if source.get(key) else "MISSING" for key in GRAPH_ENV_KEYS}
