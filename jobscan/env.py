from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from dotenv import dotenv_values, load_dotenv


GRAPH_ENV_KEYS = ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET")


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_project_env(dotenv_path: Path | str | None = None, *, override: bool = False) -> bool:
    """Load the repository .env file without replacing real host values.

    python-dotenv intentionally will not override an existing environment
    variable. Streamlit deployments can sometimes provide blank values, which
    should still be treated as missing so local .env values can fill them.
    """
    path = Path(dotenv_path) if dotenv_path is not None else project_root() / ".env"
    loaded = load_dotenv(dotenv_path=path, override=override)
    if path.exists():
        values = dotenv_values(path)
        for key, value in values.items():
            if value is not None and (override or not os.environ.get(key)):
                os.environ[key] = value
    return loaded


def graph_env_status(env: Mapping[str, str] | None = None) -> dict[str, str]:
    source = env if env is not None else os.environ
    return {key: "FOUND" if source.get(key) else "MISSING" for key in GRAPH_ENV_KEYS}


def graph_env_debug_info() -> dict[str, Any]:
    cwd_env = Path.cwd() / ".env"
    repo_env = project_root() / ".env"
    return {
        "current_working_directory": str(Path.cwd()),
        "cwd_dotenv_exists": cwd_env.exists(),
        "repo_dotenv_exists": repo_env.exists(),
        "graph_env_status": graph_env_status(),
    }
