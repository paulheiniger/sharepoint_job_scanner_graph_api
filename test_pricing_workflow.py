from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import pytest

from jobscan.pricing import core


def write_csv(path: Path, rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_sectioned_master_csv_can_be_read(tmp_path: Path) -> None:
    master = tmp_path / "Pricing Sheet (MASTER 2026)(Sheet1).csv"
    write_csv(
        master,
        [
            ["Vendor Cost Sheet", "", ""],
            ["Effective Date: April 2026", "", ""],
            ["Polyurethane Foam", "Cost", "Date"],
            ["Gaco Roof Foam 2733", "1.99", "3/16/2026"],
            ["Gaco Coatings/Primers", "", ""],
            ["S20 Silicone", "32.00", "3/16/2026"],
        ],
    )

    rows = core.extract_pricing_file(master)

    assert [row["product_name"] for row in rows] == ["Gaco Roof Foam 2733", "S20 Silicone"]
    assert rows[0]["category"] == "Polyurethane Foam"
    assert rows[0]["unit_price"] == 1.99
    assert rows[0]["effective_date"] == "2026-03-16"


def test_header_csv_and_xlsx_pricing_sheets_are_parsed(tmp_path: Path) -> None:
    csv_path = tmp_path / "vendor.csv"
    write_csv(csv_path, [["Product", "Price", "Unit", "Vendor"], ["Acrylic 100", "42.50", "gal", "Vendor A"]])
    xlsx_path = tmp_path / "vendor.xlsx"
    pd.DataFrame([["Product", "Price", "Unit"], ["Primer 200", 12.25, "pail"]]).to_excel(xlsx_path, header=False, index=False)

    csv_rows = core.extract_pricing_file(csv_path)
    xlsx_rows = core.extract_pricing_file(xlsx_path)

    assert csv_rows[0]["product_name"] == "Acrylic 100"
    assert csv_rows[0]["unit_of_measure"] == "gal"
    assert xlsx_rows[0]["product_name"] == "Primer 200"
    assert xlsx_rows[0]["unit_price"] == 12.25


def test_pdf_text_lines_extract_structured_and_skips_ambiguous_notes(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Coatings - Terr 763 - Eff 5.6.25.pdf"
    pdf_path.write_text("GAF Silicone 5 gallon pail $275.00\nFreight terms included\nAmbiguous coating note\n", encoding="utf-8")

    rows = core.extract_pricing_file(pdf_path)

    structured = [row for row in rows if row["product_name"].startswith("GAF Silicone")]
    assert structured
    assert structured[0]["vendor"] == "GAF"
    assert structured[0]["unit_price"] == 275.0
    assert structured[0]["category"] == "Coatings"
    assert '"page_number": 1' in structured[0]["details"]
    assert "Freight terms included" not in {row["product_name"] for row in rows}
    assert "Ambiguous coating note" not in {row["product_name"] for row in rows}


def test_pdf_narrative_line_prefers_currency_price_over_case_count() -> None:
    parsed = core.parse_pdf_pricing_line("Large $6.73 each / case of 6")

    assert parsed is not None
    assert parsed["unit_price"] == 6.73
    assert parsed["unit_of_measure"] == "case"


def test_reconcile_flags_new_price_changed_duplicates_missing_and_review() -> None:
    master = [
        core.normalize_price_row({"product_name": "S20 Silicone", "category": "Coatings", "unit_price": 32.00}),
        core.normalize_price_row({"product_name": "Old Primer", "category": "Primers", "unit_price": 10.00}),
        core.normalize_price_row({"product_name": "Close Match Coating", "category": "Coatings", "unit_price": 50.00}),
    ]
    source = [
        core.normalize_price_row({"product_name": "S20 Silicone", "category": "Coatings", "unit_price": 35.00}),
        core.normalize_price_row({"product_name": "Brand New Foam", "category": "Foam", "unit_price": 20.00}),
        core.normalize_price_row({"product_name": "Close Match Coatng", "category": "Coatings", "unit_price": 50.00}),
        core.normalize_price_row({"product_name": "Needs Human", "unit_price": "", "needs_review": True, "parser_confidence": 0.3}),
    ]

    review, draft = core.reconcile_pricing(master, source)
    flags = {row["product_name"] or row["current_product_name"]: row["action_flags"] for row in review}

    assert "price_changed" in flags["S20 Silicone"]
    assert "new_item" in flags["Brand New Foam"]
    assert "possible_duplicate" in flags["Close Match Coatng"]
    assert "needs_review" in flags["Needs Human"]
    assert "missing_from_new_source" in flags["Old Primer"]
    assert any(row["product_name"] == "Brand New Foam" for row in draft)
    assert any(row["product_name"] == "S20 Silicone" and row["unit_price"] == 35.0 for row in draft)


def test_reconcile_does_not_auto_merge_low_confidence_matches() -> None:
    master = [core.normalize_price_row({"product_name": "Alpha Coating", "unit_price": 10})]
    source = [core.normalize_price_row({"product_name": "Alpha Coat", "unit_price": 11})]

    review, draft = core.reconcile_pricing(master, source)

    assert any("possible_duplicate" in row["action_flags"] for row in review)
    assert draft[0]["product_name"] == "Alpha Coating"
    assert draft[0]["unit_price"] == 10


def test_extract_and_reconcile_cli_outputs(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    source_file = input_dir / "vendor.csv"
    master_file = tmp_path / "master.csv"
    extracted = tmp_path / "out" / "pricing_source_items.csv"
    review = tmp_path / "out" / "pricing_master_update_review.csv"
    draft = tmp_path / "out" / "pricing_master_updated_draft.csv"
    write_csv(source_file, [["Product", "Price", "Unit"], ["Acrylic 100", "42.50", "gal"]])
    write_csv(master_file, [["Product", "Price", "Unit"], ["Acrylic 100", "40.00", "gal"]])

    core.run_extract_cli(["--input-dir", str(input_dir), "--out", str(extracted)])
    core.run_reconcile_cli(["--master", str(master_file), "--source", str(extracted), "--out", str(review), "--draft-out", str(draft)])

    assert extracted.exists()
    assert review.exists()
    assert draft.exists()
    assert "price_changed" in read_csv(review)[0]["action_flags"]


def test_reconcile_cli_refuses_to_overwrite_source_files(tmp_path: Path) -> None:
    source_file = tmp_path / "source.csv"
    master_file = tmp_path / "master.csv"
    write_csv(source_file, [["Product", "Price"], ["Acrylic 100", "42.50"]])
    write_csv(master_file, [["Product", "Price"], ["Acrylic 100", "40.00"]])

    with pytest.raises(SystemExit):
        core.run_reconcile_cli(["--master", str(master_file), "--source", str(source_file), "--out", str(master_file)])
