from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jobscan.env import load_project_env
from jobscan.estimator import generated_cases
from jobscan.estimator.data_loader import load_estimator_data


DEFAULT_CASES_JSONL = Path("output/estimator_generated_cases/generated_live_cases_chat_reviewed.jsonl")
DEFAULT_CASES_DIR = Path("output/estimator_generated_cases/cases")
DEFAULT_OUT_DIR = Path("output/estimator_generated_cases/reviewed_note_eval")
ACTIONABLE_EXPECTED_KINDS = {"material", "labor", "equipment", "travel"}
SCAFFOLD_EXPECTED_KINDS = {"header", "total", "other", "insurance", "permit", "overhead_profit", "warranty"}
BASELINE_REQUIRED_ROWS = {
    "insulation": {19, 78, 86, 92, 95},
    "roofing": {116, 122},
}
CONDITIONAL_REVIEW_TERMS = ("review", "possible", "if", "may", "qualify", "confirm", "before committing")
ROW_EVIDENCE_TERMS = {
    "coating": ("coating", "restoration", "top coat"),
    "foam": ("foam", "spray foam", "insulation"),
    "primer": ("primer", "prime", "rust"),
    "caulk_sealant": ("caulk", "sealant", "detail", "penetration"),
    "seams_misc": ("seam", "seams"),
    "penetrations": ("penetration", "penetrations"),
    "fabric": ("fabric", "reinforcement"),
    "generator": ("generator", "temp power"),
    "lift": ("lift", "access", "equipment"),
    "truck_expense": ("truck", "travel"),
    "labor_mask": ("mask", "masking"),
    "labor_loading": ("loading", "setup", "set up"),
    "labor_traveling": ("travel",),
    "labor_details": ("detail", "details"),
    "labor_cleanup": ("cleanup", "clean up"),
    "thermal_barrier_coating": ("thermal barrier", "ignition barrier", "dc315"),
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if pd.notna(number) else default


def _reviewed_notes_for_case(case: dict[str, Any], cases_dir: Path, reviewed_filename: str) -> tuple[str, str]:
    case_id = str(case.get("case_id") or "")
    reviewed_path = cases_dir / case_id / reviewed_filename
    if reviewed_path.exists():
        return reviewed_path.read_text(encoding="utf-8").strip(), str(reviewed_path)
    return str(case.get("generated_notes") or "").strip(), "generated_notes"


def _scope_checks(case: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    expected = case.get("expected_scope_fields") or {}
    parsed = validation.get("parsed_scope") or {}
    expected_area = _safe_float(expected.get("estimated_sqft"), 0.0)
    actual_area = _safe_float(
        parsed.get("estimated_sqft")
        or parsed.get("net_sqft")
        or parsed.get("surface_area_sqft")
        or ((parsed.get("dimension_summary") or {}).get("net_area_sqft") if isinstance(parsed.get("dimension_summary"), dict) else None),
        0.0,
    )
    area_error_pct = abs(actual_area - expected_area) / expected_area if expected_area and actual_area else None
    expected_terms = [str(term).lower() for term in expected.get("project_type_contains") or []]
    project_blob = " ".join(
        str(parsed.get(field) or "").lower()
        for field in ("project_type", "division", "coating_type", "foam_type", "roof_condition", "estimate_mode")
    )
    coating_path = bool(parsed.get("coating_required") or parsed.get("coating_path_review") or "coating" in project_blob)
    missing_project_terms = [
        term
        for term in expected_terms
        if not (term in project_blob or (term == "coating" and coating_path))
    ]
    expected_warranty = _safe_float(expected.get("warranty_years"), 0.0)
    actual_warranty = _safe_float(parsed.get("warranty_years") or parsed.get("warranty_target_years"), 0.0)
    warranty_evidenced = _notes_state_warranty_years(str(validation.get("notes") or ""), expected_warranty) if expected_warranty else False
    explicit_warranty_pass = True if not expected_warranty or not warranty_evidenced else actual_warranty == expected_warranty
    return {
        "expected_area": expected_area,
        "actual_area": actual_area,
        "area_error_pct": area_error_pct,
        "scope_area_pass": bool(expected_area and actual_area and area_error_pct is not None and area_error_pct <= 0.12),
        "expected_project_terms": expected_terms,
        "missing_project_terms": missing_project_terms,
        "coating_path_pass": "coating" not in expected_terms or coating_path,
        "project_type_pass": not missing_project_terms,
        "expected_warranty_years": expected_warranty or None,
        "actual_warranty_years": actual_warranty or None,
        "warranty_evidenced_in_notes": warranty_evidenced,
        "explicit_warranty_pass": explicit_warranty_pass,
        "warranty_evaluation_reason": (
            "not_expected"
            if not expected_warranty
            else "not_evidenced_in_reviewed_notes"
            if not warranty_evidenced
            else "matched"
            if actual_warranty == expected_warranty
            else "explicit_in_reviewed_notes"
        ),
    }


def _notes_state_warranty_years(notes: str, expected_warranty: float) -> bool:
    if not expected_warranty:
        return False
    expected = int(expected_warranty)
    for match in re.finditer(r"(\d{1,2})\s*[- ]?\s*(?:year|yr)\b", notes, re.I):
        if int(match.group(1)) != expected:
            continue
        window = notes[max(0, match.start() - 60) : match.end() + 80].lower()
        if re.search(r"\b(?:warranty|system|coating|restoration|maintenance)\b", window):
            return True
    return False


def _expected_rows_for_kinds(case: dict[str, Any], kinds: set[str]) -> set[int]:
    rows: set[int] = set()
    for decision in case.get("expected_decisions") or []:
        if not isinstance(decision, dict):
            continue
        if str(decision.get("line_item_kind") or "").lower() not in kinds:
            continue
        row = _safe_float(decision.get("workbook_row"), -1)
        if row >= 0:
            rows.add(int(row))
    return rows


def _row_overlap(case: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    expected_rows = {
        int(_safe_float(row, -1))
        for row in case.get("expected_workbook_rows") or []
        if _safe_float(row, -1) >= 0
    }
    actionable_expected_rows = _expected_rows_for_kinds(case, ACTIONABLE_EXPECTED_KINDS)
    scaffold_expected_rows = _expected_rows_for_kinds(case, SCAFFOLD_EXPECTED_KINDS)
    actual_rows = {
        int(_safe_float(row, -1))
        for row in validation.get("actual_workbook_rows") or []
        if _safe_float(row, -1) >= 0
    }
    overlap = expected_rows & actual_rows
    decision_overlap = actionable_expected_rows & actual_rows
    classifications = _classify_missing_expected_rows(case, validation, actionable_expected_rows - actual_rows)
    duplicate_count = int(_safe_float(validation.get("duplicate_decision_row_count"), 0))
    return {
        "raw_expected_row_count": len(expected_rows),
        "actual_row_count": len(actual_rows),
        "raw_overlap_count": len(overlap),
        "raw_overlap_ratio": len(overlap) / len(expected_rows) if expected_rows else None,
        "raw_expected_rows": sorted(expected_rows),
        "actual_rows": sorted(actual_rows),
        "raw_matched_rows": sorted(overlap),
        "raw_missing_rows": sorted(expected_rows - actual_rows),
        "extra_rows": sorted(actual_rows - expected_rows),
        "decision_expected_row_count": len(actionable_expected_rows),
        "decision_row_overlap_count": len(decision_overlap),
        "decision_row_overlap_ratio": len(decision_overlap) / len(actionable_expected_rows) if actionable_expected_rows else None,
        "decision_expected_rows": sorted(actionable_expected_rows),
        "decision_matched_rows": sorted(decision_overlap),
        "decision_missing_rows": sorted(actionable_expected_rows - actual_rows),
        "scaffold_expected_row_count": len(scaffold_expected_rows),
        "scaffold_expected_rows": sorted(scaffold_expected_rows),
        "missing_decision_rows_by_reason": classifications,
        "prompt_evidenced_missing_count": len(classifications["prompt_evidenced"]),
        "baseline_required_missing_count": len(classifications["baseline_required"]),
        "conditional_review_missing_count": len(classifications["conditional_review"]),
        "hidden_historical_only_count": len(classifications["historical_only"]),
        "scaffolding_or_total_missing_count": len(classifications["scaffolding_or_total"]),
        "duplicate_decision_row_count": duplicate_count,
        "prompt_evidenced_decision_pass": not classifications["prompt_evidenced"],
        "baseline_required_decision_pass": not classifications["baseline_required"],
        "conditional_review_decision_pass": not classifications["conditional_review"],
        "duplicate_decision_row_pass": duplicate_count == 0,
    }


def _classify_missing_expected_rows(case: dict[str, Any], validation: dict[str, Any], missing_rows: set[int]) -> dict[str, list[int]]:
    result = {
        "prompt_evidenced": [],
        "baseline_required": [],
        "conditional_review": [],
        "historical_only": [],
        "scaffolding_or_total": [],
    }
    notes = str(validation.get("notes") or "").lower()
    template_type = str(case.get("template_type") or "").lower()
    expected_by_row: dict[int, dict[str, Any]] = {}
    for decision in case.get("expected_decisions") or []:
        if not isinstance(decision, dict):
            continue
        row = int(_safe_float(decision.get("workbook_row"), -1))
        if row >= 0:
            expected_by_row.setdefault(row, decision)
    for row in sorted(missing_rows):
        decision = expected_by_row.get(row, {})
        kind = str(decision.get("line_item_kind") or "").lower()
        bucket = str(decision.get("template_bucket") or "").lower()
        item = str(decision.get("resolved_item_name") or decision.get("line_item_name") or "").lower()
        if kind in SCAFFOLD_EXPECTED_KINDS:
            result["scaffolding_or_total"].append(row)
        elif row in BASELINE_REQUIRED_ROWS.get(template_type, set()):
            result["baseline_required"].append(row)
        elif _row_is_prompt_evidenced(notes, bucket, item):
            if any(term in notes for term in CONDITIONAL_REVIEW_TERMS):
                result["conditional_review"].append(row)
            else:
                result["prompt_evidenced"].append(row)
        else:
            result["historical_only"].append(row)
    return result


def _row_is_prompt_evidenced(notes: str, bucket: str, item: str) -> bool:
    terms = ROW_EVIDENCE_TERMS.get(bucket, ())
    if any(term and term in notes and not _term_negated(notes, term) for term in terms):
        return True
    words = [word for word in re.split(r"[^a-z0-9]+", item.lower()) if len(word) >= 5]
    return any(word in notes and not _term_negated(notes, word) for word in words[:4])


def _term_negated(notes: str, term: str) -> bool:
    pattern = rf"\b(?:no|not|without)\b[^.;,\n]{{0,35}}\b{re.escape(term)}\b"
    return bool(re.search(pattern, notes, re.I))


def _failure_reason(scope: dict[str, Any], rows: dict[str, Any], validation: dict[str, Any]) -> str:
    reasons: list[str] = []
    if validation.get("failures"):
        reasons.append("estimator_validation_failure")
    if not scope["scope_area_pass"]:
        reasons.append("area_parser")
    if not scope["project_type_pass"] or not scope["coating_path_pass"]:
        reasons.append("scope_classification")
    if not scope["explicit_warranty_pass"]:
        reasons.append("explicit_warranty_mismatch")
    if not rows["prompt_evidenced_decision_pass"]:
        reasons.append("prompt_evidenced_decision_missing")
    if not rows["baseline_required_decision_pass"]:
        reasons.append("baseline_required_decision_missing")
    if not rows["conditional_review_decision_pass"]:
        reasons.append("conditional_review_decision_missing")
    if not rows["duplicate_decision_row_pass"]:
        reasons.append("duplicate_decision_rows")
    if rows["raw_overlap_ratio"] is not None and rows["raw_overlap_ratio"] < 0.35 <= (rows["decision_row_overlap_ratio"] or 0):
        reasons.append("raw_overlap_penalized_by_scaffolding")
    return ",".join(reasons) or "passed"


def evaluate_cases(
    *,
    cases_jsonl: Path,
    cases_dir: Path,
    reviewed_filename: str,
    template_type: str | None,
    case_id: str | None,
) -> list[dict[str, Any]]:
    load_project_env()
    data = load_estimator_data(prefer_database=True)
    cases = [json.loads(line) for line in cases_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    if template_type:
        cases = [case for case in cases if str(case.get("template_type") or "") == template_type]
    if case_id:
        cases = [case for case in cases if str(case.get("case_id") or "") == case_id]

    results: list[dict[str, Any]] = []
    for case in cases:
        notes, notes_source = _reviewed_notes_for_case(case, cases_dir, reviewed_filename)
        eval_case = {**case, "generated_notes": notes}
        validation = generated_cases.validate_generated_case(eval_case, data)
        validation["notes"] = notes
        scope = _scope_checks(case, validation)
        rows = _row_overlap(case, validation)
        pass_status = (
            not validation.get("failures")
            and scope["scope_area_pass"]
            and scope["project_type_pass"]
            and scope["coating_path_pass"]
            and scope["explicit_warranty_pass"]
            and rows["prompt_evidenced_decision_pass"]
            and rows["baseline_required_decision_pass"]
            and rows["conditional_review_decision_pass"]
            and rows["duplicate_decision_row_pass"]
        )
        reason = _failure_reason(scope, rows, validation)
        results.append(
            {
                "case_id": case.get("case_id"),
                "template_type": case.get("template_type"),
                "source_job_id": case.get("source_job_id"),
                "notes_source": notes_source,
                "status": "pass" if pass_status else "review",
                "evaluation_reason": reason,
                "validation_status": validation.get("status"),
                "failures": validation.get("failures") or [],
                "warnings": validation.get("warnings") or [],
                "parsed_project_type": (validation.get("parsed_scope") or {}).get("project_type"),
                "parsed_division": (validation.get("parsed_scope") or {}).get("division"),
                **scope,
                **rows,
                "decision_count": validation.get("decision_count"),
            }
        )
    return results


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "case_count": len(results),
        "status_counts": pd.Series([row["status"] for row in results]).value_counts().to_dict() if results else {},
        "template_type_counts": pd.Series([row["template_type"] for row in results]).value_counts().to_dict() if results else {},
        "average_area_error_pct": round(
            sum(row["area_error_pct"] for row in results if row["area_error_pct"] is not None)
            / max(1, sum(1 for row in results if row["area_error_pct"] is not None)),
            6,
        ),
        "average_row_overlap_ratio": round(
            sum(row["decision_row_overlap_ratio"] for row in results if row["decision_row_overlap_ratio"] is not None)
            / max(1, sum(1 for row in results if row["decision_row_overlap_ratio"] is not None)),
            6,
        ),
        "average_raw_row_overlap_ratio": round(
            sum(row["raw_overlap_ratio"] for row in results if row["raw_overlap_ratio"] is not None)
            / max(1, sum(1 for row in results if row["raw_overlap_ratio"] is not None)),
            6,
        ),
        "duplicate_decision_row_count": sum(int(row.get("duplicate_decision_row_count") or 0) for row in results),
        "hidden_historical_only_count": sum(int(row.get("hidden_historical_only_count") or 0) for row in results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate generated estimator cases using reviewed note text.")
    parser.add_argument("--cases-jsonl", type=Path, default=DEFAULT_CASES_JSONL)
    parser.add_argument("--cases-dir", type=Path, default=DEFAULT_CASES_DIR)
    parser.add_argument("--reviewed-filename", default="notes_chat_reviewed.txt")
    parser.add_argument("--template-type", choices=["roofing", "insulation"], default=None)
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    results = evaluate_cases(
        cases_jsonl=args.cases_jsonl,
        cases_dir=args.cases_dir,
        reviewed_filename=args.reviewed_filename,
        template_type=args.template_type,
        case_id=args.case_id,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary = _summary(results)
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (args.out_dir / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    compact_rows = []
    for row in results:
        compact_rows.append(
            {
                key: row.get(key)
                for key in (
                    "case_id",
                    "template_type",
                    "status",
                    "evaluation_reason",
                    "validation_status",
                    "parsed_project_type",
                    "expected_area",
                    "actual_area",
                    "area_error_pct",
                    "scope_area_pass",
                    "coating_path_pass",
                    "project_type_pass",
                    "missing_project_terms",
                    "expected_warranty_years",
                    "actual_warranty_years",
                    "warranty_evidenced_in_notes",
                    "explicit_warranty_pass",
                    "warranty_evaluation_reason",
                    "decision_row_overlap_ratio",
                    "prompt_evidenced_decision_pass",
                    "baseline_required_decision_pass",
                    "conditional_review_decision_pass",
                    "hidden_historical_only_count",
                    "duplicate_decision_row_count",
                    "decision_row_overlap_count",
                    "decision_expected_row_count",
                    "raw_overlap_ratio",
                    "raw_overlap_count",
                    "raw_expected_row_count",
                    "actual_row_count",
                    "warnings",
                    "failures",
                )
            }
        )
    pd.DataFrame(compact_rows).to_csv(args.out_dir / "results.csv", index=False)
    print(json.dumps(summary, indent=2, default=str))
    print(f"results_json: {args.out_dir / 'results.json'}")
    print(f"results_csv: {args.out_dir / 'results.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
