from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine

from .estimator import (
    estimate_repair_from_notes,
    load_repair_history_from_database,
    sanitize_for_json,
    write_repair_audit_package,
)


def safe_stem(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip()).strip("_").lower()
    return text[:80] or "repair_estimate"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Estimate a small repair from field notes using VSimple repair history.")
    parser.add_argument("--notes", required=True, help="Repair field notes.")
    parser.add_argument("--db-url", default=os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL"), help="Database URL containing repair_* tables.")
    parser.add_argument("--out-dir", type=Path, default=Path("output/repair_estimator/audit"), help="Audit output directory.")
    parser.add_argument("--roof-type", default="", help="Optional roof type override.")
    parser.add_argument("--urgency", default="", help="Optional urgency override: standard or emergency.")
    parser.add_argument("--customer-job-name", default="", help="Optional label for the audit filename.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    args = parse_args(argv)
    if not args.db_url:
        raise SystemExit("Missing --db-url or NEON_DATABASE_URL/DATABASE_URL.")
    engine = create_engine(args.db_url)
    tables = load_repair_history_from_database(engine)
    result = estimate_repair_from_notes(
        args.notes,
        tables,
        overrides={
            "roof_type": args.roof_type,
            "urgency": args.urgency,
        },
    )
    label = args.customer_job_name or result.parsed_scope.get("issue_type") or "repair_estimate"
    paths = write_repair_audit_package(result, args.out_dir, stem=safe_stem(str(label)))
    print(json.dumps(sanitize_for_json(result.to_dict()), indent=2, default=str))
    print(f"Wrote repair estimate audit files to {args.out_dir}")
    for name, path in paths.items():
        print(f"- {name}: {path}")


if __name__ == "__main__":
    main()
