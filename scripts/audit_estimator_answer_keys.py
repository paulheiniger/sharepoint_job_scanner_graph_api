from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jobscan.estimator import estimate_from_field_notes
from jobscan.estimator.reference_answer_key import (
    answer_key_to_workbook_decision_preferences,
    build_reference_estimate_answer_key,
)
from jobscan.estimator.schemas import EstimatorData
from jobscan.estimator.workbench import build_estimating_workbench, recalculate_workbench_tables


DEFAULT_CASES_DIR = Path("output/estimator_generated_cases/cases")
DEFAULT_OUT_DIR = Path("output/estimator_generated_cases/answer_key_audit")
EXCLUDED_KINDS = {"header", "total", "subtotal", "metadata", "other"}
EXCLUDED_BUCKETS = {
    "address",
    "customer",
    "email",
    "estimate_date",
    "estimated_square_feet",
    "job_name",
    "phone",
    "total_job_cost",
    "worksheet_price",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return _text(value).lower().replace(" ", "_").replace("-", "_")


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def answer_key_pollution_reasons(row: dict[str, Any]) -> list[str]:
    bucket = _norm(row.get("template_bucket"))
    reasons: list[str] = []
    thickness = _safe_float(row.get("thickness_inches"))
    if bucket in {"foam", "roofing_foam"} and thickness is not None and not 0.01 <= thickness <= 24.0:
        reasons.append(f"implausible foam thickness {thickness:g}")
    yield_value = _safe_float(row.get("yield_or_coverage") or row.get("yield_factor"))
    if bucket in {"foam", "roofing_foam"} and yield_value is not None and not 100.0 <= yield_value <= 20000.0:
        reasons.append(f"implausible foam yield/coverage {yield_value:g}")
    for field in ("crew_size", "crew_selector_code", "people_count"):
        crew = _safe_float(row.get(field))
        if crew is not None and not 0.01 <= crew <= 20.0:
            reasons.append(f"implausible {field} {crew:g}")
    return reasons


def is_actionable_answer_key_row(row: dict[str, Any]) -> bool:
    bucket = _norm(row.get("template_bucket"))
    kind = _norm(row.get("line_item_kind"))
    if kind in EXCLUDED_KINDS or bucket in EXCLUDED_BUCKETS:
        return False
    return bool(bucket and bucket != "unknown")


def _decision_row_id(row: dict[str, Any]) -> str:
    value = row.get("workbook_row") or row.get("row_number") or row.get("source_row")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _text(value)


def _case_template_type(case_dir: Path, decisions: list[dict[str, Any]]) -> str:
    for row in decisions:
        template_type = _norm(row.get("template_type"))
        if template_type in {"roofing", "insulation", "flooring"}:
            return template_type
    case_id = case_dir.name.lower()
    if "insulation" in case_id:
        return "insulation"
    if "flooring" in case_id:
        return "flooring"
    return "roofing"


def _rows_for_answer_key(decisions: list[dict[str, Any]], template_type: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(decisions):
        if not isinstance(row, dict):
            continue
        copied = dict(row)
        copied.setdefault("template_type", template_type)
        copied.setdefault("row_number", copied.get("workbook_row"))
        copied.setdefault("template_row_id", copied.get("template_row_id") or f"source-decision-{index}")
        copied.setdefault("line_item_kind", copied.get("line_item_kind") or "material")
        rows.append(copied)
    return pd.DataFrame(rows)


def _row_cost(row: dict[str, Any]) -> float:
    for field in ("estimated_cost", "calculated_cost", "amount", "calculated_output"):
        value = _safe_float(row.get(field))
        if value is not None:
            return value
    return 0.0


def _included_decision_rows(workbench: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section, value in workbench.items():
        if section == "decision_proposals":
            continue
        if not isinstance(value, list):
            continue
        for row in value:
            if not isinstance(row, dict) or not row.get("include"):
                continue
            workbook_row = _decision_row_id(row)
            if not workbook_row:
                continue
            copied = dict(row)
            copied.setdefault("section", section)
            copied["workbook_row"] = workbook_row
            rows.append(copied)
    return rows


def _workbench_diagnostics(workbench: dict[str, Any], expected_rows: set[str]) -> dict[str, Any]:
    rows = _included_decision_rows(workbench)
    actual_rows = {_text(row.get("workbook_row")) for row in rows if _text(row.get("workbook_row"))}
    row_counts = Counter(_text(row.get("workbook_row")) for row in rows if _text(row.get("workbook_row")))
    duplicate_rows = sorted(row for row, count in row_counts.items() if count > 1)
    raw_zero_cost_rows = [
        {
            "workbook_row": row.get("workbook_row"),
            "template_bucket": row.get("template_bucket"),
            "label": row.get("template_line") or row.get("resolved_template_option") or row.get("label"),
            "section": row.get("section"),
        }
        for row in rows
        if _row_cost(row) <= 0 and _norm(row.get("template_bucket")) not in {"sales_tax", "overhead", "profit"}
    ]
    zero_cost_rows = [row for row in raw_zero_cost_rows if _text(row.get("workbook_row")) in expected_rows]
    extra_zero_cost_rows = [row for row in raw_zero_cost_rows if _text(row.get("workbook_row")) not in expected_rows]
    return {
        "actual_included_row_count": len(actual_rows),
        "matched_scoreable_row_count": len(expected_rows & actual_rows),
        "decision_row_overlap_ratio": round(len(expected_rows & actual_rows) / len(expected_rows), 4) if expected_rows else 0.0,
        "missing_scoreable_rows": sorted(expected_rows - actual_rows, key=lambda item: (len(item), item)),
        "extra_actual_rows": sorted(actual_rows - expected_rows, key=lambda item: (len(item), item)),
        "duplicate_decision_row_count": len(duplicate_rows),
        "duplicate_decision_rows": duplicate_rows,
        "included_zero_cost_count": len(zero_cost_rows),
        "included_zero_cost_rows": zero_cost_rows[:25],
        "extra_included_zero_cost_count": len(extra_zero_cost_rows),
        "extra_included_zero_cost_rows": extra_zero_cost_rows[:25],
        "raw_included_zero_cost_count": len(raw_zero_cost_rows),
    }


def _run_estimator_diagnostics(
    notes: str,
    *,
    expected_rows: set[str],
    answer_key_preferences: list[dict[str, Any]] | None = None,
    answer_key_template_type: str = "",
) -> dict[str, Any]:
    original_mapbox_setting = os.environ.get("MAPBOX_ROUTING_ENABLED")
    os.environ["MAPBOX_ROUTING_ENABLED"] = "0"
    recommendation = estimate_from_field_notes(notes, {}, data=EstimatorData())
    try:
        scope_override: dict[str, Any] | None = None
        if answer_key_preferences:
            scope = dict(getattr(recommendation, "parsed_fields", {}) or {})
            if answer_key_template_type:
                scope["template_type"] = answer_key_template_type
                scope["estimate_mode"] = answer_key_template_type
                if answer_key_template_type == "insulation":
                    scope["division"] = "Insulation"
                    scope["project_type"] = "spray foam insulation"
                elif answer_key_template_type == "roofing":
                    scope["division"] = "Roofing"
                    scope.setdefault("project_type", "roofing estimate")
                elif answer_key_template_type == "flooring":
                    scope["division"] = "Flooring"
            scope["estimator_chat"] = {
                "source": "answer_key_audit",
                "workbook_decision_preferences": answer_key_preferences,
            }
            recommendation = replace(recommendation, parsed_fields=scope)
            scope_override = scope
        workbench = build_estimating_workbench(recommendation, EstimatorData(), scope_override=scope_override)
        workbench = recalculate_workbench_tables(workbench)
        return _workbench_diagnostics(workbench, expected_rows)
    finally:
        if original_mapbox_setting is None:
            os.environ.pop("MAPBOX_ROUTING_ENABLED", None)
        else:
            os.environ["MAPBOX_ROUTING_ENABLED"] = original_mapbox_setting


def audit_case(
    case_dir: Path,
    *,
    notes_filename: str = "notes.txt",
    run_estimator: bool = False,
    apply_answer_key: bool = False,
) -> dict[str, Any]:
    source_path = case_dir / "source_decisions.json"
    notes_path = case_dir / notes_filename
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    decisions = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(decisions, list):
        decisions = []
    polluted = []
    actionable_rows = []
    scoreable_rows = []
    for row in decisions:
        if not isinstance(row, dict):
            continue
        if not is_actionable_answer_key_row(row):
            continue
        row_id = _decision_row_id(row)
        actionable_rows.append(row_id)
        reasons = answer_key_pollution_reasons(row)
        if reasons:
            polluted.append(
                {
                    "workbook_row": row_id,
                    "template_bucket": row.get("template_bucket"),
                    "line_item": row.get("resolved_item_name") or row.get("selected_item_name") or row.get("line_item"),
                    "reasons": reasons,
                }
            )
            continue
        scoreable_rows.append(row_id)

    result = {
        "case_id": case_dir.name,
        "source_decision_count": len(decisions),
        "actionable_expected_row_count": len(set(filter(None, actionable_rows))),
        "polluted_expected_row_count": len(polluted),
        "scoreable_expected_row_count": len(set(filter(None, scoreable_rows))),
        "polluted_expected_rows": polluted,
        "notes_path": str(notes_path) if notes_path.exists() else "",
    }
    if run_estimator and notes_path.exists():
        notes = notes_path.read_text(encoding="utf-8")
        scoreable = set(filter(None, scoreable_rows))
        template_type = _case_template_type(case_dir, decisions)
        source_rows = _rows_for_answer_key(decisions, template_type)
        answer_key = build_reference_estimate_answer_key(source_rows, job_context={"template_type": template_type})
        preferences = answer_key_to_workbook_decision_preferences(answer_key)
        normalized_scoreable = {
            str(row.get("workbook_row") or "").strip()
            for row in preferences
            if isinstance(row, dict) and str(row.get("workbook_row") or "").strip()
        }
        if normalized_scoreable:
            result["normalized_scoreable_expected_row_count"] = len(normalized_scoreable)
            scoreable = normalized_scoreable
        result["baseline_estimator"] = _run_estimator_diagnostics(notes, expected_rows=scoreable)
        if apply_answer_key:
            result["answer_key_preference_count"] = len(preferences)
            result["answer_key_applied_estimator"] = _run_estimator_diagnostics(
                notes,
                expected_rows=scoreable,
                answer_key_preferences=preferences,
                answer_key_template_type=template_type,
            )
    return result


def audit_cases(
    cases_dir: Path,
    *,
    case_id: str | None = None,
    notes_filename: str = "notes.txt",
    limit: int = 0,
    run_estimator: bool = False,
    apply_answer_key: bool = False,
) -> list[dict[str, Any]]:
    case_dirs = [path for path in sorted(cases_dir.iterdir()) if path.is_dir() and (path / "source_decisions.json").exists()]
    if case_id:
        case_dirs = [path for path in case_dirs if path.name == case_id]
    if limit > 0:
        case_dirs = case_dirs[:limit]
    return [
        audit_case(path, notes_filename=notes_filename, run_estimator=run_estimator, apply_answer_key=apply_answer_key)
        for path in case_dirs
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit generated estimator answer keys for polluted source decisions.")
    parser.add_argument("--cases-dir", type=Path, default=DEFAULT_CASES_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--case-id")
    parser.add_argument("--notes-filename", default="notes.txt")
    parser.add_argument("--limit", type=int, default=0, help="Maximum cases to audit; 0 means all.")
    parser.add_argument("--run-estimator", action="store_true", help="Also run the current estimator path against each case note.")
    parser.add_argument(
        "--apply-answer-key",
        action="store_true",
        help="When running estimator diagnostics, apply source_decisions as reference answer-key preferences.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    results = audit_cases(
        args.cases_dir,
        case_id=args.case_id,
        notes_filename=args.notes_filename,
        limit=args.limit,
        run_estimator=args.run_estimator,
        apply_answer_key=args.apply_answer_key,
    )
    (args.out_dir / "answer_key_audit.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    pd.DataFrame(
        [
            {
                key: value
                for key, value in row.items()
                if key
                not in {
                    "polluted_expected_rows",
                    "baseline_estimator",
                    "answer_key_applied_estimator",
                }
            }
            for row in results
        ]
    ).to_csv(args.out_dir / "answer_key_audit.csv", index=False)
    polluted_count = sum(int(row.get("polluted_expected_row_count") or 0) for row in results)
    if args.run_estimator:
        baseline_overlap = [
            float((row.get("baseline_estimator") or {}).get("decision_row_overlap_ratio") or 0.0)
            for row in results
        ]
        answer_key_overlap = [
            float((row.get("answer_key_applied_estimator") or {}).get("decision_row_overlap_ratio") or 0.0)
            for row in results
            if row.get("answer_key_applied_estimator")
        ]
        if baseline_overlap:
            print(f"Baseline estimator avg row overlap: {sum(baseline_overlap) / len(baseline_overlap):.3f}")
        if answer_key_overlap:
            print(f"Answer-key-applied avg row overlap: {sum(answer_key_overlap) / len(answer_key_overlap):.3f}")
    print(f"Audited {len(results)} estimator answer keys")
    print(f"Polluted expected rows: {polluted_count}")
    print(f"json: {args.out_dir / 'answer_key_audit.json'}")
    print(f"csv: {args.out_dir / 'answer_key_audit.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
