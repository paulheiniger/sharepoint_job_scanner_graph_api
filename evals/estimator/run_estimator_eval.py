from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except Exception:
    pass

from jobscan.db_connections import create_resilient_engine, database_target
from jobscan.estimator import estimate_from_field_notes, load_estimator_data
from jobscan.estimator.workbench import build_estimating_workbench, workbench_to_draft_workbook_inputs


DEFAULT_CASES_PATH = Path(__file__).with_name("field_notes_cases.json")


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def lower_text(value: Any) -> str:
    return clean_text(value).lower()


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def object_to_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return {
        key: getattr(value, key)
        for key in dir(value)
        if not key.startswith("_") and not callable(getattr(value, key))
    }


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def row_text(row: dict[str, Any]) -> str:
    fields = [
        "package",
        "category",
        "item",
        "item_name",
        "product_name",
        "task",
        "labor_package",
        "description",
        "notes",
        "source_type",
    ]
    return " ".join(lower_text(row.get(field)) for field in fields)


def rows_text(rows: list[dict[str, Any]]) -> str:
    return "\n".join(row_text(row) for row in rows)


def review_text(result: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.extend(clean_text(flag) for flag in result.get("review_flags") or [])
    workbench = result.get("decision_workbench") or {}
    parts.extend(clean_text(flag) for flag in workbench.get("review_flags") or [])
    draft = result.get("draft_workbook_inputs") or {}
    for row in draft.get("workbook_decisions") or []:
        if isinstance(row, dict):
            parts.append(clean_text(row.get("notes")))
            parts.append(clean_text(row.get("compatibility_warnings")))
            parts.append(clean_text(row.get("calculated_output_summary")))
    for section_name, section_rows in workbench.items():
        if not section_name.endswith("_template_decisions") and section_name not in {"insulation_surfaces", "insulation_performance_specs"}:
            continue
        for row in section_rows or []:
            if isinstance(row, dict):
                parts.append(clean_text(row.get("notes")))
                parts.append(clean_text(row.get("compatibility_warnings")))
                parts.append(clean_text(row.get("product_warning_summary")))
    return "\n".join(part for part in parts if part).lower()


def value_from_result(result: dict[str, Any], key: str) -> Any:
    parsed = result.get("parsed_fields") or {}
    header = ((result.get("draft_workbook_inputs") or {}).get("header") or {})
    dimension = parsed.get("dimension_summary") or {}
    candidates = [
        parsed.get(key),
        header.get(key),
        header.get(f"C12_{key}"),
        header.get("C12_estimated_sqft") if key in {"estimated_sqft", "surface_area_sqft"} else None,
        dimension.get(key) if isinstance(dimension, dict) else None,
    ]
    if key == "warranty_years":
        candidates.extend([parsed.get("warranty_target"), parsed.get("warranty_target_years"), parsed.get("warranty_amount")])
    if key == "thickness_inches":
        candidates.extend([parsed.get("foam_thickness_inches"), parsed.get("thickness_inches")])
    for candidate in candidates:
        if candidate not in (None, ""):
            return candidate
    return None


def text_field_contains(result: dict[str, Any], key: str, terms: list[str]) -> bool:
    parsed = result.get("parsed_fields") or {}
    value = lower_text(parsed.get(key))
    if not value and key == "project_type":
        value = lower_text(parsed.get("division")) + " " + lower_text(parsed.get("building_type"))
    return any(term.lower() in value for term in terms)


def package_present(rows: list[dict[str, Any]], package: str) -> bool:
    target = package.lower()
    return any(target in row_text(row) for row in rows)


def row_method(row: dict[str, Any]) -> str:
    return lower_text(row.get("selected_price_source") or row.get("calibration_method") or row.get("price_source_type") or row.get("source_type"))


def row_included_in_total(row: dict[str, Any]) -> bool:
    if row.get("included_in_total") is False:
        return False
    if row.get("estimated_cost") in (None, ""):
        return False
    return to_float(row.get("estimated_cost")) is not None


def numeric_matches(actual: Any, expected: Any, tolerance: float = 1.0) -> bool:
    actual_number = to_float(actual)
    expected_number = to_float(expected)
    if actual_number is None or expected_number is None:
        return False
    return abs(actual_number - expected_number) <= tolerance


def to_int(value: Any) -> int:
    number = to_float(value)
    return int(number) if number is not None else 0


def has_heavy_detail_trigger(notes: str) -> bool:
    text = lower_text(notes)
    return any(
        term in text
        for term in (
            "many penetrations",
            "lots of penetrations",
            "heavy penetrations",
            "heavy detail",
            "difficult access",
            "hard access",
            "poor condition",
            "severe rust",
        )
    )


def evaluate_case(case: dict[str, Any], estimator_data: Any = None) -> dict[str, Any]:
    result_obj = estimate_from_field_notes(case["notes"], {}, data=estimator_data)
    result = object_to_dict(result_obj)
    workbench = build_estimating_workbench(result_obj, estimator_data)
    draft_inputs = workbench_to_draft_workbook_inputs(workbench)
    result["decision_workbench"] = workbench
    result["draft_workbook_inputs"] = draft_inputs
    expected = case.get("expected") or {}
    decision_rows = [row for row in draft_inputs.get("workbook_decisions") or [] if isinstance(row, dict)]
    material_rows = [row for row in decision_rows if lower_text(row.get("row_type")) == "material"]
    labor_rows = [row for row in decision_rows if lower_text(row.get("row_type")) == "labor"]
    all_review_text = review_text(result)
    failures: list[str] = []
    warnings: list[str] = []

    for key in ["estimated_sqft", "gross_area_sqft", "deduction_area_sqft", "net_area_sqft", "warranty_years", "thickness_inches"]:
        if key in expected and not numeric_matches(value_from_result(result, key), expected[key]):
            failures.append(f"{key}: expected {expected[key]!r}, actual {value_from_result(result, key)!r}")

    for key in ["project_type", "substrate", "coating_type", "foam_type"]:
        contains_key = f"{key}_contains"
        if contains_key in expected and not text_field_contains(result, key, expected[contains_key]):
            failures.append(f"{key}: expected text containing one of {expected[contains_key]!r}")

    for package in expected.get("must_include_material_packages") or []:
        if not package_present(material_rows, package):
            failures.append(f"missing required material package: {package}")

    for package in expected.get("must_not_include_material_packages") or []:
        if package_present(material_rows, package):
            failures.append(f"unexpected material package present: {package}")

    for package in expected.get("must_not_include_labor_packages") or []:
        if package_present(labor_rows, package):
            failures.append(f"unexpected labor package present: {package}")

    if text_field_contains(result, "project_type", ["roof"]) or any("roof" in item for item in result.get("recommended_scope") or []):
        for row in material_rows:
            if "historical_cost_ratio" in row_method(row) and to_int(row.get("valid_quantity_ratio_count")) > 0:
                failures.append(
                    "historical_cost_ratio_fallback material row used despite valid physical quantity evidence: "
                    f"{row.get('item') or row.get('category')}"
                )
            if "historical_cost_ratio" in row_method(row) and row_included_in_total(row):
                failures.append(
                    "historical_cost_ratio_fallback material row was included in total: "
                    f"{row.get('item') or row.get('category')}"
                )
        estimated_sqft = to_float(value_from_result(result, "estimated_sqft"))
        total_labor_hours = sum(to_float(row.get("total_hours")) or to_float(row.get("labor_hours")) or 0 for row in labor_rows)
        if estimated_sqft and total_labor_hours / estimated_sqft * 1000 > 80:
            failures.append(
                f"labor hours per 1000 sqft exceeded max 80: {round(total_labor_hours / estimated_sqft * 1000, 2)}"
            )
        configured_roof_labor_cap = expected.get("roof_coating_labor_hours_per_1000_max")
        if configured_roof_labor_cap is not None and estimated_sqft:
            actual_hours_per_1000 = total_labor_hours / estimated_sqft * 1000
            if actual_hours_per_1000 > float(configured_roof_labor_cap):
                failures.append(
                    f"labor hours per 1000 sqft exceeded configured roof coating cap "
                    f"{configured_roof_labor_cap}: {round(actual_hours_per_1000, 2)}"
                )
        if expected.get("must_not_stack_caulk_details_without_heavy_trigger") and not has_heavy_detail_trigger(case.get("notes") or ""):
            task_texts = {lower_text(row.get("task") or row.get("labor_package")) for row in labor_rows}
            if {"labor_caulk", "labor_details", "labor_seam_sealer"}.issubset(task_texts):
                failures.append("labor_caulk stacked with labor_details and labor_seam_sealer without heavy-detail trigger")
        for row in labor_rows + material_rows:
            if row.get("template_type_match") is False and row.get("included_as_evidence") is True:
                failures.append("nonmatching template evidence was included in calibration")
        for row in result.get("similar_examples") or []:
            if isinstance(row, dict) and lower_text(row.get("match_strength")) == "weak" and row.get("included_as_evidence") is True:
                failures.append(f"weak-only similar job included as evidence: {row.get('job_id') or row.get('job_name')}")

    labor_task_expectation = expected.get("minimum_labor_tasks_from") or {}
    if labor_task_expectation:
        required_tasks = {lower_text(task) for task in labor_task_expectation.get("tasks") or []}
        min_count = int(labor_task_expectation.get("min_count") or 0)
        present = {
            lower_text(row.get("task") or row.get("labor_package"))
            for row in labor_rows
            if lower_text(row.get("task") or row.get("labor_package")) in required_tasks
        }
        if len(present) < min_count:
            failures.append(f"expected at least {min_count} labor tasks from {sorted(required_tasks)}, found {sorted(present)}")

    material_cost_multiple = expected.get("material_cost_max_multiple_of_coating")
    if material_cost_multiple is not None:
        coating_costs = [
            to_float(row.get("estimated_cost"))
            for row in material_rows
            if "coating" in lower_text(row.get("category")) and to_float(row.get("estimated_cost")) is not None
        ]
        coating_cost = max(coating_costs) if coating_costs else None
        if coating_cost:
            for row in material_rows:
                estimated_cost = to_float(row.get("estimated_cost"))
                source_type = lower_text(row.get("source_type") or row.get("selected_price_source"))
                if estimated_cost is not None and estimated_cost > coating_cost * float(material_cost_multiple) and "manual_override" not in source_type:
                    failures.append(
                        f"material row cost exceeds {material_cost_multiple}x coating cost: "
                        f"{row.get('item') or row.get('category')} cost={estimated_cost} coating={coating_cost}"
                    )
                if lower_text(row.get("selected_price_source")) == "rejected_historical_quantity_ratio" and estimated_cost is not None:
                    failures.append(f"rejected material row retained estimated_cost: {row.get('item') or row.get('category')}")

    for item in expected.get("should_include_or_flag") or []:
        if not package_present(material_rows, item) and item.lower() not in all_review_text:
            warnings.append(f"expected material/review signal not found: {item}")

    for item in expected.get("should_include_labor_or_review") or []:
        if not package_present(labor_rows, item) and item.lower() not in all_review_text:
            warnings.append(f"expected labor/review signal not found: {item}")

    required_review_terms = [term.lower() for term in expected.get("must_include_review_text_any") or []]
    if required_review_terms and not any(term in all_review_text for term in required_review_terms):
        failures.append(f"review text did not include any of: {required_review_terms}")

    travel_max = expected.get("travel_labor_hours_max")
    if travel_max is not None:
        travel = result.get("travel_plan") or {}
        if (to_float(travel.get("travel_labor_hours")) or 0) > float(travel_max):
            failures.append(f"travel_labor_hours exceeded max {travel_max}: {travel.get('travel_labor_hours')}")

    actual_summary = {
        "parsed_fields": result.get("parsed_fields"),
        "header": (result.get("draft_workbook_inputs") or {}).get("header"),
        "workbook_decision_count": len(decision_rows),
        "material_decisions": [row_text(row) for row in material_rows],
        "labor_decisions": [row_text(row) for row in labor_rows],
        "review_flags": result.get("review_flags") or [],
    }
    return {
        "case_id": case.get("case_id"),
        "passed": not failures,
        "failures": failures,
        "warnings": warnings,
        "actual": actual_summary,
    }


PREFLIGHT_TABLES = (
    "estimate_template_rows",
    "relationship_material_qty_ratios",
    "relationship_labor_rates",
)


def _safe_table_count(connection: Any, table_name: str) -> int:
    exists = connection.execute(
        text("SELECT to_regclass(:table_name)"),
        {"table_name": table_name},
    ).scalar()
    if not exists:
        return 0
    return int(connection.execute(text(f"SELECT count(*) FROM {table_name}")).scalar() or 0)


def estimator_database_preflight(database_url: str) -> dict[str, Any]:
    target = database_target(database_url)
    engine = create_resilient_engine(database_url)
    counts: dict[str, int] = {}
    with engine.connect() as connection:
        dialect_name = connection.dialect.name
        driver_name = connection.dialect.driver
        for table_name in PREFLIGHT_TABLES:
            counts[table_name] = _safe_table_count(connection, table_name)
    return {
        "database_engine": f"{dialect_name}+{driver_name}" if driver_name else dialect_name,
        "database_host": target.host,
        "database_name": target.database,
        "counts": counts,
    }


def print_estimator_database_preflight(info: dict[str, Any]) -> None:
    print("Estimator calibration database preflight:", flush=True)
    print(f"  database engine: {info.get('database_engine') or 'unknown'}", flush=True)
    print(f"  database host: {info.get('database_host') or 'unknown'}", flush=True)
    print(f"  database name: {info.get('database_name') or 'unknown'}", flush=True)
    counts = info.get("counts") or {}
    for table_name in PREFLIGHT_TABLES:
        print(f"  {table_name} count: {counts.get(table_name, 0)}", flush=True)


def load_data_for_eval(database_url: str | None) -> Any:
    if not database_url:
        raise RuntimeError(
            "NEON_DATABASE_URL is required for estimator evaluation. "
            "Set NEON_DATABASE_URL to the production Neon database URL before running evals."
        )
    try:
        print_estimator_database_preflight(estimator_database_preflight(database_url))
        return load_estimator_data(REPO_ROOT, database_url=database_url, prefer_database=True)
    except Exception as exc:
        message = f"Could not load estimator data from database: {type(exc).__name__}: {exc}"
        raise RuntimeError(message) from exc


def run_eval_with_data(args: argparse.Namespace) -> tuple[dict[str, Any], Any]:
    cases = load_cases(args.cases)
    if args.case_id:
        cases = [case for case in cases if case.get("case_id") == args.case_id]
        if not cases:
            raise SystemExit(f"No estimator eval case found for --case-id {args.case_id!r}")
    data = load_data_for_eval(args.database_url)
    results = [evaluate_case(case, data) for case in cases]
    return {
        "total_cases": len(results),
        "passed_cases": sum(1 for result in results if result["passed"]),
        "failed_cases": sum(1 for result in results if not result["passed"]),
        "results": results,
    }, data


def run_eval(args: argparse.Namespace) -> dict[str, Any]:
    report, _data = run_eval_with_data(args)
    return report


def audit_command(case_id: str, audit_output_dir: Path) -> str:
    return (
        "python -m jobscan.estimator.calibration_audit "
        f"--case-id {case_id} "
        '--database-url "$NEON_DATABASE_URL" '
        f"--out-dir {audit_output_dir} "
        "--evidence-limit 50"
    )


def print_report(report: dict[str, Any], *, audit_output_dir: Path = Path("output/estimator_audit")) -> None:
    print(f"Estimator eval: {report['passed_cases']}/{report['total_cases']} cases passed")
    for result in report["results"]:
        status = "PASS" if result["passed"] else "FAIL"
        print(f"\n[{status}] {result['case_id']}")
        for failure in result["failures"]:
            print(f"  failure: {failure}")
        for warning in result["warnings"]:
            print(f"  warning: {warning}")
        if result["failures"] or result["warnings"]:
            print(f"  audit: {audit_command(result['case_id'], audit_output_dir)}")
        if result["failures"]:
            actual = result["actual"]
            print(f"  header: {json.dumps(actual.get('header'), default=str)[:1000]}")
            print(f"  parsed: {json.dumps(actual.get('parsed_fields'), default=str)[:1000]}")
            print(f"  workbook_decision_count: {actual.get('workbook_decision_count')}")
            print(f"  material decisions: {actual.get('material_decisions')}")
            print(f"  labor decisions: {actual.get('labor_decisions')}")
            print(f"  review_flags: {actual.get('review_flags')}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic evals for the field-notes estimator.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--case-id")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--database-url", default=os.getenv("NEON_DATABASE_URL"))
    parser.add_argument("--write-audit", action="store_true", help="Write estimator calibration audit packages for selected cases.")
    parser.add_argument("--audit-output-dir", type=Path, default=Path("output/estimator_audit"))
    parser.add_argument("--fast", action="store_true", help="Write compact audit evidence when --write-audit is used.")
    parser.add_argument("--evidence-limit", type=int, default=50, help="Maximum evidence rows per audit evidence sheet.")
    parser.add_argument("--debug-evidence", action="store_true", help="Write full audit diagnostics when --write-audit is used.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report, data = run_eval_with_data(args)
    except Exception as exc:
        print(f"Estimator eval failed to run: {type(exc).__name__}: {exc}")
        return 1
    print_report(report, audit_output_dir=args.audit_output_dir)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"JSON report: {args.json_output}")
    if args.write_audit:
        try:
            from jobscan.estimator.calibration_audit import run_audit_for_case

            for result in report["results"]:
                paths = run_audit_for_case(
                    case_id=result["case_id"],
                    database_url=args.database_url,
                    out_dir=args.audit_output_dir,
                    cases_path=args.cases,
                    data=data,
                    evidence_limit=args.evidence_limit,
                    fast=args.fast,
                    debug_evidence=args.debug_evidence,
                )
                print(f"Audit JSON for {result['case_id']}: {paths['json']}")
                print(f"Audit XLSX for {result['case_id']}: {paths['xlsx']}")
        except Exception as exc:
            print(f"Could not write estimator audit package: {type(exc).__name__}: {exc}")
            return 1
    return 1 if report["failed_cases"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
