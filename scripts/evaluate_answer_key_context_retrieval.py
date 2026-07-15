from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jobscan.estimator.chat_assistant import run_estimator_chat_turn

from scripts.audit_estimator_answer_keys import (
    DEFAULT_CASES_DIR,
    _case_template_type,
    _chat_context_data,
)


DEFAULT_OUT_DIR = Path("output/estimator_generated_cases/context_retrieval_audit")
MODES = ("cold", "hinted", "locked")
VARIANTS = ("identity", "semantic")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _case_dirs(cases_dir: Path, *, case_id: str = "", limit: int = 0) -> list[Path]:
    paths = [
        path
        for path in sorted(cases_dir.iterdir())
        if path.is_dir() and (path / "source_decisions.json").exists() and (path / "notes.txt").exists()
    ]
    if case_id:
        paths = [path for path in paths if path.name == case_id]
    if limit > 0:
        paths = paths[:limit]
    return paths


def _capture_context_provider(captures: list[dict[str, Any]]):
    def provider(messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if len(messages) > 1:
            try:
                payload = json.loads(str(messages[1].get("content") or "{}"))
            except json.JSONDecodeError:
                payload = {}
        captures.append(
            {
                "model": model,
                "prompt_bytes": sum(len(str(message.get("content") or "")) for message in messages),
                "payload": payload,
            }
        )
        return {
            "assistant_message": "Captured estimator context for retrieval audit.",
            "estimator_notes": "\n\n".join(
                str(row.get("content") or "")
                for row in payload.get("conversation", [])
                if isinstance(row, dict) and row.get("role") == "user"
            ),
            "scope_overrides": payload.get("existing_scope") if isinstance(payload.get("existing_scope"), dict) else {},
            "workbook_decision_preferences": [],
            "missing_questions": [],
            "assumptions": [],
            "warnings": [],
            "confidence": 0.0,
        }

    return provider


def _semantic_notes(notes: str) -> str:
    lines = str(notes or "").splitlines()
    output: list[str] = []
    after_marker = False
    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if lower.startswith("field notes reconstructed from historical proposal scope"):
            after_marker = True
            continue
        if not after_marker and (
            lower.startswith("historical proposal/source:")
            or lower.startswith("site address:")
            or (stripped and len(output) == 0)
        ):
            continue
        output.append(line)
    cleaned = "\n".join(output).strip()
    return cleaned or str(notes or "")


def _semantic_context_data(data: Any) -> Any:
    cloned = copy.deepcopy(data)
    frame = getattr(cloned, "template_examples", pd.DataFrame())
    if isinstance(frame, pd.DataFrame) and not frame.empty:
        sanitized = frame.copy()
        for column in ("customer", "job_name", "source_file"):
            if column in sanitized.columns:
                sanitized[column] = ""
        if "scope_summary" in sanitized.columns:
            sanitized["scope_summary"] = sanitized["scope_summary"].fillna("").astype(str).map(_semantic_notes)
        cloned.template_examples = sanitized
    return cloned


def _run_case_mode(
    case_dir: Path,
    *,
    notes: str,
    template_type: str,
    data: Any,
    mode: str,
    variant: str,
) -> dict[str, Any]:
    captures: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "data": data,
        "provider": _capture_context_provider(captures),
        "model": "context-retrieval-audit",
    }
    if mode in {"hinted", "locked"}:
        kwargs["template_type_hint"] = template_type
    if variant == "identity":
        kwargs["existing_scope"] = {"allow_identity_retrieval": True}
    if mode == "locked":
        kwargs["existing_scope"] = {
            **(kwargs.get("existing_scope") or {}),
            "template_type": template_type,
            "division": "Roofing" if template_type == "roofing" else template_type.title(),
            "project_type": "roofing estimate" if template_type == "roofing" else template_type,
            "estimate_mode": template_type,
            "template_type_locked": True,
        }
    original_mapbox_setting = os.environ.get("MAPBOX_ROUTING_ENABLED")
    os.environ["MAPBOX_ROUTING_ENABLED"] = "0"
    try:
        run_estimator_chat_turn([{"role": "user", "content": notes}], **kwargs)
    finally:
        if original_mapbox_setting is None:
            os.environ.pop("MAPBOX_ROUTING_ENABLED", None)
        else:
            os.environ["MAPBOX_ROUTING_ENABLED"] = original_mapbox_setting
    payload = captures[-1]["payload"] if captures else {}
    context = payload.get("estimator_context") if isinstance(payload.get("estimator_context"), dict) else {}
    matched = (
        ((context.get("historical_answer_key_examples") or {}).get("matched_answer_keys") or [])
        if isinstance(context.get("historical_answer_key_examples"), dict)
        else []
    )
    case_id = case_dir.name
    self_rank = 0
    for index, match in enumerate(matched, start=1):
        if not isinstance(match, dict):
            continue
        if str(match.get("job_id") or "") == case_id or str(match.get("example_id") or "") == case_id:
            self_rank = index
            break
    cues = context.get("historical_answer_key_decision_cues") if isinstance(context.get("historical_answer_key_decision_cues"), list) else []
    self_cue_count = 0
    for cue in cues:
        if not isinstance(cue, dict):
            continue
        examples = cue.get("examples") if isinstance(cue.get("examples"), list) else []
        if any(isinstance(example, dict) and str(example.get("job_id") or "") == case_id for example in examples):
            self_cue_count += 1
    top_matches = [
        {
            "rank": index,
            "job_id": match.get("job_id"),
            "job_name": match.get("job_name"),
            "template_type": match.get("template_type"),
            "similarity_score": match.get("similarity_score"),
            "match_reasons": match.get("match_reasons") or [],
            "decision_count_sent": len((((match.get("reference_answer_key") or {}).get("decisions") or []))),
        }
        for index, match in enumerate(matched[:5], start=1)
        if isinstance(match, dict)
    ]
    return {
        "case_id": case_id,
        "mode": mode,
        "variant": variant,
        "expected_template_type": template_type,
        "context_template_type": context.get("template_type"),
        "template_type_match": (context.get("template_type") == template_type),
        "self_rank": self_rank,
        "hit_top_1": self_rank == 1,
        "hit_top_3": 0 < self_rank <= 3,
        "hit_top_5": 0 < self_rank <= 5,
        "matched_count": len(matched),
        "decision_cue_count": len(cues),
        "self_decision_cue_count": self_cue_count,
        "candidate_count": ((context.get("historical_answer_key_examples") or {}).get("retrieval") or {}).get("candidate_count")
        if isinstance(context.get("historical_answer_key_examples"), dict)
        else None,
        "prompt_bytes": captures[-1]["prompt_bytes"] if captures else 0,
        "top_matches": top_matches,
    }


def evaluate_retrieval(
    cases_dir: Path,
    *,
    case_id: str = "",
    notes_filename: str = "notes.txt",
    limit: int = 0,
    modes: list[str] | None = None,
    variants: list[str] | None = None,
    context_source: str = "cases",
    database_url: str = "",
    chat_load_profile: str = "chat",
) -> list[dict[str, Any]]:
    data = _chat_context_data(
        cases_dir,
        source=context_source,
        database_url=database_url,
        load_profile=chat_load_profile,
    )
    selected_modes = modes or list(MODES)
    selected_variants = variants or list(VARIANTS)
    data_by_variant = {
        "identity": data,
        "semantic": _semantic_context_data(data),
    }
    results: list[dict[str, Any]] = []
    for case_dir in _case_dirs(cases_dir, case_id=case_id, limit=limit):
        decisions = _load_json(case_dir / "source_decisions.json")
        if not isinstance(decisions, list):
            decisions = []
        template_type = _case_template_type(case_dir, decisions)
        notes_path = case_dir / notes_filename
        if not notes_path.exists():
            continue
        original_notes = notes_path.read_text(encoding="utf-8")
        for variant in selected_variants:
            notes = _semantic_notes(original_notes) if variant == "semantic" else original_notes
            for mode in selected_modes:
                results.append(
                    _run_case_mode(
                        case_dir,
                        notes=notes,
                        template_type=template_type,
                        data=data_by_variant.get(variant, data),
                        mode=mode,
                        variant=variant,
                    )
                )
    return results


def _summary(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    variants = [variant for variant in VARIANTS if any(row.get("variant") == variant for row in results)]
    for variant in variants:
        for mode in MODES:
            subset = [row for row in results if row.get("variant") == variant and row.get("mode") == mode]
            if not subset:
                continue
            count = len(subset)
            valid_subset = [row for row in subset if row.get("template_type_match")]
            valid_count = len(valid_subset)
            ranks = [int(row.get("self_rank") or 0) for row in subset]
            misses = [row for row in subset if not row.get("hit_top_5")]
            rows.append(
                {
                    "variant": variant,
                    "mode": mode,
                    "cases": count,
                    "template_type_mismatches": count - valid_count,
                    "top1_rate": round(sum(1 for row in subset if row.get("hit_top_1")) / count, 3),
                    "top3_rate": round(sum(1 for row in subset if row.get("hit_top_3")) / count, 3),
                    "top5_rate": round(sum(1 for row in subset if row.get("hit_top_5")) / count, 3),
                    "valid_top1_rate": round(sum(1 for row in valid_subset if row.get("hit_top_1")) / valid_count, 3) if valid_count else 0.0,
                    "valid_top3_rate": round(sum(1 for row in valid_subset if row.get("hit_top_3")) / valid_count, 3) if valid_count else 0.0,
                    "valid_top5_rate": round(sum(1 for row in valid_subset if row.get("hit_top_5")) / valid_count, 3) if valid_count else 0.0,
                    "missing_top5": len(misses),
                    "avg_rank_when_found": round(sum(rank for rank in ranks if rank > 0) / max(sum(1 for rank in ranks if rank > 0), 1), 2),
                    "avg_decision_cues": round(sum(int(row.get("decision_cue_count") or 0) for row in subset) / count, 1),
                    "avg_self_decision_cues": round(sum(int(row.get("self_decision_cue_count") or 0) for row in subset) / count, 1),
                }
            )
    return rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate whether generated field notes retrieve their own historical answer key in estimator chat context.")
    parser.add_argument("--cases-dir", type=Path, default=DEFAULT_CASES_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--case-id")
    parser.add_argument("--notes-filename", default="notes.txt")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    parser.add_argument("--context-source", choices=["cases", "database", "empty"], default="cases")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL") or "")
    parser.add_argument("--chat-load-profile", default="chat", choices=["chat", "interactive", "full"])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    results = evaluate_retrieval(
        args.cases_dir,
        case_id=args.case_id or "",
        notes_filename=args.notes_filename,
        limit=args.limit,
        modes=list(args.modes),
        variants=list(args.variants),
        context_source=args.context_source,
        database_url=args.database_url,
        chat_load_profile=args.chat_load_profile,
    )
    summary = _summary(results)
    (args.out_dir / "context_retrieval_audit.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    (args.out_dir / "context_retrieval_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    flat_rows = []
    for row in results:
        copied = dict(row)
        copied.pop("top_matches", None)
        flat_rows.append(copied)
    pd.DataFrame(flat_rows).to_csv(args.out_dir / "context_retrieval_audit.csv", index=False)
    pd.DataFrame(summary).to_csv(args.out_dir / "context_retrieval_summary.csv", index=False)
    for row in summary:
        print(
            f"{row['variant']}/{row['mode']}: top1={row['top1_rate']:.3f} top3={row['top3_rate']:.3f} "
            f"top5={row['top5_rate']:.3f} missing_top5={row['missing_top5']} "
            f"valid_top5={row['valid_top5_rate']:.3f} "
            f"template_mismatch={row['template_type_mismatches']} avg_rank={row['avg_rank_when_found']:.2f}"
        )
    print(f"json: {args.out_dir / 'context_retrieval_audit.json'}")
    print(f"summary: {args.out_dir / 'context_retrieval_summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
