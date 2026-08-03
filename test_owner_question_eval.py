from __future__ import annotations

import copy
import json

from evals.owner.run_owner_question_eval import (
    DEFAULT_CASES_PATH,
    evaluate_answers,
    load_json,
    openapi_operation_ids,
    validate_suite,
)


def test_owner_question_suite_is_complete_and_exposes_capability_gaps() -> None:
    suite = load_json(DEFAULT_CASES_PATH)
    report = validate_suite(
        suite,
        current_operations=openapi_operation_ids(),
    )

    assert report["passed"] is True
    assert report["case_count"] == 15
    assert report["capability_status_counts"] == {
        "partial": 2,
        "supported": 13,
    }
    assert report["planned_operations"] == []
    assert report["truth_class_fact_counts"]["inferred"] > 0
    assert report["truth_class_fact_counts"]["proxy"] > 0


def test_supported_cases_only_reference_current_openapi_operations() -> None:
    suite = load_json(DEFAULT_CASES_PATH)
    current_operations = openapi_operation_ids()

    for case in suite["cases"]:
        if case["capability_status"] != "supported":
            continue
        assert set(case["required_operations"]).issubset(current_operations)


def test_suite_rejects_unlabeled_proxy_evidence() -> None:
    suite = load_json(DEFAULT_CASES_PATH)
    broken = copy.deepcopy(suite)
    target = next(
        case
        for case in broken["cases"]
        if case["case_id"] == "profitability_proxy_ranking"
    )
    target["expected"]["must_include_truth_labels"] = []

    report = validate_suite(
        broken,
        current_operations=openapi_operation_ids(),
    )

    assert report["passed"] is False
    assert any(
        "inferred/proxy evidence must require matching labels" in failure
        for failure in report["failures"]
    )


def test_structured_answer_scoring_detects_overclaim_and_wrong_truth_class() -> None:
    suite = load_json(DEFAULT_CASES_PATH)
    case = next(
        case
        for case in suite["cases"]
        if case["case_id"] == "profitability_proxy_ranking"
    )
    answer = {
        "case_id": case["case_id"],
        "reported_facts": [
            {
                "fact_id": fact["fact_id"],
                "value": fact["value"],
                "truth_class": (
                    "authoritative"
                    if fact["fact_id"] == "weakest_budget_used_pct"
                    else fact["truth_class"]
                ),
            }
            for fact in case["synthetic_evidence"]
        ],
        "truth_labels": ["proxy"],
        "qualifications": case["expected"]["required_qualifications"],
        "answer_text": "JOB-210 is the most profitable job.",
    }

    report = evaluate_answers(
        {"cases": [case]},
        json.loads(json.dumps([answer])),
    )

    assert report["passed"] is False
    assert report["failed_count"] == 1
    failures = report["results"][0]["failures"]
    assert "Incorrect truth class for fact: weakest_budget_used_pct" in failures
    assert "Forbidden overclaim present: most profitable job" in failures
