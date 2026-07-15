from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")

import dashboard.app as app


DEFAULT_PROMPTS = [
    "Which active jobs are behind or at risk?",
    "What jobs are ready to schedule?",
    "Which proposals need follow-up?",
    "Show me job tracking notes for Pegasus 39 Pearce.",
    "What did Carlos touch this week?",
    "Find roofing jobs that required coating and foam.",
    "For roofing jobs with Gaco silicone and primer, what labor rows were usually included?",
    "When we include SPF roof foam and coating, what companion materials and labor usually show up?",
]


def _text(value: Any) -> str:
    return app.text_value(value)


def _row_counts(evidence: dict[str, Any]) -> dict[str, int]:
    facts = evidence.get("facts") if isinstance(evidence.get("facts"), dict) else {}
    return {key: len(value) for key, value in facts.items() if isinstance(value, list)}


def _answer_from_evidence(
    prompt: str,
    chunks: list[dict[str, Any]],
    structured_evidence: dict[str, Any],
    *,
    use_ai: bool,
) -> str:
    if use_ai:
        return app.llm_grounded_document_answer(prompt, chunks, structured_evidence)
    return app.fallback_document_answer(prompt, chunks, structured_evidence)


def _quality_checks(answer: str, evidence_counts: dict[str, int], *, use_ai: bool) -> dict[str, Any]:
    normalized = answer.lower()
    return {
        "has_answer": bool(answer.strip()),
        "has_answer_heading": "answer" in normalized,
        "has_key_details_heading": "key details" in normalized,
        "has_checked_sources": "what i checked" in normalized
        or "data checked" in normalized
        or any(source.lower() in normalized for source in evidence_counts),
        "has_gap_language": "gap" in normalized or "missing" in normalized or "uncertain" in normalized,
        "has_markdown_table": "|" in answer and "---" in answer,
        "uses_ai": use_ai,
        "answer_chars": len(answer),
    }


def _safe_debug_payload(debug: dict[str, Any]) -> dict[str, Any]:
    payload = dict(debug)
    if "structured_evidence" in payload:
        payload["structured_evidence"] = _row_counts(payload["structured_evidence"])
    return payload


def answer_prompt(prompt: str, *, use_ai: bool, max_rows: int) -> dict[str, Any]:
    started = time.perf_counter()
    interpreted = app.interpret_search_request(prompt)
    plan = app.plan_ask_spraytec_query(prompt, interpreted)
    targets = set(plan.get("targets") or [])
    plan_targets = targets & app.ASK_SPRAYTEC_STRUCTURED_TARGETS
    debug: dict[str, Any] = {
        "interpreted": interpreted,
        "query_plan": plan,
        "document_matches": [],
        "document_chunks": [],
        "job_matches": [],
        "attribute_results": [],
        "structured_evidence": {},
    }
    answer = ""
    document_matches: list[dict[str, Any]] = []
    document_chunks: list[dict[str, Any]] = []
    structured_evidence: dict[str, Any] = {}
    mode = _text(plan.get("mode"))

    if mode == "generated_field_notes":
        return {
            "prompt": prompt,
            "status": "skipped",
            "skip_reason": "generated_field_notes mode is intentionally not run by this Ask answer harness",
            "query_plan": plan,
            "interpreted": interpreted,
            "seconds": round(time.perf_counter() - started, 3),
        }

    try:
        with app.get_engine().connect() as conn:
            if mode == "attribute_job_search":
                attribute_query = plan.get("attribute_query") if isinstance(plan.get("attribute_query"), dict) else {}
                attribute_results = app.search_jobs_by_estimate_attributes(
                    conn,
                    concepts=list(attribute_query.get("concepts") or []),
                    interpreted=interpreted,
                    attribute_query=attribute_query,
                    limit=max_rows,
                )
                answer = app.attribute_job_search_response(attribute_results, attribute_query)
                debug["attribute_results"] = [
                    {
                        "job_id": result.get("job_id"),
                        "customer": result.get("customer"),
                        "job_name": result.get("job_name"),
                        "matched_concepts": result.get("matched_concepts"),
                        "evidence_count": result.get("match_evidence_count"),
                        "score": result.get("match_score"),
                    }
                    for result in attribute_results[:max_rows]
                ]
            else:
                job_results: list[dict[str, Any]] = []
                if "documents" in targets and interpreted.get("search_text"):
                    document_matches = app.search_documents(
                        conn,
                        str(interpreted.get("search_text") or ""),
                        document_type=interpreted.get("document_type"),
                        limit=max_rows,
                    )
                    if document_matches and "document_content" in targets:
                        document_chunks = app.fetch_document_content_chunks(
                            conn,
                            query=prompt,
                            document_ids=[_text(doc.get("document_id")) for doc in document_matches],
                            document_type=interpreted.get("document_type"),
                            limit=app.ASK_DOCUMENT_CHUNK_LIMIT,
                        )
                matched_job_ids = [_text(doc.get("job_id")) for doc in document_matches if _text(doc.get("job_id"))]
                job_lookup_query = _text(interpreted.get("search_text")) or prompt
                if "jobs" in targets:
                    job_results = app.search_jobs(conn, job_lookup_query, limit=min(max_rows, 10))
                matched_job_ids.extend(_text(result.get("job_id")) for result in job_results[:3] if _text(result.get("job_id")))
                matched_job_ids = list(dict.fromkeys(job_id for job_id in matched_job_ids if job_id))
                structured_evidence = app.build_structured_evidence_pack(
                    conn,
                    query=prompt,
                    interpreted=interpreted,
                    job_ids=matched_job_ids,
                    targets=plan_targets,
                    max_rows=max_rows,
                )
                if document_matches:
                    if document_chunks or structured_evidence.get("facts"):
                        answer = _answer_from_evidence(prompt, document_chunks, structured_evidence, use_ai=use_ai)
                        answer += "\n\nIndexed document links:\n"
                        answer += "\n".join(app.indexed_document_markdown(doc) for doc in document_matches[:12])
                    else:
                        answer = app.indexed_documents_response(document_matches, interpreted=interpreted, query=prompt)
                        answer += "\n\nI found matching document metadata, but no extracted text chunks were available to summarize."
                elif plan.get("use_llm_answer") and structured_evidence.get("facts"):
                    answer = _answer_from_evidence(prompt, [], structured_evidence, use_ai=use_ai)
                    guidance_answer = bool((structured_evidence.get("facts") or {}).get("historical_estimate_guidance"))
                    related = [] if guidance_answer else job_results[:3]
                    if related:
                        answer += "\n\nRelated job matches:\n"
                        answer += "\n\n".join(
                            f"{index}. " + app.job_result_markdown(job, interpreted, include_documents=True, connection=None)
                            for index, job in enumerate(related, start=1)
                        )
                elif "jobs" not in targets and mode == "structured_answer":
                    requested_sources = ", ".join(target for target in plan.get("targets", []) if target in app.ASK_SPRAYTEC_STRUCTURED_TARGETS)
                    answer = (
                        "I did not find matching structured records for that question"
                        + (f" in {requested_sources}." if requested_sources else ".")
                    )
                else:
                    answer = app.concise_job_candidates_response(job_results, interpreted)
                debug["document_matches"] = [
                    {
                        "document_id": doc.get("document_id"),
                        "job_id": doc.get("job_id"),
                        "document_type": doc.get("document_type"),
                        "file_name": doc.get("file_name"),
                    }
                    for doc in document_matches[:max_rows]
                ]
                debug["document_chunks"] = [
                    {
                        "source": app.source_label_for_chunk(chunk, index),
                        "document_id": chunk.get("document_id"),
                        "job_id": chunk.get("job_id"),
                        "file_name": chunk.get("file_name"),
                    }
                    for index, chunk in enumerate(document_chunks[:10], start=1)
                ]
                debug["job_matches"] = [
                    {
                        "job_id": result.get("job_id"),
                        "customer": result.get("customer"),
                        "job_name": result.get("job_name"),
                        "score": result.get("match_score"),
                        "reason": result.get("match_reason"),
                    }
                    for result in job_results[:max_rows]
                ]
                debug["structured_evidence"] = structured_evidence
    except Exception as exc:
        answer = f"Ask Spray-Tec evaluation failed: {app.safe_exception_text(exc)}"
        status = "error"
    else:
        status = "ok"

    evidence_counts = _row_counts(structured_evidence)
    checks = _quality_checks(answer, evidence_counts, use_ai=use_ai)
    return {
        "prompt": prompt,
        "status": status,
        "seconds": round(time.perf_counter() - started, 3),
        "mode": mode,
        "targets": sorted(targets),
        "interpreted": interpreted,
        "query_plan": plan,
        "evidence_counts": evidence_counts,
        "document_match_count": len(document_matches),
        "document_chunk_count": len(document_chunks),
        "checks": checks,
        "answer": answer,
        "answer_excerpt": answer[:1000],
        "debug": _safe_debug_payload(debug),
    }


def synthetic_result(*, use_ai: bool) -> dict[str, Any]:
    prompt = "Which active jobs are behind or at risk?"
    evidence = {
        "facts": {
            "operations_dashboard": [
                {
                    "project": "Example Roof Restoration",
                    "customer": "Example Customer",
                    "project_health": "Behind expected progress",
                    "operations_value": 125000,
                    "estimated_start_date": "2026-07-01",
                    "estimated_end_date": "2026-07-10",
                    "actual_labor_hours": 180,
                    "estimated_labor_hours": 140,
                }
            ],
            "job_tracking_summary": [
                {
                    "job_name": "Example Roof Restoration",
                    "actual_labor_hours": 180,
                    "estimated_labor_hours": 140,
                    "labor_hours_variance": 40,
                    "tracking_notes": "Example notes: detail work took longer than planned.",
                }
            ],
        }
    }
    started = time.perf_counter()
    answer = _answer_from_evidence(prompt, [], evidence, use_ai=use_ai)
    counts = _row_counts(evidence)
    return {
        "prompt": prompt,
        "status": "ok",
        "seconds": round(time.perf_counter() - started, 3),
        "mode": "synthetic",
        "targets": sorted(counts),
        "evidence_counts": counts,
        "document_match_count": 0,
        "document_chunk_count": 0,
        "checks": _quality_checks(answer, counts, use_ai=use_ai),
        "answer": answer,
        "answer_excerpt": answer[:1000],
        "debug": {"structured_evidence": counts},
    }


def load_prompts(args: argparse.Namespace) -> list[str]:
    prompts: list[str] = []
    for prompt in args.prompt or []:
        if prompt.strip():
            prompts.append(prompt.strip())
    if args.prompt_file:
        path = Path(args.prompt_file)
        prompts.extend(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    if args.all_samples:
        prompts.extend(question for questions in app.ASK_SPRAYTEC_SAMPLE_QUESTIONS.values() for question in questions)
    if not prompts:
        prompts = list(DEFAULT_PROMPTS)
    if args.limit > 0:
        prompts = prompts[: args.limit]
    return prompts


def write_outputs(results: list[dict[str, Any]], out_dir: Path) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "ask_spraytec_eval.jsonl"
    json_path = out_dir / "ask_spraytec_eval.json"
    csv_path = out_dir / "ask_spraytec_eval_summary.csv"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    fieldnames = [
        "prompt",
        "status",
        "seconds",
        "mode",
        "targets",
        "evidence_counts",
        "document_match_count",
        "document_chunk_count",
        "has_answer",
        "has_checked_sources",
        "has_markdown_table",
        "answer_chars",
        "answer_excerpt",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            checks = result.get("checks") if isinstance(result.get("checks"), dict) else {}
            writer.writerow(
                {
                    "prompt": result.get("prompt"),
                    "status": result.get("status"),
                    "seconds": result.get("seconds"),
                    "mode": result.get("mode"),
                    "targets": ", ".join(result.get("targets") or []),
                    "evidence_counts": json.dumps(result.get("evidence_counts") or {}, sort_keys=True),
                    "document_match_count": result.get("document_match_count"),
                    "document_chunk_count": result.get("document_chunk_count"),
                    "has_answer": checks.get("has_answer"),
                    "has_checked_sources": checks.get("has_checked_sources"),
                    "has_markdown_table": checks.get("has_markdown_table"),
                    "answer_chars": checks.get("answer_chars"),
                    "answer_excerpt": result.get("answer_excerpt"),
                }
            )
    return jsonl_path, json_path, csv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Ask Spray-Tec prompt routing, retrieval, and answer formatting.")
    parser.add_argument("--prompt", action="append", help="Prompt to evaluate. Can be provided multiple times.")
    parser.add_argument("--prompt-file", help="Text file with one prompt per line.")
    parser.add_argument("--all-samples", action="store_true", help="Run every sample question shown in the Ask Spray-Tec UI.")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of prompts after loading prompt sources.")
    parser.add_argument("--max-rows", type=int, default=12, help="Max rows per structured evidence table.")
    parser.add_argument("--out-dir", default="output/ask_spraytec_eval", help="Output directory for JSONL/JSON/CSV results.")
    parser.add_argument("--use-ai", action="store_true", help="Use OpenAI for answer synthesis. This may send retrieved operational evidence to OpenAI.")
    parser.add_argument("--synthetic", action="store_true", help="Run a synthetic no-private-data prompt to test answer formatting and API availability.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.use_ai and not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set. Load .env or run without --use-ai.")
    if args.use_ai:
        print("AI answer synthesis enabled. Retrieved evidence for real prompts may be sent to OpenAI.")
    if args.synthetic:
        results = [synthetic_result(use_ai=args.use_ai)]
    else:
        prompts = load_prompts(args)
        results = [answer_prompt(prompt, use_ai=args.use_ai, max_rows=args.max_rows) for prompt in prompts]
    jsonl_path, json_path, csv_path = write_outputs(results, Path(args.out_dir))
    ok_count = sum(1 for result in results if result.get("status") == "ok")
    print(f"Evaluated {len(results)} Ask Spray-Tec prompt(s); ok={ok_count}")
    print(f"jsonl: {jsonl_path}")
    print(f"json: {json_path}")
    print(f"csv: {csv_path}")
    for result in results:
        counts = result.get("evidence_counts") or {}
        print(
            f"- {result.get('status')} {result.get('seconds')}s | {result.get('mode')} | "
            f"{result.get('prompt')} | evidence={counts} | docs={result.get('document_match_count')}/{result.get('document_chunk_count')}"
        )


if __name__ == "__main__":
    main()
