from jobscan.job_search import (
    format_cli_documents,
    get_job_documents,
    interpret_search_request,
    normalize_search_text,
    requested_document_available,
    rank_candidate_jobs,
    rank_job,
)


def test_normalize_search_text_handles_curly_apostrophes_and_legal_suffixes() -> None:
    assert normalize_search_text("Diven's invoice") == "diven invoice"
    assert normalize_search_text("Mudd’s Furniture Showroom, Inc.") == "mudd furniture showroom"
    assert normalize_search_text("Canadian Solar Inc / CSI Jeffersonville") == "canadian solar canadian solar csi jeffersonville"
    assert normalize_search_text("Canadian Solar Inc.") == "canadian solar"


def test_invoice_document_intent_does_not_force_invoiced_status() -> None:
    interpreted = interpret_search_request("show me Diven's invoice")
    assert interpreted["search_text"] == "diven"
    assert interpreted["document_type"] == "invoice"
    assert interpreted["status"] is None


def test_interpret_search_request_detects_document_intents() -> None:
    assert interpret_search_request("Show me the Canadian Solar estimate")["document_type"] == "estimate"
    assert interpret_search_request("Find invoices for Diven sporting shop")["document_type"] == "invoice"
    assert interpret_search_request("Open the proposal for Billy Riddle")["document_type"] == "proposal"
    assert interpret_search_request("Open the job folder for Goodwin")["document_type"] == "folder"


def test_interpret_search_request_removes_document_filler_words() -> None:
    interpreted = interpret_search_request("all documents on Canadian Solar")

    assert interpreted["document_type"] == "all"
    assert interpreted["search_text"] == "canadian solar"
    assert interpreted["tokens"] == ["canadian", "solar"]


def test_interpret_search_request_detects_filters_and_keeps_unknown_status_searchable() -> None:
    interpreted = interpret_search_request("find completed flooring jobs in Jeffersonville")
    assert interpreted["division"] == "Flooring"
    assert interpreted["status"] == "Completed"
    assert interpreted["city"] == "Jeffersonville"
    assert interpreted["search_text"] == ""


def test_follow_up_intent_detection_for_selected_job_documents() -> None:
    interpreted = interpret_search_request("What documents do we have?")
    assert interpreted["document_type"] == "all"
    assert interpreted["is_follow_up"] is True


def test_rank_job_prioritizes_exact_and_contains_matches() -> None:
    interpreted = interpret_search_request("Billy Riddle")
    billy = {
        "job_id": "job-billy",
        "customer": "Billy Riddle",
        "job_name": "Billy Riddle Residence",
        "division": "Flooring",
    }
    goodwin = {
        "job_id": "job-goodwin",
        "customer": "Goodwin Residence",
        "job_name": "Goodwin Residence - Garage Floor",
        "division": "Flooring",
    }
    assert rank_job(billy, interpreted).score > rank_job(goodwin, interpreted).score


def test_exact_customer_match_ranks_above_fuzzy_matches() -> None:
    interpreted = interpret_search_request("Billy Riddle")
    exact = {"job_id": "1", "customer": "Billy Riddle", "job_name": "Residence"}
    fuzzy = {"job_id": "2", "customer": "Billy Riddell LLC", "job_name": "Residence"}
    assert rank_job(exact, interpreted).score > rank_job(fuzzy, interpreted).score


def test_rank_job_supports_realistic_job_name_fragments() -> None:
    interpreted = interpret_search_request("Diven sporting shop")
    record = {
        "job_id": "job-diven",
        "customer": "Diven, Clint",
        "job_name": "Diven, Clint - Lee Sporting Shop",
        "division": "Roofing",
    }
    ranked = rank_job(record, interpreted)
    assert ranked.score >= 45
    assert "query tokens" in ranked.reason.lower() or "job name" in ranked.reason.lower()


def test_token_matching_across_customer_and_job_name() -> None:
    interpreted = interpret_search_request("mudd furniture")
    record = {
        "job_id": "job-mudd",
        "customer": "Mudd Family Trust",
        "job_name": "Furniture Showroom Roof",
        "division": "Roofing",
    }
    ranked = rank_job(record, interpreted)
    assert ranked.score >= 85
    assert ranked.reason == "All query tokens present across job metadata"


def test_diven_matches_customer_with_comma() -> None:
    interpreted = interpret_search_request("show me Diven's invoice")
    record = {
        "job_id": "job-diven",
        "customer": "Diven, Clint",
        "job_name": "Diven, Clint - Lee Sporting Shop",
        "invoice_url": "https://sharepoint.example/diven-invoice.pdf",
    }
    ranked = rank_job(record, interpreted)
    assert ranked.score >= 90


def test_filter_only_query_returns_rows_when_candidates_are_preloaded() -> None:
    interpreted = interpret_search_request("find completed flooring jobs in Jeffersonville")
    candidates = [
        {
            "job_id": "job-goodwin",
            "customer": "Goodwin Residence",
            "job_name": "Garage Floor",
            "division": "Flooring",
            "pipeline_status": "Completed",
            "status": "Completed",
            "city": "Jeffersonville",
            "state": "IN",
        }
    ]
    results = rank_candidate_jobs(candidates, interpreted, limit=10)
    assert len(results) == 1
    assert results[0]["job_id"] == "job-goodwin"
    assert results[0]["match_score"] > 0


def test_get_job_documents_filters_and_deduplicates_urls() -> None:
    job = {
        "folder_url": "https://sharepoint.example/jobs/mudds",
        "primary_doc_link": "https://sharepoint.example/docs/mudds-estimate.xlsx",
        "primary_doc_type": "estimate",
        "estimate_url": "https://sharepoint.example/docs/mudds-estimate.xlsx",
        "proposal_url": "https://sharepoint.example/docs/mudds-proposal.pdf",
        "invoice_url": "",
    }
    all_docs = get_job_documents(job, "all")
    assert [doc["url"] for doc in all_docs] == [
        "https://sharepoint.example/jobs/mudds",
        "https://sharepoint.example/docs/mudds-estimate.xlsx",
        "https://sharepoint.example/docs/mudds-proposal.pdf",
    ]
    estimate_docs = get_job_documents(job, "estimate")
    assert estimate_docs[:1] == [
        {
            "label": "Estimate",
            "url": "https://sharepoint.example/docs/mudds-estimate.xlsx",
            "type": "estimate",
            "field": "estimate_url",
        }
    ]
    assert [doc["url"] for doc in estimate_docs] == [
        "https://sharepoint.example/docs/mudds-estimate.xlsx",
        "https://sharepoint.example/jobs/mudds",
        "https://sharepoint.example/docs/mudds-proposal.pdf",
    ]


def test_requested_document_appears_first_then_available_documents_are_deduped() -> None:
    job = {
        "folder_url": "https://sharepoint.example/jobs/canadian-solar",
        "primary_doc_link": "https://sharepoint.example/docs/canadian-solar-estimate.xlsx",
        "primary_doc_type": "estimate",
        "estimate_url": "https://sharepoint.example/docs/canadian-solar-estimate.xlsx",
        "proposal_url": "https://sharepoint.example/docs/canadian-solar-proposal.pdf",
    }
    docs = get_job_documents(job, "proposal")

    assert [doc["label"] for doc in docs] == ["Proposal", "Job folder", "Estimate"]
    assert [doc["url"] for doc in docs] == [
        "https://sharepoint.example/docs/canadian-solar-proposal.pdf",
        "https://sharepoint.example/jobs/canadian-solar",
        "https://sharepoint.example/docs/canadian-solar-estimate.xlsx",
    ]


def test_missing_requested_document_is_reported_safely_with_available_documents() -> None:
    job = {
        "folder_url": "https://sharepoint.example/jobs/diven",
        "proposal_url": "https://sharepoint.example/docs/diven-proposal.pdf",
        "invoice_url": "",
    }

    assert requested_document_available(job, "invoice") is False
    assert format_cli_documents(job, "invoice") == [
        "  Invoice: not indexed",
        "  Available documents:",
        "  - Job folder: https://sharepoint.example/jobs/diven",
        "  - Proposal: https://sharepoint.example/docs/diven-proposal.pdf",
    ]


def test_requested_document_link_is_returned_without_fabrication() -> None:
    job = {
        "customer": "Diven, Clint",
        "job_name": "Diven, Clint - Lee Sporting Shop",
        "invoice_url": "https://sharepoint.example/invoices/diven.pdf",
        "estimate_url": "https://sharepoint.example/estimates/diven.xlsx",
    }
    docs = get_job_documents(job, interpret_search_request("show me Diven's invoice")["document_type"])
    assert docs == [
        {
            "label": "Invoice",
            "url": "https://sharepoint.example/invoices/diven.pdf",
            "type": "invoice",
            "field": "invoice_url",
        },
        {
            "label": "Estimate",
            "url": "https://sharepoint.example/estimates/diven.xlsx",
            "type": "estimate",
            "field": "estimate_url",
        },
    ]
    assert format_cli_documents(job, "invoice") == [
        "  Invoice: https://sharepoint.example/invoices/diven.pdf",
        "  Available documents:",
        "  - Estimate: https://sharepoint.example/estimates/diven.xlsx",
    ]


def test_document_link_extraction_for_requested_folder() -> None:
    job = {
        "customer": "Canadian Solar Inc",
        "job_name": "Canadian Solar Inc Jeffersonville",
        "folder_url": "https://sharepoint.example/jobs/canadian-solar",
        "estimate_url": "https://sharepoint.example/docs/canadian-solar-estimate.xlsx",
    }
    docs = get_job_documents(job, interpret_search_request("open CSI folder")["document_type"])
    assert docs[:1] == [
        {
            "label": "Job folder",
            "url": "https://sharepoint.example/jobs/canadian-solar",
            "type": "folder",
            "field": "folder_url",
        }
    ]
    assert docs[1]["label"] == "Estimate"


def test_dashboard_jobs_view_exposes_document_link_fields() -> None:
    view_sql = open("db/dashboard_views.sql", encoding="utf-8").read()
    for field in [
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
        "document_link_count",
    ]:
        assert f"j.{field}" in view_sql
