from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DEFAULT_CASES_PATH = Path(__file__).with_name("owner_questions.json")
DEFAULT_OPENAPI_PATH = REPO_ROOT / "services" / "estimator_api" / "openapi.json"
ALLOWED_TRUTH_CLASSES = {"authoritative", "inferred", "proxy"}
ALLOWED_CAPABILITY_STATUSES = {"supported", "partial", "planned"}
REQUIRED_CATEGORIES = {
    "executive_briefing",
    "sales",
    "operations",
    "scheduling",
    "production",
    "production_cost",
    "profitability",
    "office",
    "office_data_quality",
    "data_quality",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def openapi_operation_ids(path: Path = DEFAULT_OPENAPI_PATH) -> set[str]:
    document = load_json(path)
    return {
        str(operation["operationId"])
        for methods in document.get("paths", {}).values()
        for method, operation in methods.items()
        if method.lower() in {"get", "post", "put", "patch", "delete"}
        and isinstance(operation, dict)
        and operation.get("operationId")
    }


def validate_suite(
    suite: dict[str, Any],
    *,
    current_operations: set[str],
) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    cases = suite.get("cases")
    if not isinstance(cases, list):
        return {
            "passed": False,
            "failures": ["Suite cases must be a list."],
            "warnings": [],
        }
    if len(cases) != 15:
        failures.append(f"Expected exactly 15 owner questions; found {len(cases)}.")

    case_ids = [str(case.get("case_id") or "") for case in cases]
    duplicates = sorted(
        case_id for case_id, count in Counter(case_ids).items() if count > 1
    )
    if "" in case_ids:
        failures.append("Every case must have a nonblank case_id.")
    if duplicates:
        failures.append(f"Duplicate case IDs: {', '.join(duplicates)}")

    categories: set[str] = set()
    status_counts: Counter[str] = Counter()
    truth_counts: Counter[str] = Counter()
    planned_operations: set[str] = set()
    for case in cases:
        case_id = str(case.get("case_id") or "(unknown)")
        question = str(case.get("question") or "").strip()
        if not question.endswith("?"):
            failures.append(f"{case_id}: question must end with a question mark.")
        category = str(case.get("category") or "")
        categories.add(category)
        status = str(case.get("capability_status") or "")
        status_counts[status] += 1
        if status not in ALLOWED_CAPABILITY_STATUSES:
            failures.append(f"{case_id}: invalid capability_status {status!r}.")

        operations = {
            str(operation)
            for operation in case.get("required_operations", [])
            if str(operation).strip()
        }
        if not operations:
            failures.append(f"{case_id}: at least one required operation is needed.")
        missing_operations = operations - current_operations
        if status == "supported" and missing_operations:
            failures.append(
                f"{case_id}: supported case requires unavailable operations: "
                f"{', '.join(sorted(missing_operations))}"
            )
        planned_operations.update(missing_operations)

        evidence = case.get("synthetic_evidence")
        if not isinstance(evidence, list) or not evidence:
            failures.append(f"{case_id}: synthetic_evidence must be a nonempty list.")
            continue
        fact_ids = [str(fact.get("fact_id") or "") for fact in evidence]
        if len(fact_ids) != len(set(fact_ids)):
            failures.append(f"{case_id}: synthetic fact IDs must be unique.")
        for fact in evidence:
            fact_id = str(fact.get("fact_id") or "(missing)")
            truth_class = str(fact.get("truth_class") or "")
            truth_counts[truth_class] += 1
            if truth_class not in ALLOWED_TRUTH_CLASSES:
                failures.append(
                    f"{case_id}/{fact_id}: invalid truth_class {truth_class!r}."
                )
            if not str(fact.get("source_operation") or "").strip():
                failures.append(f"{case_id}/{fact_id}: source_operation is required.")
            if not str(fact.get("source_table") or "").strip():
                failures.append(f"{case_id}/{fact_id}: source_table is required.")

        expected = case.get("expected") or {}
        required_fact_ids = set(expected.get("must_report_fact_ids") or [])
        unknown_required = required_fact_ids - set(fact_ids)
        if unknown_required:
            failures.append(
                f"{case_id}: expected facts are absent from synthetic evidence: "
                f"{', '.join(sorted(unknown_required))}"
            )
        required_labels = set(expected.get("must_include_truth_labels") or [])
        case_truth_classes = {
            str(fact.get("truth_class") or "") for fact in evidence
        }
        if not required_labels.issubset(case_truth_classes):
            failures.append(
                f"{case_id}: required truth labels do not occur in the evidence."
            )
        sensitive_truth = case_truth_classes & {"inferred", "proxy"}
        if sensitive_truth:
            if not sensitive_truth.issubset(required_labels):
                failures.append(
                    f"{case_id}: inferred/proxy evidence must require matching labels."
                )
            if not expected.get("required_qualifications"):
                failures.append(
                    f"{case_id}: inferred/proxy evidence needs a qualification."
                )
            if not expected.get("must_not_claim"):
                failures.append(
                    f"{case_id}: inferred/proxy evidence needs forbidden overclaims."
                )

    missing_categories = REQUIRED_CATEGORIES - categories
    if missing_categories:
        failures.append(
            "Suite is missing owner-question categories: "
            + ", ".join(sorted(missing_categories))
        )
    if status_counts["supported"] == 0:
        warnings.append("No questions are currently marked supported.")
    if status_counts["planned"] == 0:
        warnings.append("No questions expose planned capability gaps.")

    return {
        "passed": not failures,
        "suite_id": suite.get("suite_id"),
        "case_count": len(cases),
        "capability_status_counts": dict(sorted(status_counts.items())),
        "truth_class_fact_counts": dict(sorted(truth_counts.items())),
        "current_operation_count": len(current_operations),
        "planned_operations": sorted(planned_operations),
        "failures": failures,
        "warnings": warnings,
    }


def _values_match(expected: Any, actual: Any) -> bool:
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(expected) - float(actual)) <= 1e-6
    return expected == actual


def evaluate_answers(
    suite: dict[str, Any],
    answers: list[dict[str, Any]],
) -> dict[str, Any]:
    cases = {
        str(case.get("case_id")): case for case in suite.get("cases", [])
    }
    answers_by_id = {
        str(answer.get("case_id")): answer for answer in answers
    }
    results: list[dict[str, Any]] = []
    for case_id, case in cases.items():
        answer = answers_by_id.get(case_id)
        if answer is None:
            results.append(
                {
                    "case_id": case_id,
                    "passed": False,
                    "failures": ["No answer record supplied."],
                }
            )
            continue
        failures: list[str] = []
        expected = case.get("expected") or {}
        evidence_by_id = {
            str(fact.get("fact_id")): fact
            for fact in case.get("synthetic_evidence", [])
        }
        reported = {
            str(fact.get("fact_id")): fact
            for fact in answer.get("reported_facts", [])
        }
        for fact_id in expected.get("must_report_fact_ids") or []:
            if fact_id not in reported:
                failures.append(f"Missing required fact: {fact_id}")
                continue
            expected_fact = evidence_by_id[fact_id]
            actual_fact = reported[fact_id]
            if not _values_match(expected_fact.get("value"), actual_fact.get("value")):
                failures.append(f"Incorrect value for fact: {fact_id}")
            if expected_fact.get("truth_class") != actual_fact.get("truth_class"):
                failures.append(f"Incorrect truth class for fact: {fact_id}")

        truth_labels = set(answer.get("truth_labels") or [])
        required_labels = set(expected.get("must_include_truth_labels") or [])
        missing_labels = required_labels - truth_labels
        if missing_labels:
            failures.append(
                "Missing truth labels: " + ", ".join(sorted(missing_labels))
            )
        qualifications = answer.get("qualifications") or []
        if len(qualifications) < len(expected.get("required_qualifications") or []):
            failures.append("Required qualifications were not fully represented.")
        answer_text = str(answer.get("answer_text") or "").lower()
        for forbidden in expected.get("must_not_claim") or []:
            if str(forbidden).lower() in answer_text:
                failures.append(f"Forbidden overclaim present: {forbidden}")
        results.append(
            {
                "case_id": case_id,
                "passed": not failures,
                "failures": failures,
            }
        )
    passed_count = sum(bool(result["passed"]) for result in results)
    return {
        "passed": passed_count == len(results),
        "case_count": len(results),
        "passed_count": passed_count,
        "failed_count": len(results) - passed_count,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the Spray-Tec owner-question evaluation suite."
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--openapi", type=Path, default=DEFAULT_OPENAPI_PATH)
    parser.add_argument(
        "--answers",
        type=Path,
        help="Optional structured answer records to score against the suite.",
    )
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    suite = load_json(args.cases)
    report: dict[str, Any] = {
        "suite_validation": validate_suite(
            suite,
            current_operations=openapi_operation_ids(args.openapi),
        )
    }
    if args.answers:
        answers = load_json(args.answers)
        if isinstance(answers, dict):
            answers = answers.get("answers", [])
        report["answer_evaluation"] = evaluate_answers(suite, answers)

    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    passed = report["suite_validation"]["passed"]
    if "answer_evaluation" in report:
        passed = passed and report["answer_evaluation"]["passed"]
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
