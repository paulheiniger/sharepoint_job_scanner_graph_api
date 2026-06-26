from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jobscan.estimator import estimate_from_field_notes, load_estimator_data


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
    draft = result.get("draft_workbook_inputs") or {}
    for row in draft.get("adders_review_rows") or []:
        if isinstance(row, dict):
            parts.extend(clean_text(value) for value in row.values())
        else:
            parts.append(clean_text(row))
    for collection_name in ("material_plan", "labor_plan"):
        for row in result.get(collection_name) or []:
            if isinstance(row, dict):
                parts.append(clean_text(row.get("notes")))
                parts.append(clean_text(row.get("applies_reason")))
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


def numeric_matches(actual: Any, expected: Any, tolerance: float = 1.0) -> bool:
    actual_number = to_float(actual)
    expected_number = to_float(expected)
    if actual_number is None or expected_number is None:
        return False
    return abs(actual_number - expected_number) <= tolerance


def evaluate_case(case: dict[str, Any], estimator_data: Any = None) -> dict[str, Any]:
    result_obj = estimate_from_field_notes(case["notes"], {}, data=estimator_data)
    result = object_to_dict(result_obj)
    expected = case.get("expected") or {}
    material_rows = [row for row in result.get("material_plan") or [] if isinstance(row, dict)]
    labor_rows = [row for row in result.get("labor_plan") or [] if isinstance(row, dict)]
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
        "material_items": [row_text(row) for row in material_rows],
        "labor_tasks": [row_text(row) for row in labor_rows],
        "review_flags": result.get("review_flags") or [],
    }
    return {
        "case_id": case.get("case_id"),
        "passed": not failures,
        "failures": failures,
        "warnings": warnings,
        "actual": actual_summary,
    }


def load_data_for_eval(database_url: str | None, allow_db_missing: bool) -> Any:
    if not database_url:
        print("NEON_DATABASE_URL not set; estimator eval will run with local/default data only.")
        return None
    try:
        return load_estimator_data(REPO_ROOT, database_url=database_url, prefer_database=True)
    except Exception as exc:
        message = f"Could not load estimator data from database: {type(exc).__name__}: {exc}"
        if allow_db_missing:
            print(message)
            print("Continuing with data=None because --allow-db-missing was supplied.")
            return None
        raise RuntimeError(message) from exc


def run_eval(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_cases(args.cases)
    if args.case_id:
        cases = [case for case in cases if case.get("case_id") == args.case_id]
        if not cases:
            raise SystemExit(f"No estimator eval case found for --case-id {args.case_id!r}")
    data = load_data_for_eval(args.database_url, args.allow_db_missing)
    results = [evaluate_case(case, data) for case in cases]
    return {
        "total_cases": len(results),
        "passed_cases": sum(1 for result in results if result["passed"]),
        "failed_cases": sum(1 for result in results if not result["passed"]),
        "results": results,
    }


def print_report(report: dict[str, Any]) -> None:
    print(f"Estimator eval: {report['passed_cases']}/{report['total_cases']} cases passed")
    for result in report["results"]:
        status = "PASS" if result["passed"] else "FAIL"
        print(f"\n[{status}] {result['case_id']}")
        for failure in result["failures"]:
            print(f"  failure: {failure}")
        for warning in result["warnings"]:
            print(f"  warning: {warning}")
        if result["failures"]:
            actual = result["actual"]
            print(f"  header: {json.dumps(actual.get('header'), default=str)[:1000]}")
            print(f"  parsed: {json.dumps(actual.get('parsed_fields'), default=str)[:1000]}")
            print(f"  materials: {actual.get('material_items')}")
            print(f"  labor: {actual.get('labor_tasks')}")
            print(f"  review_flags: {actual.get('review_flags')}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic evals for the field-notes estimator.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--case-id")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--allow-db-missing", action="store_true")
    parser.add_argument("--database-url", default=os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run_eval(args)
    except Exception as exc:
        print(f"Estimator eval failed to run: {type(exc).__name__}: {exc}")
        return 1
    print_report(report)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"JSON report: {args.json_output}")
    return 1 if report["failed_cases"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
