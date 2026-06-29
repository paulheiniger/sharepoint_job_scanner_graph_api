from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jobscan.repair_estimator.estimator import (  # noqa: E402
    estimate_repair_from_notes,
    load_repair_history_from_database,
    sanitize_for_json,
    write_repair_audit_package,
)


def safe_db_target(db_url: str) -> dict[str, str]:
    parsed = urlparse(db_url)
    return {
        "database_engine": parsed.scheme.split("+")[0],
        "database_host": parsed.hostname or "",
        "database_name": parsed.path.lstrip("/"),
    }


def table_count(engine, table: str) -> int | None:
    try:
        with engine.connect() as connection:
            return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
    except Exception:
        return None


def load_cases(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def package_names(result: dict) -> set[str]:
    return {str(row.get("material_package")) for row in result.get("selected_repair_packages") or []}


def case_passed(case: dict, result: dict) -> tuple[bool, list[str]]:
    expected = case.get("expected") or {}
    parsed = result.get("parsed_scope") or {}
    failures: list[str] = []
    if expected.get("issue_type") and parsed.get("issue_type") != expected["issue_type"]:
        failures.append(f"issue_type expected {expected['issue_type']}, actual {parsed.get('issue_type')}")
    if expected.get("repair_type") and parsed.get("repair_type") != expected["repair_type"]:
        failures.append(f"repair_type expected {expected['repair_type']}, actual {parsed.get('repair_type')}")
    if expected.get("roof_type") and parsed.get("roof_type") != expected["roof_type"]:
        failures.append(f"roof_type expected {expected['roof_type']}, actual {parsed.get('roof_type')}")
    if "leak_present" in expected and bool(parsed.get("leak_present")) != bool(expected["leak_present"]):
        failures.append(f"leak_present expected {expected['leak_present']}, actual {parsed.get('leak_present')}")
    if expected.get("urgency") and parsed.get("emergency_or_standard") != expected["urgency"]:
        failures.append(f"urgency expected {expected['urgency']}, actual {parsed.get('emergency_or_standard')}")
    if expected.get("confidence") and result.get("confidence") != expected["confidence"]:
        failures.append(f"confidence expected {expected['confidence']}, actual {result.get('confidence')}")
    if expected.get("package_any") and not (set(expected["package_any"]) & package_names(result)):
        failures.append(f"expected any package {expected['package_any']}, actual {sorted(package_names(result))}")
    if expected.get("review_flag_contains"):
        text = " ".join(result.get("review_flags") or [])
        if expected["review_flag_contains"].lower() not in text.lower():
            failures.append(f"review flags did not contain {expected['review_flag_contains']!r}")
    if result.get("estimated_invoice_target") is None:
        failures.append("estimated_invoice_target is missing")
    if result.get("estimated_labor_hours_target") is None:
        failures.append("estimated_labor_hours_target is missing")
    return not failures, failures


def safe_stem(case_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", case_id).strip("_").lower() or "repair_case"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run repair estimator field-notes evaluation cases.")
    parser.add_argument("--db-url", default=os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL"), help="Database URL containing repair_* tables.")
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases.json"), help="Repair eval cases JSON.")
    parser.add_argument("--case-id", default="", help="Run one case_id.")
    parser.add_argument("--write-audit", action="store_true", help="Write JSON/XLSX audit package per case.")
    parser.add_argument("--audit-output-dir", type=Path, default=Path("output/repair_estimator/eval_audit"), help="Audit output directory.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    if not args.db_url:
        print("Missing --db-url or NEON_DATABASE_URL/DATABASE_URL.", file=sys.stderr)
        return 2
    target = safe_db_target(args.db_url)
    print(f"database engine: {target['database_engine']}")
    print(f"database host: {target['database_host']}")
    print(f"database name: {target['database_name']}")
    engine = create_engine(args.db_url)
    for table in ["repair_jobs", "repair_material_usage", "repair_labor_usage", "repair_scope_text", "repair_outcomes"]:
        print(f"{table} count: {table_count(engine, table)}")
    tables = load_repair_history_from_database(engine)
    cases = load_cases(args.cases)
    if args.case_id:
        cases = [case for case in cases if case.get("case_id") == args.case_id]
    if not cases:
        print("No repair eval cases selected.", file=sys.stderr)
        return 2

    passed = 0
    results = []
    for case in cases:
        result = estimate_repair_from_notes(case["notes"], tables)
        payload = sanitize_for_json(result.to_dict())
        ok, failures = case_passed(case, payload)
        passed += int(ok)
        status = "PASS" if ok else "FAIL"
        print(f"{status} {case['case_id']}: confidence={payload.get('confidence')} evidence={payload.get('evidence_summary', {}).get('similar_repair_count')}")
        for failure in failures:
            print(f"  - {failure}")
        if args.write_audit:
            write_repair_audit_package(result, args.audit_output_dir, stem=safe_stem(case["case_id"]))
        results.append({"case_id": case["case_id"], "passed": ok, "failures": failures, "result": payload})

    args.audit_output_dir.mkdir(parents=True, exist_ok=True)
    (args.audit_output_dir / "repair_eval_results.json").write_text(
        json.dumps(sanitize_for_json(results), indent=2, default=str),
        encoding="utf-8",
    )
    print(f"Repair estimator eval: {passed}/{len(cases)} cases passed")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
