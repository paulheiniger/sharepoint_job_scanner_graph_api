from __future__ import annotations

import argparse
import hashlib
import mimetypes
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urldefrag, urljoin, urlparse

import requests

from .document_queue import (
    DEFAULT_APPROVED_DOMAINS,
    is_approved_document_url,
    queue_product_document_url,
    source_domain,
    upsert_document_queue,
    write_queue_csv,
)
from .product_catalog import clean_text, slugify
from .product_family_lookup import (
    build_document_queue_from_lookup,
    load_product_family_lookup,
    upsert_product_family_lookup,
)

USER_AGENT = "SprayTecProductKnowledgeBot/0.1 (+controlled estimator document discovery)"
DOCUMENT_URL_MARKERS = (
    ".pdf",
    "pds",
    "product-data",
    "product_data",
    "data-sheet",
    "datasheet",
    "technical-data",
    "technical_data",
    "documents",
    "download",
)


@dataclass
class FetchResult:
    url: str
    content: bytes
    content_type: str = ""
    final_url: str | None = None

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="ignore")


@dataclass
class LinkCandidate:
    url: str
    text: str
    source_url: str
    score: float
    matched_lookup_ids: list[str]
    matched_families: list[str]
    decision_nodes: list[str]
    reason: str


class _AnchorParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self._current_href: str | None = None
        self._current_text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = ""
        for key, value in attrs:
            if key.lower() == "href" and value:
                href = value
                break
        if not href:
            return
        self._current_href = href
        self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._current_href:
            return
        url = normalize_url(self.base_url, self._current_href)
        text = clean_text(" ".join(self._current_text))
        if url:
            self.links.append((url, text))
        self._current_href = None
        self._current_text = []


def normalize_url(base_url: str, href: str) -> str:
    href = clean_text(href)
    if not href or href.startswith(("mailto:", "tel:", "javascript:")):
        return ""
    url = urljoin(base_url, href)
    url, _fragment = urldefrag(url)
    return url


def extract_links_from_html(html: str, base_url: str) -> list[tuple[str, str]]:
    parser = _AnchorParser(base_url)
    parser.feed(html or "")
    deduped: dict[str, str] = {}
    for url, text in parser.links:
        deduped.setdefault(url, text)
    return sorted(deduped.items())


def _tokens(value: str) -> set[str]:
    text = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return {token for token in text.split() if len(token) > 1 and token not in {"the", "and", "for", "with", "pds"}}


def _add_unique(values: list[str], value: Any) -> None:
    text = clean_text(value)
    if text and text not in values:
        values.append(text)


def looks_like_document_url(url: str, link_text: str = "") -> bool:
    haystack = f"{url} {link_text}".lower()
    return any(marker in haystack for marker in DOCUMENT_URL_MARKERS)


def score_document_link(url: str, link_text: str, lookup_row: dict[str, Any]) -> tuple[float, list[str]]:
    haystack = f"{url} {link_text}".lower()
    lookup_terms = clean_text(lookup_row.get("lookup_terms"))
    family = clean_text(lookup_row.get("canonical_product_family"))
    lookup_tokens = _tokens(f"{family} {lookup_terms}")
    haystack_tokens = _tokens(haystack)
    overlap = len(lookup_tokens & haystack_tokens)
    score = 0.0
    reasons: list[str] = []
    if looks_like_document_url(url, link_text):
        score += 2.0
        reasons.append("document_url_marker")
    if url.lower().endswith(".pdf"):
        score += 2.5
        reasons.append("pdf_url")
    if overlap:
        score += min(overlap, 8) * 0.6
        reasons.append(f"{overlap}_lookup_terms_matched")
    normalized_family = re.sub(r"[^a-z0-9]+", "", family.lower())
    normalized_haystack = re.sub(r"[^a-z0-9]+", "", haystack)
    if normalized_family and normalized_family in normalized_haystack:
        score += 2.5
        reasons.append("family_name_matched")
    if "sds" in haystack or "safety data" in haystack:
        score -= 1.5
        reasons.append("sds_deprioritized")
    return score, reasons


def fetch_url(url: str, *, timeout: int = 20) -> FetchResult:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return FetchResult(
        url=url,
        final_url=response.url,
        content=response.content,
        content_type=response.headers.get("content-type", ""),
    )


def _same_or_approved_domain(url: str, source_url: str, approved_domains: set[str] | list[str]) -> bool:
    domain = source_domain(url)
    source = source_domain(source_url)
    return domain == source or is_approved_document_url(url, approved_domains)


def _lookup_rows_for_url(lookup_rows: list[dict[str, Any]], url: str) -> list[dict[str, Any]]:
    return [row for row in lookup_rows if clean_text(row.get("official_vendor_url")) == url]


def candidate_links_for_page(
    *,
    page_url: str,
    html: str,
    lookup_rows: list[dict[str, Any]],
    approved_domains: set[str] | list[str] | None = None,
    min_score: float = 2.0,
) -> list[LinkCandidate]:
    approved = set(approved_domains or DEFAULT_APPROVED_DOMAINS)
    family_rows = _lookup_rows_for_url(lookup_rows, page_url)
    if not family_rows:
        family_rows = [row for row in lookup_rows if source_domain(row.get("official_vendor_url") or "") == source_domain(page_url)]
    candidates_by_url: dict[str, LinkCandidate] = {}
    for url, link_text in extract_links_from_html(html, page_url):
        if not _same_or_approved_domain(url, page_url, approved):
            continue
        if not looks_like_document_url(url, link_text):
            continue
        matched_lookup_ids: list[str] = []
        matched_families: list[str] = []
        decision_nodes: list[str] = []
        reasons: list[str] = []
        best_score = 0.0
        for row in family_rows:
            score, score_reasons = score_document_link(url, link_text, row)
            if score >= min_score:
                _add_unique(matched_lookup_ids, row.get("lookup_id"))
                _add_unique(matched_families, row.get("canonical_product_family"))
                for node in row.get("decision_nodes") or []:
                    _add_unique(decision_nodes, node)
            if score > best_score:
                best_score = score
            reasons.extend(score_reasons)
        if best_score < min_score:
            continue
        existing = candidates_by_url.get(url)
        candidate = LinkCandidate(
            url=url,
            text=link_text,
            source_url=page_url,
            score=round(best_score, 4),
            matched_lookup_ids=matched_lookup_ids,
            matched_families=matched_families,
            decision_nodes=decision_nodes,
            reason=", ".join(sorted(set(reasons))),
        )
        if existing is None or candidate.score > existing.score:
            candidates_by_url[url] = candidate
    return sorted(candidates_by_url.values(), key=lambda item: item.score, reverse=True)


def _queue_row_from_candidate(candidate: LinkCandidate, family_rows: list[dict[str, Any]]) -> dict[str, Any]:
    vendor = clean_text(family_rows[0].get("vendor")) if family_rows else ""
    document_type = "PDS" if "pds" in f"{candidate.url} {candidate.text}".lower() else "product_document"
    row = queue_product_document_url(
        candidate.url,
        manufacturer_hint=vendor,
        document_type=document_type,
        approved_domains=DEFAULT_APPROVED_DOMAINS,
        decision_nodes=candidate.decision_nodes,
        notes=(
            f"Discovered from approved product-family page {candidate.source_url}. "
            f"Matched families: {', '.join(candidate.matched_families) or 'review required'}. "
            f"Link text: {candidate.text}. Score: {candidate.score}. Reason: {candidate.reason}."
        ),
        priority=max(10, 50 - int(candidate.score * 5)),
    )
    row["source_type"] = "discovered_product_document_url"
    row["discovery_method"] = "approved_domain_scrape"
    row["lookup_ids"] = candidate.matched_lookup_ids
    row["source_page_url"] = candidate.source_url
    row["link_text"] = candidate.text
    row["scrape_score"] = candidate.score
    return row


def scrape_product_family_lookup(
    lookup_rows: list[dict[str, Any]],
    *,
    approved_domains: set[str] | list[str] | None = None,
    fetcher: Callable[[str], FetchResult] | None = None,
    max_pages: int = 25,
    max_candidates_per_page: int = 8,
    min_score: float = 2.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    approved = set(approved_domains or DEFAULT_APPROVED_DOMAINS)
    fetch = fetcher or fetch_url
    seed_rows = build_document_queue_from_lookup(lookup_rows, approved_domains=approved)
    discovered_rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for seed in seed_rows[:max_pages]:
        page_url = str(seed.get("source_url") or "")
        if not page_url or not is_approved_document_url(page_url, approved):
            diagnostics.append({"source_url": page_url, "status": "skipped_unapproved_domain"})
            continue
        try:
            fetched = fetch(page_url)
        except Exception as exc:
            diagnostics.append({"source_url": page_url, "status": "fetch_failed", "error": f"{type(exc).__name__}: {exc}"})
            continue
        content_type = fetched.content_type.lower()
        family_rows = _lookup_rows_for_url(lookup_rows, page_url)
        if page_url.lower().endswith(".pdf") or "pdf" in content_type:
            candidate = LinkCandidate(
                url=fetched.final_url or page_url,
                text=page_url.rsplit("/", 1)[-1],
                source_url=page_url,
                score=10.0,
                matched_lookup_ids=[row.get("lookup_id") for row in family_rows if row.get("lookup_id")],
                matched_families=[row.get("canonical_product_family") for row in family_rows if row.get("canonical_product_family")],
                decision_nodes=[node for row in family_rows for node in (row.get("decision_nodes") or [])],
                reason="seed_url_is_pdf",
            )
            discovered_rows.append(_queue_row_from_candidate(candidate, family_rows))
            diagnostics.append({"source_url": page_url, "status": "seed_pdf", "candidate_count": 1})
            continue
        candidates = candidate_links_for_page(
            page_url=fetched.final_url or page_url,
            html=fetched.text,
            lookup_rows=lookup_rows,
            approved_domains=approved,
            min_score=min_score,
        )[:max_candidates_per_page]
        for candidate in candidates:
            candidate_family_rows = [row for row in lookup_rows if row.get("lookup_id") in set(candidate.matched_lookup_ids)]
            discovered_rows.append(_queue_row_from_candidate(candidate, candidate_family_rows or family_rows))
        diagnostics.append(
            {
                "source_url": page_url,
                "status": "scraped",
                "candidate_count": len(candidates),
                "content_type": fetched.content_type,
            }
        )
    deduped: dict[str, dict[str, Any]] = {}
    for row in discovered_rows:
        key = str(row.get("source_url") or row.get("queue_id"))
        existing = deduped.get(key)
        if existing is None or float(row.get("scrape_score") or 0) > float(existing.get("scrape_score") or 0):
            deduped[key] = row
    return sorted(deduped.values(), key=lambda row: (int(row.get("priority") or 100), str(row.get("source_url") or ""))), diagnostics


def _safe_download_name(url: str, content_type: str = "") -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name
    if not name or "." not in name:
        extension = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".pdf"
        name = f"{slugify(url)}{extension}"
    return slugify(Path(name).stem) + Path(name).suffix.lower()


def download_queue_documents(
    queue_rows: list[dict[str, Any]],
    *,
    out_dir: str | Path,
    fetcher: Callable[[str], FetchResult] | None = None,
    max_docs: int = 50,
) -> list[dict[str, Any]]:
    fetch = fetcher or fetch_url
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[dict[str, Any]] = []
    for row in queue_rows:
        if len(downloaded) >= max_docs:
            break
        url = str(row.get("source_url") or "")
        if not url or not row.get("approved_for_ingest"):
            continue
        if not is_approved_document_url(url):
            continue
        try:
            fetched = fetch(url)
        except Exception as exc:
            updated = dict(row)
            updated["ingest_status"] = "download_failed"
            updated["validation_warnings"] = [*(updated.get("validation_warnings") or []), f"{type(exc).__name__}: {exc}"]
            downloaded.append(updated)
            continue
        content_type = fetched.content_type.lower()
        if not (url.lower().endswith(".pdf") or "pdf" in content_type):
            updated = dict(row)
            updated["ingest_status"] = "not_downloaded_non_pdf"
            updated["last_checked_at"] = datetime.now(UTC).isoformat()
            downloaded.append(updated)
            continue
        content = fetched.content
        digest = hashlib.sha256(content).hexdigest()
        filename = _safe_download_name(fetched.final_url or url, fetched.content_type)
        target = output_dir / filename
        if target.exists():
            target = output_dir / f"{target.stem}_{digest[:8]}{target.suffix}"
        target.write_bytes(content)
        updated = dict(row)
        updated["source_path"] = str(target)
        updated["content_hash"] = digest
        updated["fetched_at"] = datetime.now(UTC).isoformat()
        updated["ingest_status"] = "downloaded"
        updated["catalog_path"] = ""
        downloaded.append(updated)
    return downloaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Controlled approved-domain discovery of product PDS/PDF documents.")
    parser.add_argument("--lookup", default="", help="Product family lookup CSV. Defaults to packaged seed.")
    parser.add_argument("--queue-out", default="output/product_document_scrape_queue.csv", help="Discovered queue CSV output.")
    parser.add_argument("--diagnostics-out", default="output/product_document_scrape_diagnostics.csv", help="Scrape diagnostics CSV output.")
    parser.add_argument("--download-dir", default="", help="Optional directory for downloaded approved PDFs.")
    parser.add_argument("--download", action="store_true", help="Download discovered approved PDFs.")
    parser.add_argument("--max-pages", type=int, default=25)
    parser.add_argument("--max-candidates-per-page", type=int, default=8)
    parser.add_argument("--min-score", type=float, default=2.0)
    parser.add_argument("--db-url", default="")
    parser.add_argument("--write-db", action="store_true", help="Upsert lookup rows and discovered queue rows to DB.")
    parser.add_argument("--approved-domain", action="append", default=[], help="Approved vendor domain. Can be repeated.")
    args = parser.parse_args(argv)

    approved_domains = set(args.approved_domain or DEFAULT_APPROVED_DOMAINS)
    lookup_rows = load_product_family_lookup(args.lookup or None, approved_domains=approved_domains) if args.lookup else load_product_family_lookup(approved_domains=approved_domains)
    queue_rows, diagnostics = scrape_product_family_lookup(
        lookup_rows,
        approved_domains=approved_domains,
        max_pages=args.max_pages,
        max_candidates_per_page=args.max_candidates_per_page,
        min_score=args.min_score,
    )
    if args.download:
        if not args.download_dir:
            raise SystemExit("--download requires --download-dir")
        queue_rows = download_queue_documents(queue_rows, out_dir=args.download_dir)
    queue_out = write_queue_csv(queue_rows, args.queue_out)
    diag_path = Path(args.diagnostics_out)
    diag_path.parent.mkdir(parents=True, exist_ok=True)
    if diagnostics:
        keys = sorted({key for row in diagnostics for key in row})
        with diag_path.open("w", newline="", encoding="utf-8") as handle:
            writer = __import__("csv").DictWriter(handle, fieldnames=keys)
            writer.writeheader()
            writer.writerows(diagnostics)
    else:
        diag_path.write_text("", encoding="utf-8")
    if args.write_db:
        if not args.db_url:
            raise SystemExit("--write-db requires --db-url")
        upsert_product_family_lookup(args.db_url, lookup_rows)
        upsert_document_queue(args.db_url, queue_rows)
    print(f"Wrote discovered product document queue: {queue_out} ({len(queue_rows)} rows)")
    print(f"Wrote scrape diagnostics: {diag_path} ({len(diagnostics)} pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
