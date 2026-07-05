#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jobscan.env import load_project_env
from jobscan.estimator.unknown_rows import main


def _argv_with_database_url(argv: list[str]) -> list[str]:
    if "--db-url" in argv:
        return argv
    load_project_env()
    database_url = os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        return argv
    return ["--db-url", database_url, *argv]


if __name__ == "__main__":
    raise SystemExit(main(_argv_with_database_url(sys.argv[1:])))
