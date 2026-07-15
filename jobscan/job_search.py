from __future__ import annotations

import argparse
import json
import os
import re
import string
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

LEGAL_SUFFIXES = {"inc", "llc", "company", "co", "corporation", "corp"}
DIVISIONS = {"roofing": "Roofing", "flooring": "Flooring", "insulation": "Insulation", "specialty": "Specialty"}
STATUS_TERMS = {
    "proposed": "Proposed",
    "contracted": "Contracted",
    "completed": "Completed",
    "complete": "Completed",
    "invoiced": "Invoiced",
}
DOCUMENT_KEYWORDS = {
    "estimate": "estimate",
    "estimates": "estimate",
    "proposal": "proposal",
    "proposals": "proposal",
    "quote": "proposal",
    "quotes": "proposal",
    "bid": "proposal",
    "bids": "proposal",
    "contract": "contract",
    "contracts": "contract",
    "invoice": "invoice",
    "invoices": "invoice",
    "warranty": "warranty",
    "warranties": "warranty",
    "aerial": "aerial",
    "drone": "aerial",
    "eagleview": "aerial",
    "tracking": "job_tracking",
    "job tracking": "job_tracking",
    "tracking form": "job_tracking",
    "folder": "folder",
    "file": "all",
    "files": "all",
    "documents": "all",
    "docs": "all",
}
DOC_FIELD_MAP = {
    "folder": [("folder_url", "Job folder")],
    "proposal": [("proposal_url", "Proposal")],
    "estimate": [("estimate_url", "Estimate"), ("primary_doc_link", "Primary document")],
    "contract": [("contract_url", "Contract")],
    "invoice": [("invoice_url", "Invoice")],
    "job_tracking": [("job_tracking_url", "Job tracking form")],
    "warranty": [("warranty_url", "Warranty")],
    "aerial": [("aerial_url", "Aerial report")],
}
DOCUMENT_LABELS = {
    "folder": "Job folder",
    "proposal": "Proposal",
    "estimate": "Estimate",
    "contract": "Contract",
    "invoice": "Invoice",
    "job_tracking": "Job tracking form",
    "warranty": "Warranty",
    "aerial": "Aerial report",
    "all": "Documents",
}
ALL_DOCUMENT_FIELDS = [
    ("folder_url", "Job folder"),
    ("primary_doc_link", "Primary document"),
    ("proposal_url", "Proposal"),
    ("estimate_url", "Estimate"),
    ("contract_url", "Contract"),
    ("invoice_url", "Invoice"),
    ("job_tracking_url", "Job tracking form"),
    ("warranty_url", "Warranty"),
    ("aerial_url", "Aerial report"),
]
DOCUMENT_LINK_COLUMNS = {field for field, _label in ALL_DOCUMENT_FIELDS} | {"primary_doc_type", "primary_doc_name"}
SEARCH_COLUMNS = [
    "job_id",
    "customer",
    "job_name",
    "site_address",
    "city",
    "state",
    "folder_name",
    "folder_path",
    "estimate_file",
    "invoice_file",
    "primary_doc_name",
    "primary_doc_link",
    "proposal_url",
    "estimate_url",
    "contract_url",
    "invoice_url",
    "job_tracking_url",
    "warranty_url",
    "aerial_url",
]
JOB_COLUMNS = [
    "job_id",
    "customer",
    "job_name",
    "division",
    "pipeline_status",
    "status",
    "site_address",
    "city",
    "state",
    "folder_name",
    "folder_path",
    "folder_url",
    "primary_doc_link",
    "primary_doc_type",
    "primary_doc_name",
    "proposal_url",
    "estimate_url",
    "contract_url",
    "invoice_url",
    "job_tracking_url",
    "warranty_url",
    "aerial_url",
    "estimate_file",
    "invoice_file",
    "warnings",
]


@dataclass
class RankedJob:
    row: dict[str, Any]
    score: float
    reason: str


def normalize_search_text(value: object) -> str:
    text_value = str(value or "").lower()
    text_value = text_value.replace("’", "'").replace("‘", "'").replace("`", "'")
    text_value = re.sub(r"\b([a-z0-9]+)'s\b", r"\1", text_value)
    translation = str.maketrans({char: " " for char in string.punctuation if char != "#"})
    text_value = text_value.translate(translation)
    text_value = re.sub(r"\bcsi\b", "canadian solar csi", text_value)
    words = [word for word in text_value.split() if word not in LEGAL_SUFFIXES]
    return " ".join(words)


def tokenize_search_text(value: object) -> list[str]:
    normalized = normalize_search_text(value)
    return [word for word in normalized.split() if word]


def first_nonblank(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        text_value = str(value).strip()
        if text_value and text_value.lower() not in {"nan", "none", "null", "-"}:
            return text_value
    return ""


def interpret_search_request(query: str) -> dict[str, Any]:
    raw = query.strip()
    normalized = normalize_search_text(raw)
    document_type = None
    for phrase, doc_type in sorted(DOCUMENT_KEYWORDS.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", normalized):
            document_type = doc_type
            break
    division = next((label for key, label in DIVISIONS.items() if re.search(rf"\b{key}\b", normalized)), None)
    status = next((label for key, label in STATUS_TERMS.items() if re.search(rf"\b{key}\b", normalized)), None)
    state_match = re.search(r"\b(ky|in|oh|tn|il)\b", normalized)
    city = None
    city_match = re.search(r"\bin\s+([a-z][a-z\s]+)$", normalized)
    if city_match:
        candidate = city_match.group(1).strip()
        if candidate not in DIVISIONS and candidate not in STATUS_TERMS:
            city = candidate.title()

    search_text = normalized
    remove_terms = set(DOCUMENT_KEYWORDS.keys()) | set(DIVISIONS.keys()) | set(STATUS_TERMS.keys())
    stopwords = {
        "all",
        "about",
        "find",
        "from",
        "give",
        "have",
        "job",
        "jobs",
        "me",
        "of",
        "on",
        "open",
        "please",
        "show",
        "the",
        "what",
        "we",
        "for",
        "do",
        "note",
        "notes",
        "form",
        "forms",
    }
    words = [word for word in search_text.split() if word not in remove_terms and word not in stopwords]
    if city:
        city_words = set(normalize_search_text(city).split())
        words = [word for word in words if word not in city_words and word != "in"]
    if state_match:
        words = [word for word in words if word != state_match.group(1)]
    search_text = " ".join(words)

    return {
        "search_text": search_text,
        "tokens": tokenize_search_text(search_text),
        "document_type": document_type,
        "division": division,
        "status": status,
        "city": city,
        "state": state_match.group(1).upper() if state_match else None,
        "is_follow_up": bool(document_type and not search_text),
    }


def relation_columns(connection: Connection, relation_name: str = "dashboard_jobs") -> set[str]:
    rows = connection.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = :relation_name
            """
        ),
        {"relation_name": relation_name},
    ).fetchall()
    return {row[0] for row in rows}


def _connection(obj: Connection | Engine):
    return obj.connect() if isinstance(obj, Engine) else None


def search_source_columns(connection: Connection) -> tuple[str, set[str]]:
    dashboard_columns = relation_columns(connection, "dashboard_jobs")
    jobs_columns = relation_columns(connection, "jobs")
    if jobs_columns and len(DOCUMENT_LINK_COLUMNS & jobs_columns) > len(DOCUMENT_LINK_COLUMNS & dashboard_columns):
        return "jobs", jobs_columns
    if dashboard_columns:
        return "dashboard_jobs", dashboard_columns
    return "jobs", jobs_columns


def load_candidate_jobs(connection: Connection, interpreted: dict[str, Any], limit: int = 100) -> list[dict[str, Any]]:
    relation, columns = search_source_columns(connection)
    selected = [column for column in JOB_COLUMNS if column in columns]
    for required in ("job_id", "customer", "job_name"):
        if required in columns and required not in selected:
            selected.append(required)
    if not selected:
        return []

    params: dict[str, Any] = {"limit": max(limit, 25)}
    where = []
    if interpreted.get("division") and "division" in columns:
        where.append("LOWER(division) = LOWER(:division)")
        params["division"] = interpreted["division"]
    if interpreted.get("status"):
        status_clauses = []
        if "pipeline_status" in columns:
            status_clauses.append("LOWER(pipeline_status) LIKE LOWER(:status_like)")
        if "status" in columns:
            status_clauses.append("LOWER(status) LIKE LOWER(:status_like)")
        if status_clauses:
            where.append("(" + " OR ".join(status_clauses) + ")")
            params["status_like"] = f"%{interpreted['status']}%"
    if interpreted.get("city") and "city" in columns:
        where.append("LOWER(city) LIKE LOWER(:city_like)")
        params["city_like"] = f"%{interpreted['city']}%"
    if interpreted.get("state") and "state" in columns:
        where.append("LOWER(state) = LOWER(:state)")
        params["state"] = interpreted["state"]

    searchable = [column for column in SEARCH_COLUMNS if column in columns]
    tokens = interpreted.get("tokens") or tokenize_search_text(interpreted.get("search_text") or "")
    token_where = []
    if tokens and searchable:
        for index, token in enumerate(tokens):
            param_name = f"token_{index}"
            token_clauses = [f"LOWER(COALESCE({column}::text, '')) LIKE LOWER(:{param_name})" for column in searchable]
            token_where.append("(" + " OR ".join(token_clauses) + ")")
            params[param_name] = f"%{token}%"
    where_with_tokens = where + token_where

    order_sql = "ORDER BY updated_at DESC NULLS LAST" if "updated_at" in columns else ""
    if "job_id" in columns:
        order_sql = f"{order_sql}, job_id" if order_sql else "ORDER BY job_id"

    def fetch(active_where: list[str]) -> list[dict[str, Any]]:
        where_sql = "WHERE " + " AND ".join(active_where) if active_where else ""
        sql = f"SELECT {', '.join(selected)} FROM {relation} {where_sql} {order_sql} LIMIT :limit"
        rows = connection.execute(text(sql), params).mappings().all()
        return [dict(row) for row in rows]

    rows = fetch(where_with_tokens)
    if not rows and token_where:
        rows = fetch(where)
    return rows


def _score_field(query_norm: str, value: object, label: str) -> tuple[float, str]:
    value_text = first_nonblank(value)
    value_norm = normalize_search_text(value_text)
    if not query_norm or not value_norm:
        return 0.0, ""
    if query_norm == value_norm:
        return 100.0, f"Exact {label} match"
    prefix_score = 92.0 if label == "customer" else 90.0
    if value_norm.startswith(query_norm):
        return prefix_score, f"{label.title()} prefix match"
    if query_norm in value_norm:
        phrase_score = 88.0 if label in {"customer", "job name"} else 65.0
        return phrase_score, f"{label.title()} contains search phrase"
    ratio = SequenceMatcher(None, query_norm, value_norm).ratio()
    if ratio >= 0.62:
        return 55.0 + ratio * 20.0, f"Similar {label}"
    return ratio * 25.0, f"Weak {label} similarity"


def combined_search_text(record: dict[str, Any]) -> str:
    return normalize_search_text(" ".join(first_nonblank(record.get(column)) for column in SEARCH_COLUMNS))


def rank_job(record: dict[str, Any], interpreted: dict[str, Any]) -> RankedJob:
    query_norm = normalize_search_text(interpreted.get("search_text") or "")
    tokens = interpreted.get("tokens") or tokenize_search_text(query_norm)
    best_score = 0.0
    best_reason = "Filter match"
    field_weights = {
        "job_id": 1.0,
        "customer": 1.0,
        "job_name": 0.98,
        "site_address": 0.75,
        "city": 0.6,
        "folder_name": 0.7,
        "folder_path": 0.65,
        "estimate_file": 0.62,
        "invoice_file": 0.62,
        "primary_doc_name": 0.62,
    }
    for field, weight in field_weights.items():
        score, reason = _score_field(query_norm, record.get(field), field.replace("_", " "))
        score *= weight
        if score > best_score:
            best_score = score
            best_reason = reason
    if tokens:
        combined = combined_search_text(record)
        matched_tokens = [token for token in tokens if re.search(rf"\b{re.escape(token)}\b", combined)]
        if len(matched_tokens) == len(tokens):
            token_score = 85.0
            if token_score > best_score:
                best_score = token_score
                best_reason = "All query tokens present across job metadata"
        elif matched_tokens:
            token_score = 30.0 + (len(matched_tokens) / len(tokens)) * 45.0
            if token_score > best_score:
                best_score = token_score
                best_reason = f"{len(matched_tokens)} of {len(tokens)} query tokens present"
    elif any(interpreted.get(key) for key in ("division", "status", "city", "state")):
        best_score = 70.0
        best_reason = "Filter match"
    if interpreted.get("division") and normalize_search_text(record.get("division")) == normalize_search_text(interpreted["division"]):
        best_score += 5
    if interpreted.get("status"):
        status_text = normalize_search_text(" ".join([first_nonblank(record.get("pipeline_status")), first_nonblank(record.get("status"))]))
        if normalize_search_text(interpreted["status"]) in status_text:
            best_score += 5
    if interpreted.get("city") and normalize_search_text(record.get("city")) == normalize_search_text(interpreted["city"]):
        best_score += 3
    if interpreted.get("state") and normalize_search_text(record.get("state")) == normalize_search_text(interpreted["state"]):
        best_score += 2
    return RankedJob(row=record, score=round(best_score, 2), reason=best_reason)


def search_jobs_with_diagnostics(connection: Connection | Engine, query: str, limit: int = 10, filters: dict | None = None) -> dict[str, Any]:
    interpreted = interpret_search_request(query)
    if filters:
        interpreted.update({key: value for key, value in filters.items() if value})
    interpreted["tokens"] = tokenize_search_text(interpreted.get("search_text") or "")
    manager = _connection(connection)
    conn = manager.__enter__() if manager else connection
    try:
        source_relation, source_columns = search_source_columns(conn)
        candidates = load_candidate_jobs(conn, interpreted, limit=max(limit * 12, 100))
    finally:
        if manager:
            manager.__exit__(None, None, None)
    results = rank_candidate_jobs(candidates, interpreted, limit=limit)
    return {
        "interpreted": interpreted,
        "tokens": interpreted["tokens"],
        "candidate_count": len(candidates),
        "source_relation": source_relation,
        "source_columns": sorted(source_columns),
        "results": results,
    }


def rank_candidate_jobs(candidates: list[dict[str, Any]], interpreted: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    ranked = sorted((rank_job(row, interpreted) for row in candidates), key=lambda item: item.score, reverse=True)
    results = []
    for item in ranked[:limit]:
        row = dict(item.row)
        row["match_score"] = item.score
        row["match_reason"] = item.reason
        row["interpreted_request"] = interpreted
        results.append(row)
    return results


def search_jobs(connection: Connection | Engine, query: str, limit: int = 10, filters: dict | None = None) -> list[dict[str, Any]]:
    return search_jobs_with_diagnostics(connection, query, limit=limit, filters=filters)["results"]


def get_job_documents(job: dict[str, Any], document_type: str | None = None) -> list[dict[str, str]]:
    if document_type in (None, "all"):
        fields = ALL_DOCUMENT_FIELDS
    else:
        requested_fields = DOC_FIELD_MAP.get(document_type, [])
        requested_field_names = {field for field, _label in requested_fields}
        fields = requested_fields + [(field, label) for field, label in ALL_DOCUMENT_FIELDS if field not in requested_field_names]
    seen: set[str] = set()
    docs: list[dict[str, str]] = []
    for field, label in fields:
        url = first_nonblank(job.get(field))
        if not url or url in seen:
            continue
        seen.add(url)
        doc_type = field.replace("_url", "").replace("_link", "")
        if field == "primary_doc_link" and first_nonblank(job.get("primary_doc_type")):
            label = str(job["primary_doc_type"]).replace("_", " ").title()
            doc_type = str(job["primary_doc_type"])
        docs.append({"label": label, "url": url, "type": doc_type, "field": field})
    return docs


def normalize_indexed_document(doc: dict[str, Any]) -> dict[str, str]:
    document_type = str(doc.get("document_type") or "other")
    label = DOCUMENT_LABELS.get(document_type, document_type.replace("_", " ").title())
    return {
        "label": label,
        "url": str(doc.get("sharepoint_url") or ""),
        "type": document_type,
        "field": "documents",
        "file_name": str(doc.get("file_name") or ""),
        "classification_reason": str(doc.get("classification_reason") or ""),
    }


def get_preferred_job_documents(connection: Connection | Engine, job: dict[str, Any], document_type: str | None = None, limit: int = 100) -> list[dict[str, str]]:
    try:
        from .document_index import documents_table_count, list_job_documents

        if documents_table_count(connection) > 0:
            indexed = [
                normalize_indexed_document(doc)
                for doc in list_job_documents(connection, str(job.get("job_id") or ""), document_type, limit)
                if first_nonblank(doc.get("sharepoint_url"))
            ]
            seen: set[str] = set()
            deduped = []
            for doc in indexed:
                if doc["url"] in seen:
                    continue
                seen.add(doc["url"])
                deduped.append(doc)
            return deduped
    except Exception:
        pass
    return get_job_documents(job, document_type)


def requested_document_available(job: dict[str, Any], document_type: str | None) -> bool:
    if document_type in (None, "all"):
        return True
    return any(first_nonblank(job.get(field)) for field, _label in DOC_FIELD_MAP.get(document_type, []))


def requested_document_label(document_type: str | None) -> str:
    return DOCUMENT_LABELS.get(str(document_type or ""), str(document_type or "Document").replace("_", " ").title())


def format_cli_result(result: dict[str, Any]) -> str:
    title = first_nonblank(result.get("job_name"), result.get("customer"), result.get("job_id"))
    location = ", ".join(part for part in [first_nonblank(result.get("city")), first_nonblank(result.get("state"))] if part)
    return (
        f"score={result['match_score']} | {result['match_reason']} | "
        f"customer={result.get('customer') or '-'} | job_name={title} | job_id={result.get('job_id') or '-'} | "
        f"division={result.get('division') or '-'} | pipeline_status={result.get('pipeline_status') or '-'} | "
        f"status={result.get('status') or '-'} | location={location or '-'}"
    )


def format_cli_documents(result: dict[str, Any], document_type: str | None) -> list[str]:
    docs = get_job_documents(result, document_type)
    return format_document_lines(docs, document_type)


def format_document_lines(docs: list[dict[str, str]], document_type: str | None) -> list[str]:
    lines: list[str] = []
    if document_type not in (None, "all"):
        requested_label = requested_document_label(document_type)
        requested_docs = docs[:1] if docs and docs[0]["label"] == requested_label else []
        available_docs = docs[1:] if requested_docs else docs
        if requested_docs:
            lines.append(f"  {requested_docs[0]['label']}: {requested_docs[0]['url']}")
        else:
            lines.append(f"  {requested_label}: not indexed")
        if available_docs:
            lines.append("  Available documents:")
            lines.extend(f"  - {doc['label']}: {doc['url']}" for doc in available_docs)
        return lines
    if not docs:
        return ["  Documents: not indexed"]
    for doc in docs:
        lines.append(f"  - {doc['label']}: {doc['url']}")
    return lines


def format_cli_documents_for_connection(connection: Connection | Engine, result: dict[str, Any], document_type: str | None) -> list[str]:
    docs = get_preferred_job_documents(connection, result, document_type)
    return format_document_lines(docs, document_type)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Search Spray-Tec jobs and document links.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL"))
    parser.add_argument("--limit", type=int, default=10)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    if not args.database_url:
        raise SystemExit("Set --database-url, DATABASE_URL, or NEON_DATABASE_URL.")
    engine = create_engine(args.database_url, future=True)
    diagnostics = search_jobs_with_diagnostics(engine, args.query, limit=max(args.limit, 10))
    interpreted = diagnostics["interpreted"]
    filters = {
        "division": interpreted.get("division"),
        "status": interpreted.get("status"),
        "city": interpreted.get("city"),
        "state": interpreted.get("state"),
    }
    print("Interpreted request:")
    print(json.dumps(interpreted, indent=2))
    print(f"Normalized search text: {interpreted.get('search_text') or ''}")
    print(f"Tokens: {diagnostics['tokens']}")
    print(f"Filters: {filters}")
    print(f"Search source: {diagnostics['source_relation']}")
    print(f"Search source columns: {', '.join(diagnostics['source_columns'])}")
    print(f"Candidate count before threshold: {diagnostics['candidate_count']}")

    results = diagnostics["results"]
    strong_results = [result for result in results if float(result.get("match_score") or 0) >= 45]
    weak_results = [result for result in results if float(result.get("match_score") or 0) < 45]
    print("Top candidates:")
    if strong_results:
        for result in strong_results[:10]:
            print(format_cli_result(result))
            for line in format_cli_documents_for_connection(engine, result, interpreted.get("document_type")):
                print(line)
    else:
        print("No candidate passed the normal threshold.")
    if not strong_results and weak_results:
        print("Weak suggestions:")
        for result in weak_results[:5]:
            print(format_cli_result(result))
            for line in format_cli_documents_for_connection(engine, result, interpreted.get("document_type")):
                print(line)


if __name__ == "__main__":
    main()
