from __future__ import annotations

import json

import pandas as pd
import pytest

from jobscan.estimator.decision_history import build_historical_decision_tables
from jobscan.estimator.schemas import EstimatorData
from jobscan.products.ai_document_parser import is_suspicious_product_name, normalize_extracted_measure
from jobscan.products.document_queue import (
    discover_product_documents,
    is_approved_document_url,
    queue_product_document_url,
    write_queue_csv,
)
from jobscan.products.document_scraper import (
    FetchResult,
    candidate_links_for_page,
    download_queue_documents,
    scrape_product_family_lookup,
)
from jobscan.products.catalog_db import (
    _document_params,
    _json_param,
    _product_params,
    _property_params,
    _rule_params,
    _schema_statements,
)
from jobscan.products.product_catalog import ProductKnowledge, export_product_catalog_xlsx, load_product_catalog_json
from jobscan.products.product_family_lookup import (
    build_document_queue_from_lookup,
    infer_decision_nodes,
    load_product_family_lookup,
)
from jobscan.products.product_ingest import ingest_product_directory, ingest_product_document
from jobscan.products import product_ingest as product_ingest_module
from jobscan.products.product_matching import match_product, product_context_for_decision
from jobscan.products.product_rules import DECISION_LINKS_BY_CATEGORY
from jobscan.products.template_product_mapping import (
    collect_product_mapping_audit,
    proposed_product_aliases,
    proposed_template_product_links,
)
from jobscan.products.validate_catalog import validate_product_catalog, write_validation_workbook


def write_pdf(path, text: str) -> None:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=10)
    doc.save(path)


def test_product_catalog_database_params_sanitize_json_and_numbers() -> None:
    product = _product_params(
        {
            "product_id": "gaco_onepass",
            "manufacturer": "Gaco",
            "product_name": "GacoOnePass",
            "category": "spray_foam",
            "aliases": ["Gaco 2.0 lb", "GacoOnePass"],
            "active": "true",
            "extraction_warnings": ["needs review"],
        }
    )
    assert product["product_id"] == "gaco_onepass"
    assert json.loads(product["aliases"]) == ["Gaco 2.0 lb", "GacoOnePass"]
    assert json.loads(product["extraction_warnings"]) == ["needs review"]
    assert product["active"] is True

    document = _document_params(
        {
            "document_id": "doc1",
            "product_id": "gaco_onepass",
            "source_path": "product_documents/scraped/gaco_onepass.pdf",
            "revision_date": "Revision 2026 pending",
            "extraction_warnings": [],
        }
    )
    assert document["revision_date"] is None
    assert json.loads(document["extraction_warnings"]) == []

    prop = _property_params(
        {
            "property_id": "prop1",
            "product_id": "gaco_onepass",
            "property_name": "R_value",
            "numeric_value": "5.7",
            "source_page": "2",
            "confidence": "0.85",
        }
    )
    assert prop["numeric_value"] == 5.7
    assert prop["source_page"] == 2
    assert prop["confidence"] == 0.85

    rule = _rule_params(
        {
            "rule_id": "rule1",
            "product_id": "gaco_onepass",
            "rule_type": "limitation",
            "confidence": "bad",
        }
    )
    assert rule["confidence"] is None


def test_product_catalog_schema_splitter_handles_multiline_statements() -> None:
    statements = _schema_statements(
        """
        -- comment
        CREATE TABLE IF NOT EXISTS product_catalog (
            product_id TEXT PRIMARY KEY
        );

        ALTER TABLE product_catalog ADD COLUMN IF NOT EXISTS extraction_method TEXT;
        """
    )
    assert len(statements) == 2
    assert statements[0].startswith("CREATE TABLE")
    assert statements[1].startswith("ALTER TABLE")


def test_json_param_preserves_json_strings_and_wraps_plain_text() -> None:
    assert json.loads(_json_param(["a"])) == ["a"]
    assert json.loads(_json_param('["already"]')) == ["already"]
    assert json.loads(_json_param("plain warning")) == ["plain warning"]


def test_local_product_pdf_ingest_matching_links_and_export(tmp_path) -> None:
    pdf_dir = tmp_path / "product_documents"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / "GAF High Solids Silicone PDS.pdf"
    write_pdf(
        pdf_path,
        "\n".join(
            [
                "GAF High Solids Silicone Roof Coating",
                "Product Data Sheet",
                "Recommended use: used for silicone roof coating restoration systems.",
                "Coverage: 100 sq ft per gallon at 16 wet mils.",
                "Metal substrates require primer when rust is present.",
                "Do not use on existing silicone unless adhesion is verified.",
                "Warranty guidance: follow manufacturer application guide.",
                "Revision Date: 2025-02-01",
            ]
        ),
    )

    knowledge = ingest_product_directory(pdf_dir)
    assert len(knowledge.product_catalog) == 1
    product = knowledge.product_catalog[0]
    assert product["manufacturer"] == "GAF"
    assert product["category"] == "roof_coating"
    assert any(row["property_name"] == "coverage_sqft_per_gallon" for row in knowledge.product_properties)
    assert any(row["rule_type"] == "requires_primer" for row in knowledge.product_rules)
    assert any(row["decision_id"] == "roofing_coating_system" for row in knowledge.product_decision_links)

    matched = match_product("GAF High Solids Silicone 55 Gal - Standard Colors", pd.DataFrame(knowledge.product_catalog))
    assert matched["product_id"] == product["product_id"]

    context = product_context_for_decision(
        product_name="GAF High Solids Silicone 55 Gal - Standard Colors",
        decision_id="roofing_coating_system",
        product_catalog=pd.DataFrame(knowledge.product_catalog),
        product_properties=pd.DataFrame(knowledge.product_properties),
        product_rules=pd.DataFrame(knowledge.product_rules),
        product_documents=pd.DataFrame(knowledge.product_documents),
        product_decision_links=pd.DataFrame(knowledge.product_decision_links),
    )
    assert context["product_id"] == product["product_id"]
    assert "silicone roof coating" in context["recommended_use"].lower()
    assert context["coverage"]
    assert context["warnings"]
    assert "roofing_coating_system" in context["linked_decision_nodes"]
    assert context["source_evidence"]

    json_path = tmp_path / "product_catalog.json"
    json_path.write_text(json.dumps(knowledge.to_dict()), encoding="utf-8")
    loaded = load_product_catalog_json(json_path)
    xlsx_path = export_product_catalog_xlsx(loaded, tmp_path / "product_catalog.xlsx")
    assert xlsx_path.exists()


def test_historical_decision_rows_reference_product_ids() -> None:
    catalog = pd.DataFrame(
        [
            {
                "product_id": "gaf_high_solids_silicone",
                "manufacturer": "GAF",
                "product_name": "GAF High Solids Silicone",
                "category": "roof_coating",
                "aliases": ["GAF High Solids Silicone 55 Gal"],
                "active": True,
            }
        ]
    )
    data = EstimatorData(
        product_catalog=catalog,
        product_decision_links=pd.DataFrame(
            [
                {
                    "product_id": "gaf_high_solids_silicone",
                    "decision_id": "roofing_coating_system",
                    "influence_type": "candidate_product",
                }
            ]
        ),
        template_rows=pd.DataFrame(
            [
                {
                    "job_id": "J1",
                    "source_file": "2026/roofing.xlsx",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "sheet_name": "Estimate",
                    "row_number": 26,
                    "template_bucket": "coating",
                    "line_item_kind": "material",
                    "resolved_item_name": "GAF High Solids Silicone 55 Gal",
                    "area_sqft": 10000,
                    "gal_per_100_sqft": 1.5,
                    "estimated_cost": 5000,
                }
            ]
        ),
    )

    tables = build_historical_decision_tables(data)
    coating = tables["roofing_coating_decision_history"]
    assert coating.iloc[0]["product_id"] == "gaf_high_solids_silicone"
    assert coating.iloc[0]["product_match_score"] >= 0.55


def test_product_decision_links_cover_workbench_decision_ids() -> None:
    assert "roofing_primer" in DECISION_LINKS_BY_CATEGORY["primer"]
    assert "insulation_primer" in DECISION_LINKS_BY_CATEGORY["primer"]
    assert "roofing_seam_treatment" in DECISION_LINKS_BY_CATEGORY["sealant"]
    assert "roofing_caulk_detail" in DECISION_LINKS_BY_CATEGORY["sealant"]
    assert "insulation_caulk_sealant" in DECISION_LINKS_BY_CATEGORY["sealant"]
    assert "roofing_fabric" in DECISION_LINKS_BY_CATEGORY["fabric"]
    assert "roofing_granules" in DECISION_LINKS_BY_CATEGORY["granules"]


def test_product_context_uses_decision_link_and_source_evidence() -> None:
    product_id = "gaco_prime"
    context = product_context_for_decision(
        product_name="GacoPrime",
        decision_id="insulation_primer",
        product_catalog=pd.DataFrame(
            [
                {
                    "product_id": product_id,
                    "manufacturer": "Gaco",
                    "product_name": "GacoPrime Low VOC Primer",
                    "category": "primer",
                    "aliases": ["GacoPrime"],
                    "active": True,
                }
            ]
        ),
        product_properties=pd.DataFrame(
            [
                {
                    "product_id": product_id,
                    "property_name": "coverage_sqft_per_gallon",
                    "property_value": "200-250",
                    "unit": "sqft/gal",
                    "source_page": 1,
                    "source_text": "Coverage: 200-250 ft2/gal.",
                }
            ]
        ),
        product_rules=pd.DataFrame(
            [
                {
                    "product_id": product_id,
                    "rule_type": "limitation",
                    "rule_value": "Existing silicone coatings should not be primed.",
                    "severity": "warning",
                    "source_page": 2,
                    "source_text": "Existing silicone coatings should not be primed.",
                }
            ]
        ),
        product_documents=pd.DataFrame(
            [
                {
                    "product_id": product_id,
                    "source_path": "product_documents/GacoPrime.pdf",
                }
            ]
        ),
        product_decision_links=pd.DataFrame(
            [
                {
                    "product_id": product_id,
                    "decision_id": "insulation_primer",
                    "influence_type": "candidate_product",
                }
            ]
        ),
        category="primer",
    )

    assert context["product_id"] == product_id
    assert "Existing silicone" in context["important_limitations"]
    assert context["source_documents"] == ["product_documents/GacoPrime.pdf"]
    assert any(row["source_text"] == "Coverage: 200-250 ft2/gal." for row in context["source_evidence"])


def test_product_match_uses_product_alias_table_rows() -> None:
    matched = match_product(
        "DC 315 TB",
        pd.DataFrame(
            [
                {
                    "product_id": "ift_dc315",
                    "manufacturer": "International Fireproof Technology",
                    "product_name": "DC315 Intumescent Coating",
                    "category": "thermal_barrier",
                    "active": True,
                }
            ]
        ),
        category="thermal_barrier",
        decision_id="insulation_thermal_barrier",
        product_aliases=pd.DataFrame(
            [
                {
                    "product_id": "ift_dc315",
                    "alias": "DC 315 TB",
                    "alias_type": "historical_template_row",
                    "confidence": 0.95,
                }
            ]
        ),
    )

    assert matched["product_id"] == "ift_dc315"
    assert matched["match_strategy"] == "exact_product_or_alias"
    assert matched["matched_name"] == "DC 315 TB"


def test_product_context_uses_template_product_option_links() -> None:
    context = product_context_for_decision(
        product_name="Gaco 2.0",
        decision_id="insulation_foam_system",
        product_catalog=pd.DataFrame(
            [
                {
                    "product_id": "gaco_onepass",
                    "manufacturer": "Gaco",
                    "product_name": "GacoOnePass Closed Cell Spray Foam",
                    "category": "spray_foam",
                    "active": True,
                }
            ]
        ),
        template_product_links=pd.DataFrame(
            [
                {
                    "template_product_option_id": "tplopt_gaco_2lb",
                    "product_id": "gaco_onepass",
                    "review_status": "approved",
                }
            ]
        ),
        template_product_option_id="tplopt_gaco_2lb",
        category="foam",
    )

    assert context["product_id"] == "gaco_onepass"
    assert context["match_strategy"] == "template_product_option_link"
    assert context["match_score"] >= 0.98


def test_product_match_rejects_weak_cross_category_fuzzy_match() -> None:
    matched = match_product(
        "GACO 2.0",
        pd.DataFrame(
            [
                {
                    "product_id": "gaco_roof_coating",
                    "manufacturer": "Gaco",
                    "product_name": "Gaco Silicone Roof Coating",
                    "category": "roof_coating",
                    "active": True,
                }
            ]
        ),
        category="foam",
        decision_id="insulation_foam_system",
    )

    assert matched == {}


def test_product_mapping_audit_generates_alias_and_template_link_candidates() -> None:
    data = EstimatorData(
        product_catalog=pd.DataFrame(
            [
                {
                    "product_id": "gaco_onepass",
                    "manufacturer": "Gaco",
                    "product_name": "GacoOnePass Closed Cell Spray Foam",
                    "category": "spray_foam",
                    "active": True,
                    "aliases": ["Gaco 2.0 lb"],
                }
            ]
        ),
        template_product_options=pd.DataFrame(
            [
                {
                    "template_product_option_id": "tplopt_gaco_2lb",
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "row_number": 19,
                    "selector_code": "11",
                    "product_name": "Gaco 2.0 lb.",
                }
            ]
        ),
    )

    audit = collect_product_mapping_audit(data)
    aliases = proposed_product_aliases(data, audit)
    links = proposed_template_product_links(audit)

    assert audit.iloc[0]["matched_product_id"] == "gaco_onepass"
    assert aliases.iloc[0]["alias"] == "Gaco 2.0 lb."
    assert links.iloc[0]["template_product_option_id"] == "tplopt_gaco_2lb"
    assert links.iloc[0]["product_id"] == "gaco_onepass"


def test_product_document_queue_discovers_local_docs_and_writes_csv(tmp_path) -> None:
    docs = tmp_path / "product_documents"
    docs.mkdir()
    (docs / "GacoPrime PDS.pdf").write_text("fake pdf text", encoding="utf-8")
    (docs / "Gaco SDS.txt").write_text("fake sds text", encoding="utf-8")
    (docs / "ignore.csv").write_text("not a product doc", encoding="utf-8")

    rows = discover_product_documents(docs, manufacturer_hint="Gaco")
    assert len(rows) == 2
    assert {row["document_type"] for row in rows} == {"PDS", "SDS"}
    assert all(row["ingest_status"] == "pending" for row in rows)
    assert all(row["approved_for_ingest"] is True for row in rows)
    assert all(row["discovery_method"] == "local_folder" for row in rows)
    out = write_queue_csv(rows, tmp_path / "queue.csv")
    assert out.exists()


def test_product_document_queue_records_controlled_approved_url_metadata() -> None:
    approved = queue_product_document_url(
        "https://www.gaco.com/documents/PDS-GACOPRIME.pdf",
        manufacturer_hint="Gaco",
        approved_domains=["gaco.com"],
        decision_nodes=["roofing_primer"],
    )
    blocked = queue_product_document_url(
        "https://example.net/random.pdf",
        manufacturer_hint="Unknown",
        approved_domains=["gaco.com"],
    )

    assert is_approved_document_url("https://sub.gaco.com/product.pdf", ["gaco.com"])
    assert approved["domain_approved"] is True
    assert approved["approved_for_ingest"] is True
    assert approved["review_status"] == "approved"
    assert approved["decision_nodes"] == ["roofing_primer"]
    assert blocked["domain_approved"] is False
    assert blocked["ingest_status"] == "blocked_domain_review"
    assert "Domain not approved" in blocked["validation_warnings"][0]


def test_product_family_lookup_seed_loads_and_builds_controlled_queue() -> None:
    rows = load_product_family_lookup()
    families = {row["canonical_product_family"]: row for row in rows}
    by_lookup_id = {row["lookup_id"]: row for row in rows}

    assert len(rows) >= 40
    assert families["GacoPrime"]["vendor"] == "Gaco"
    assert families["GacoPrime"]["domain_approved"] is True
    assert "roofing_primer" in families["GacoPrime"]["decision_nodes"]
    assert "insulation_primer" in families["GacoPrime"]["decision_nodes"]
    assert families["GacoOnePass"]["template_option"] == "Gaco 2.0 lb"
    assert families["GacoOnePass"]["cell_type"] == "closed_cell"
    assert families["GacoOnePass"]["density_class"] == "2.0 lb"
    assert families["GacoOnePass"]["priority"] == 20
    assert "insulation_foam_system" in families["GacoOnePass"]["decision_nodes"]
    assert families["GacoRoofFoam F2733 RHFO"]["template_option"] == "Gaco Roof 2.7"
    assert families["GacoRoofFoam F2733 RHFO"]["mapping_status"] == "approved"
    assert families["GacoRoofFoam F2733 RHFO"]["alias_policy"] == "auto_alias_after_doc_ingest"
    assert by_lookup_id["ift_dc315"]["template_option"] == "DC315 TB"
    assert by_lookup_id["ift_dc315"]["vendor_product_url"] == "https://www.painttoprotect.com/"
    assert families["WALLTITE"]["domain_approved"] is True
    assert families["PSI closed-cell SPF"]["domain_approved"] is False
    assert "insulation_thermal_barrier" in families["DC315"]["decision_nodes"]
    assert "roofing_granules" in families["Mineral Shield Granules"]["decision_nodes"]

    queue_rows = build_document_queue_from_lookup(rows)
    urls = [row["source_url"] for row in queue_rows]
    assert len(urls) == len(set(urls))
    assert all(row["discovery_method"] == "product_family_lookup" for row in queue_rows)
    gaco_home = next(row for row in queue_rows if row["source_url"] == "https://gaco.com/")
    assert "GacoFlex S42" in gaco_home["notes"]
    assert "GacoOnePass [Gaco 2.0 lb]" in gaco_home["notes"]
    assert "preferred docs: PDS; application guide; installation guide; SDS" in gaco_home["notes"]
    assert len(gaco_home["lookup_ids"]) > 1
    assert "roofing_coating_system" in gaco_home["decision_nodes"]
    assert "insulation_foam_system" in gaco_home["decision_nodes"]


def test_product_family_lookup_infers_decision_nodes_from_terms() -> None:
    assert infer_decision_nodes("GAF", "Premium Fabric", "GAF Premium Fabric PDS roof coating fabric") == [
        "roofing_coating_system",
        "roofing_fabric",
        "roofing_seam_treatment",
    ]
    assert "insulation_foam_system" in infer_decision_nodes(
        "NCFI",
        "InsulStarLight",
        "NCFI InsulStarLight PDS open cell spray foam",
    )


def test_controlled_scraper_finds_approved_pds_links_from_seed_page() -> None:
    rows = [row for row in load_product_family_lookup() if row["canonical_product_family"] == "GacoPrime"]
    html = """
    <html><body>
      <a href="/documents/PDS-GacoPrime-Low-VOC-Primer.pdf">GacoPrime Low VOC Primer PDS</a>
      <a href="https://example.com/PDS-GacoPrime.pdf">offsite duplicate</a>
      <a href="/documents/GacoPrime-SDS.pdf">GacoPrime SDS</a>
    </body></html>
    """

    candidates = candidate_links_for_page(
        page_url="https://gaco.com/product/gacoprime/",
        html=html,
        lookup_rows=rows,
    )

    assert candidates
    assert candidates[0].url == "https://gaco.com/documents/PDS-GacoPrime-Low-VOC-Primer.pdf"
    assert candidates[0].matched_lookup_ids == ["gaco_gacoprime"]
    assert "roofing_primer" in candidates[0].decision_nodes
    assert all("example.com" not in candidate.url for candidate in candidates)


def test_scrape_product_family_lookup_builds_discovered_queue_rows() -> None:
    rows = [row for row in load_product_family_lookup() if row["canonical_product_family"] == "GacoPrime"]

    def fetcher(url: str) -> FetchResult:
        assert url == "https://gaco.com/product/gacoprime/"
        return FetchResult(
            url=url,
            content=b'<a href="/documents/PDS-GacoPrime-Low-VOC-Primer.pdf">GacoPrime PDS</a>',
            content_type="text/html",
        )

    queue_rows, diagnostics = scrape_product_family_lookup(rows, fetcher=fetcher)

    assert diagnostics[0]["status"] == "scraped"
    assert len(queue_rows) == 1
    row = queue_rows[0]
    assert row["source_url"] == "https://gaco.com/documents/PDS-GacoPrime-Low-VOC-Primer.pdf"
    assert row["source_type"] == "discovered_product_document_url"
    assert row["discovery_method"] == "approved_domain_scrape"
    assert row["lookup_ids"] == ["gaco_gacoprime"]
    assert row["scrape_score"] > 0


def test_download_queue_documents_writes_approved_pdf(tmp_path) -> None:
    row = queue_product_document_url(
        "https://gaco.com/documents/PDS-GacoPrime-Low-VOC-Primer.pdf",
        manufacturer_hint="Gaco",
        decision_nodes=["roofing_primer"],
    )

    def fetcher(url: str) -> FetchResult:
        return FetchResult(url=url, content=b"%PDF-1.4 fake", content_type="application/pdf")

    downloaded = download_queue_documents([row], out_dir=tmp_path, fetcher=fetcher)

    assert downloaded[0]["ingest_status"] == "downloaded"
    assert downloaded[0]["content_hash"]
    assert downloaded[0]["source_path"].endswith(".pdf")
    assert (tmp_path / downloaded[0]["source_path"].split("/")[-1]).exists()


def test_ai_structured_gacoprime_extracts_primer_properties_and_rules(tmp_path, monkeypatch) -> None:
    pdf_path = tmp_path / "GacoPrime Low VOC Primer PDS.pdf"
    write_pdf(
        pdf_path,
        "\n".join(
            [
                "GacoPrime Low VOC Primer",
                "Product Data Sheet",
                "Coverage: 200-250 ft²/gal at 6-8 wet mils.",
                "Topcoat after primer has dried, normally within 24 hours.",
                "Limitations: Not for asphalt, concrete, or rough porous substrates.",
                "Existing silicone coatings should not be primed.",
            ]
        ),
    )
    payload = {
        "manufacturer": "Gaco",
        "product_name": "GacoPrime Low VOC Primer",
        "category": "primer",
        "document_type": "PDS",
        "coverage_rates": [{"value": "200-250 ft²/gal", "unit": "ft²/gal", "source_page": 1}],
        "wet_mils": [{"value": "6-8 mils", "unit": "mils", "source_page": 1}],
        "topcoat_windows": [{"value": "24 hours", "source_page": 1}],
        "limitations": [
            {"value": "Not for asphalt, concrete, or rough porous substrates.", "source_page": 1},
            {"value": "Existing silicone coatings should not be primed.", "source_page": 1},
        ],
        "source_evidence": [
            {
                "field": "product_name",
                "value": "GacoPrime Low VOC Primer",
                "source_page": 1,
                "source_text_excerpt": "GacoPrime Low VOC Primer Product Data Sheet",
            },
            {
                "field": "coverage_rates",
                "value": "200-250 ft²/gal",
                "source_page": 1,
                "source_text_excerpt": "Coverage: 200-250 ft²/gal at 6-8 wet mils.",
            },
            {
                "field": "wet_mils",
                "value": "6-8 mils",
                "source_page": 1,
                "source_text_excerpt": "Coverage: 200-250 ft²/gal at 6-8 wet mils.",
            },
            {
                "field": "topcoat_windows",
                "value": "24 hours",
                "source_page": 1,
                "source_text_excerpt": "Topcoat after primer has dried, normally within 24 hours.",
            },
            {
                "field": "limitations",
                "value": "Not for asphalt, concrete, or rough porous substrates.",
                "source_page": 1,
                "source_text_excerpt": "Limitations: Not for asphalt, concrete, or rough porous substrates.",
            },
            {
                "field": "limitations",
                "value": "Existing silicone coatings should not be primed.",
                "source_page": 1,
                "source_text_excerpt": "Existing silicone coatings should not be primed.",
            },
        ],
        "confidence_by_field": {"product_name": "high", "coverage_rates": "high", "limitations": "high"},
    }
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(product_ingest_module, "parse_product_document_with_ai", lambda *args, **kwargs: payload)

    knowledge = ingest_product_document(pdf_path, use_ai=True, manufacturer_hint="Gaco")
    assert knowledge.product_catalog[0]["product_name"] == "GacoPrime Low VOC Primer"
    assert knowledge.product_catalog[0]["category"] == "primer"
    assert knowledge.product_catalog[0]["extraction_method"] == "ai_structured"

    coverage = next(row for row in knowledge.product_properties if row["property_name"] == "coverage_sqft_per_gallon")
    assert coverage["unit"] == "sqft/gal"
    assert coverage["numeric_min"] == 200
    assert coverage["numeric_max"] == 250
    wet_mils = next(row for row in knowledge.product_properties if row["property_name"] == "wet_mils")
    assert wet_mils["numeric_min"] == 6
    assert wet_mils["numeric_max"] == 8
    topcoat = next(row for row in knowledge.product_properties if row["property_name"] == "topcoat_window")
    assert topcoat["numeric_value"] == 24

    limitation_text = " ".join(row["rule_value"] for row in knowledge.product_rules if row["rule_type"] == "limitation").lower()
    assert "asphalt" in limitation_text
    assert "concrete" in limitation_text
    assert "existing silicone" in limitation_text


def test_ai_structured_gacorooffoam_extracts_density_r_values_and_pass_thickness(tmp_path, monkeypatch) -> None:
    pdf_path = tmp_path / "GacoRoofFoam Low GWP F2780 PDS.pdf"
    write_pdf(
        pdf_path,
        "\n".join(
            [
                "GacoRoofFoam Low GWP F2780",
                "Sprayed-in-place density: 2.7-3.4 pcf.",
                "Initial R-value 6.5 per inch. Aged R-value 5.7 per inch.",
                "Apply in passes from 0.75-1.5 in.",
            ]
        ),
    )
    payload = {
        "manufacturer": "Gaco",
        "product_name": "GacoRoofFoam Low GWP F2780",
        "category": "spray foam / roofing foam",
        "document_type": "PDS",
        "density": [{"value": "2.7-3.4 pcf sprayed-in-place", "unit": "pcf", "source_page": 1}],
        "r_values": [
            {"value": "initial 6.5 per inch", "unit": "R per inch", "source_page": 1},
            {"value": "aged 5.7 per inch", "unit": "R per inch", "source_page": 1},
        ],
        "pass_thickness": [{"value": "0.75-1.5 in", "unit": "in", "source_page": 1}],
        "source_evidence": [
            {
                "field": "product_name",
                "value": "GacoRoofFoam Low GWP F2780",
                "source_page": 1,
                "source_text_excerpt": "GacoRoofFoam Low GWP F2780",
            },
            {
                "field": "density",
                "value": "2.7-3.4 pcf sprayed-in-place",
                "source_page": 1,
                "source_text_excerpt": "Sprayed-in-place density: 2.7-3.4 pcf.",
            },
            {
                "field": "r_values",
                "value": "initial 6.5 per inch",
                "source_page": 1,
                "source_text_excerpt": "Initial R-value 6.5 per inch. Aged R-value 5.7 per inch.",
            },
            {
                "field": "pass_thickness",
                "value": "0.75-1.5 in",
                "source_page": 1,
                "source_text_excerpt": "Apply in passes from 0.75-1.5 in.",
            },
        ],
    }
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(product_ingest_module, "parse_product_document_with_ai", lambda *args, **kwargs: payload)

    knowledge = ingest_product_document(pdf_path, use_ai=True, manufacturer_hint="Gaco")
    assert knowledge.product_catalog[0]["product_name"] == "GacoRoofFoam Low GWP F2780"
    assert knowledge.product_catalog[0]["category"] == "spray_foam"
    density = next(row for row in knowledge.product_properties if row["property_name"] == "density")
    assert density["unit"] == "pcf"
    assert density["numeric_min"] == 2.7
    assert density["numeric_max"] == 3.4
    r_values = [row for row in knowledge.product_properties if row["property_name"] == "R_value"]
    assert {row["numeric_value"] for row in r_values} >= {6.5, 5.7}
    pass_thickness = next(row for row in knowledge.product_properties if row["property_name"] == "pass_thickness")
    assert pass_thickness["numeric_min"] == 0.75
    assert pass_thickness["numeric_max"] == 1.5


def test_product_document_validation_flags_suspicious_names_and_writes_xlsx(tmp_path) -> None:
    assert is_suspicious_product_name("Gaco.com | 800-331-0196")
    report = validate_product_catalog(
        ProductKnowledge(
            product_catalog=[
                {
                    "product_id": "bad",
                    "manufacturer": "Gaco",
                    "product_name": "Gaco.com | 800-331-0196",
                    "category": "primer",
                }
            ],
            product_rules=[
                {
                    "rule_id": "bad_rule",
                    "product_id": "bad",
                    "rule_type": "limitation",
                    "rule_value": "LIMITATIONS:",
                    "source_text": "LIMITATIONS:",
                    "confidence": 0.9,
                }
            ],
        )
    )
    issues = {row["issue_type"] for row in report["Validation Warnings"]}
    assert "suspicious_product_name" in issues
    assert "section_heading_source_text" in issues
    out = write_validation_workbook(report, tmp_path / "validation.xlsx")
    assert out.exists()


def test_measure_normalization_parses_ranges_and_units() -> None:
    assert normalize_extracted_measure("200-250 ft²/gal") == {
        "value": "200-250 ft²/gal",
        "unit": "sqft/gal",
        "numeric_value": 225.0,
        "numeric_min": 200.0,
        "numeric_max": 250.0,
    }
    assert normalize_extracted_measure({"minimum": "0.75 in", "maximum": "1.5 in", "units": "inches"}) == {
        "value": "0.75-1.5 inches",
        "unit": "inches",
        "numeric_value": 1.125,
        "numeric_min": 0.75,
        "numeric_max": 1.5,
    }
