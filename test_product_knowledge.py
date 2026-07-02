from __future__ import annotations

import json

import pandas as pd
import pytest

from jobscan.estimator.decision_history import build_historical_decision_tables
from jobscan.estimator.schemas import EstimatorData
from jobscan.products.ai_document_parser import is_suspicious_product_name, normalize_extracted_measure
from jobscan.products.document_queue import discover_product_documents, write_queue_csv
from jobscan.products.product_catalog import ProductKnowledge, export_product_catalog_xlsx, load_product_catalog_json
from jobscan.products.product_ingest import ingest_product_directory, ingest_product_document
from jobscan.products import product_ingest as product_ingest_module
from jobscan.products.product_matching import match_product, product_context_for_decision
from jobscan.products.product_rules import DECISION_LINKS_BY_CATEGORY
from jobscan.products.validate_catalog import validate_product_catalog, write_validation_workbook


def write_pdf(path, text: str) -> None:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=10)
    doc.save(path)


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
    out = write_queue_csv(rows, tmp_path / "queue.csv")
    assert out.exists()


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
