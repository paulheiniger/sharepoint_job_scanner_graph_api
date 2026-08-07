from __future__ import annotations

import argparse
import json
import os

from jobscan.db_connections import create_resilient_engine
from jobscan.env import load_project_env
from jobscan.quickbooks.oauth import create_admin_authorization
from jobscan.quickbooks.repository import ensure_tables, get_connection
from jobscan.quickbooks.sync import sync_quickbooks


def main() -> int:
    load_project_env()
    parser = argparse.ArgumentParser(description="Prepare and operate the QuickBooks Online integration.")
    parser.add_argument("command", choices=("authorize-url", "status", "sync"))
    parser.add_argument("--database-url", default="")
    parser.add_argument("--full", action="store_true", help="Run a full instead of incremental synchronization.")
    args = parser.parse_args()
    database_url = args.database_url or os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("Set NEON_DATABASE_URL or DATABASE_URL.")
    engine = create_resilient_engine(database_url)
    try:
        ensure_tables(engine)
        if args.command == "authorize-url":
            print(create_admin_authorization(engine))
        elif args.command == "sync":
            print(json.dumps(sync_quickbooks(engine, full=args.full), indent=2, default=str))
        else:
            connection = get_connection(engine, decrypt_tokens=False)
            safe = {
                key: value
                for key, value in connection.items()
                if key not in {"access_token_encrypted", "refresh_token_encrypted"}
            }
            if not safe:
                safe = {
                    "company_name": os.getenv("QUICKBOOKS_EXPECTED_COMPANY_NAME", "Spray-Tec Inc."),
                    "status": "awaiting_administrator_authorization",
                }
            print(json.dumps(safe, indent=2, default=str))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
