from __future__ import annotations

import builtins
import csv
import json
from pathlib import Path

import pandas as pd

from jobscan import pricing_loader as pl


def write_csv(path: Path, rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def master_fixture(path: Path) -> None:
    write_csv(
        path,
        [
            ["Vendor Cost Sheet", "", ""],
            ["Effective Date: April 2026", "", ""],
            ["Gaco Coatings/Primers", "Cost", "Date"],
            ["S20 Silicone", "32.00", "3/16/2026"],
            ["Needs Review Product", "", ""],
        ],
    )


def pdf_text_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "GAF Roof Coatings",
                "As of 05/06/2025",
                "Silicone Roofing Products",
                "Price",
                "UOM",
                "GAF Unisil 5 Gal - Standard Colors",
                "$185.00",
                "Pail",
                "Ambiguous coating note",
            ]
        ),
        encoding="utf-8",
    )


def test_pricing_catalog_migration_exists_and_is_idempotent() -> None:
    sql = Path("db/add_pricing_catalog_tables.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS pricing_catalog" in sql
    assert "CREATE TABLE IF NOT EXISTS pricing_source_files" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_pricing_catalog_vendor" in sql


def test_pricing_loader_normalization_price_date_and_stable_id() -> None:
    row = {
        "vendor": " GAF ",
        "category": " Coatings ",
        "product_name": "GAF High Solids Silicone 5 Gal",
        "product_name_normalized": pl.normalize_product_name("GAF High-Solids Silicone 5 Gal"),
        "unit_of_measure": "pail",
        "package_size": "5 Gal",
        "source_file": "master.csv",
    }

    assert pl.normalize_product_name("GAF High-Solids Silicone 5 Gal") == "gaf high solids silicone 5 gal"
    assert pl.safe_number("$1,234.50") == 1234.5
    assert pl.safe_date("3/16/2026") == "2026-03-16"
    assert pl.stable_pricing_item_id(row) == pl.stable_pricing_item_id(dict(row))


def test_loader_reads_master_csv_fixture_and_preserves_raw_row_json(tmp_path: Path) -> None:
    path = tmp_path / "Pricing Sheet (MASTER 2026)(Sheet1).csv"
    master_fixture(path)

    rows, skipped = pl.load_input_rows(path, mark_current=True)

    assert skipped == 0
    assert len(rows) == 1
    row = rows[0]
    assert row["product_name"] == "S20 Silicone"
    assert row["unit_price"] == 32.0
    assert row["effective_date"] == "2026-03-16"
    raw = json.loads(row["raw_row_json"])
    assert raw["raw_row"][0] == "S20 Silicone"
    assert raw["extracted_row"]["product_name"] == "S20 Silicone"


def test_prepare_pricing_row_sets_needs_review_for_incomplete_price(tmp_path: Path) -> None:
    row = pl.prepare_pricing_row(
        {"product_name": "Mystery Coating", "unit_price": "", "source_type": "csv"},
        source_path=tmp_path / "source.csv",
        raw_rows={1: ["Mystery Coating", ""]},
    )

    assert row is not None
    assert row["needs_review"] is True


class FakeRows:
    def __init__(self, rows, rowcount=0):
        self.rows = rows
        self.rowcount = rowcount

    def fetchall(self):
        return self.rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class FakeConnection:
    def __init__(self):
        self.pricing_ids: set[str] = set()
        self.pricing_rows: dict[str, dict[str, object]] = {}
        self.export_rows: list[dict[str, object]] = []
        self.executed = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append((sql, params))
        if "FROM pricing_catalog" in sql and "COALESCE(is_current, false) IS TRUE" in sql:
            return FakeRows(self.export_rows)
        if "SELECT pricing_item_id FROM pricing_catalog" in sql:
            ids = params.get("ids", []) if isinstance(params, dict) else []
            return FakeRows([(item_id,) for item_id in ids if item_id in self.pricing_ids])
        if "INSERT INTO pricing_catalog" in sql:
            rows = params if isinstance(params, list) else [params]
            for row in rows:
                self.pricing_ids.add(row["pricing_item_id"])
                existing = self.pricing_rows.get(row["pricing_item_id"], {})
                self.pricing_rows[row["pricing_item_id"]] = {**existing, **row}
        if "UPDATE pricing_catalog" in sql and "source_file = :source_file" in sql:
            source_file = params.get("source_file") if isinstance(params, dict) else None
            source_type = str(params.get("source_type") or "").lower() if isinstance(params, dict) else ""
            count = 0
            for row in self.pricing_rows.values():
                if row.get("source_file") == source_file and str(row.get("source_type") or "").lower() == source_type:
                    if row.get("status") != "inactive" or row.get("is_current") is not False or row.get("needs_review") is not True:
                        row["status"] = "inactive"
                        row["is_current"] = False
                        row["needs_review"] = True
                        row["review_notes"] = params.get("review_notes")
                        count += 1
            return FakeRows([], rowcount=count)
        if "UPDATE pricing_catalog" in sql and "LOWER(COALESCE(source_type" in sql:
            return FakeRows([], rowcount=3)
        return FakeRows([])


class FakeBegin:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeEngine:
    def __init__(self):
        self.conn = FakeConnection()

    def begin(self):
        return FakeBegin(self.conn)


def test_load_pricing_upsert_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "master.csv"
    master_fixture(path)
    engine = FakeEngine()

    first = pl.load_pricing(engine, [path])
    second = pl.load_pricing(engine, [path])

    assert first.rows_inserted == 1
    assert first.rows_updated == 0
    assert second.rows_inserted == 0
    assert second.rows_updated == 1


def test_pdf_file_discovery_from_input_dir(tmp_path: Path) -> None:
    csv_path = tmp_path / "master.csv"
    pdf_path = tmp_path / "Coatings - Terr 763 - Eff 5.6.25.pdf"
    master_fixture(csv_path)
    pdf_text_fixture(pdf_path)

    paths = pl.input_paths(None, tmp_path)

    assert csv_path in paths
    assert pdf_path in paths


def test_loader_extracts_machine_readable_pdf_rows_with_source_page(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Coatings - Terr 763 - Eff 5.6.25.pdf"
    pdf_text_fixture(pdf_path)

    rows, skipped = pl.load_input_rows(pdf_path, mark_current=True)
    products = {row["product_name"]: row for row in rows}

    assert skipped == 0
    assert "GAF Unisil 5 Gal - Standard Colors" in products
    row = products["GAF Unisil 5 Gal - Standard Colors"]
    assert row["source_file"] == pdf_path.name
    assert row["source_type"] == "pdf"
    assert row["source_page"] == 1
    assert row["vendor"] == "GAF"
    assert row["unit_price"] == 185.0
    assert row["unit_of_measure"] == "pail"
    assert row["package_size"] == "5 gal"
    assert row["price_per_gallon"] == 37.0
    assert row["effective_date"] == "2025-05-06"
    raw = json.loads(row["raw_row_json"])
    assert raw["source_details"]["page_number"] == 1
    assert raw["source_details"]["source_line"] == "GAF Unisil 5 Gal - Standard Colors"


def test_ambiguous_pdf_line_becomes_needs_review(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Spray Tec Pricing Information 2025.pdf"
    pdf_path.write_text("Spray Tec Pricing\nAmbiguous coating note\n", encoding="utf-8")

    rows, _skipped = pl.load_input_rows(pdf_path)

    assert rows == []


def test_pdf_load_is_idempotent_and_reports_pdf_counts(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Coatings - Terr 763 - Eff 5.6.25.pdf"
    pdf_text_fixture(pdf_path)
    engine = FakeEngine()

    first = pl.load_pricing(engine, [pdf_path], mark_current=True)
    second = pl.load_pricing(engine, [pdf_path], mark_current=True)

    assert first.pdf_files_discovered == 1
    assert first.pdf_files_parsed == 1
    assert first.pdf_pages_read >= 1
    assert first.pdf_product_rows_extracted >= 1
    assert first.pdf_notes_skipped >= 1
    assert first.pdf_rows_loaded == first.rows_inserted + first.rows_updated
    assert second.rows_inserted == 0
    assert second.rows_updated == first.rows_inserted


def test_pdf_reload_retires_existing_rows_for_same_source_only(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Coatings - Terr 763 - Eff 5.6.25.pdf"
    pdf_path.write_text("Silicone Roofing Products\nGAF High Solids Silicone 5 Gal\n$190.00\nPail\n", encoding="utf-8")
    engine = FakeEngine()
    engine.conn.pricing_rows = {
        "old-bad": {
            "pricing_item_id": "old-bad",
            "product_name": "GAF High Solids Silicone 5 Gal",
            "source_file": pdf_path.name,
            "source_type": "pdf",
            "unit_of_measure": "gallon",
            "package_size": "5 Gal",
            "price_per_gallon": 190.0,
            "status": "active",
            "is_current": True,
            "needs_review": False,
        },
        "csv-row": {
            "pricing_item_id": "csv-row",
            "product_name": "Master Row",
            "source_file": "Pricing Sheet (MASTER 2026)(Sheet1).csv",
            "source_type": "csv",
            "status": "active",
            "is_current": True,
            "needs_review": False,
        },
        "other-pdf": {
            "pricing_item_id": "other-pdf",
            "product_name": "Other PDF Row",
            "source_file": "Other.pdf",
            "source_type": "pdf",
            "status": "active",
            "is_current": True,
            "needs_review": False,
        },
    }

    result = pl.load_pricing(engine, [pdf_path], mark_current=True)

    assert result.source_rows_retired == 1
    assert engine.conn.pricing_rows["old-bad"]["status"] == "inactive"
    assert engine.conn.pricing_rows["old-bad"]["is_current"] is False
    assert engine.conn.pricing_rows["old-bad"]["needs_review"] is True
    assert engine.conn.pricing_rows["old-bad"]["review_notes"] == "Retired before PDF source reload"
    assert engine.conn.pricing_rows["csv-row"]["status"] == "active"
    assert engine.conn.pricing_rows["other-pdf"]["status"] == "active"
    new_rows = [row for key, row in engine.conn.pricing_rows.items() if key not in {"old-bad", "csv-row", "other-pdf"}]
    assert len(new_rows) == 1
    assert new_rows[0]["status"] == "active"
    assert new_rows[0]["unit_of_measure"] == "pail"
    assert new_rows[0]["price_per_gallon"] == 38.0


def test_csv_load_does_not_replace_source_without_flag(tmp_path: Path) -> None:
    path = tmp_path / "master.csv"
    master_fixture(path)
    engine = FakeEngine()
    engine.conn.pricing_rows = {
        "old-csv": {
            "pricing_item_id": "old-csv",
            "product_name": "Old CSV",
            "source_file": path.name,
            "source_type": "csv",
            "status": "active",
            "is_current": True,
            "needs_review": False,
        }
    }

    result = pl.load_pricing(engine, [path], mark_current=True)

    assert result.source_rows_retired == 0
    assert engine.conn.pricing_rows["old-csv"]["status"] == "active"


def test_replace_source_retires_same_csv_source_when_explicit(tmp_path: Path) -> None:
    path = tmp_path / "master.csv"
    master_fixture(path)
    engine = FakeEngine()
    engine.conn.pricing_rows = {
        "old-csv": {
            "pricing_item_id": "old-csv",
            "product_name": "Old CSV",
            "source_file": path.name,
            "source_type": "csv",
            "status": "active",
            "is_current": True,
            "needs_review": False,
        }
    }

    result = pl.load_pricing(engine, [path], mark_current=True, replace_source=True)

    assert result.source_rows_retired == 1
    assert engine.conn.pricing_rows["old-csv"]["status"] == "inactive"
    assert engine.conn.pricing_rows["old-csv"]["review_notes"] == "Retired before source reload"


def test_pdf_table_headers_and_notes_are_skipped(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Coatings - Terr 763 - Eff 5.6.25.pdf"
    pdf_path.write_text(
        "\n".join(
            [
                "GAF Roof Coatings",
                "Price UOM Unit Info Details Effective Date End Date",
                "General Notes for all Coatings Products:",
                "-All orders must be placed via coatings@gaf.com",
                "Silicone Roofing Products",
                "GAF Unisil 5 Gal",
                "$185.00",
                "Pail",
            ]
        ),
        encoding="utf-8",
    )

    rows, skipped, stats = pl.load_input_rows_with_stats(pdf_path)
    products = [row["product_name"] for row in rows]

    assert products == ["GAF Unisil 5 Gal"]
    assert skipped == 0
    assert stats["notes_skipped"] >= 3


def test_pdf_section_header_carried_into_category(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Coatings - Terr 763 - Eff 5.6.25.pdf"
    pdf_path.write_text("Primers\nGAF QuickPrime 5 Gal\n$200.00\nPail\n", encoding="utf-8")

    rows, _skipped = pl.load_input_rows(pdf_path)

    assert rows[0]["category"] == "Primers"


def test_pdf_55_gal_row_becomes_drum_with_price_per_gallon(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Coatings - Terr 763 - Eff 5.6.25.pdf"
    pdf_path.write_text("Silicone Roofing Products\nGAF Silicone 55 Gal White\n$5,500.00\nDrum\n", encoding="utf-8")

    rows, _skipped = pl.load_input_rows(pdf_path)
    row = rows[0]

    assert row["unit_of_measure"] == "drum"
    assert row["package_size"] == "55 gal"
    assert row["price_per_gallon"] == 100.0


def test_pdf_250g_liquid_row_becomes_250_gal(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Coatings - Terr 763 - Eff 5.6.25.pdf"
    pdf_path.write_text("Silicone Roofing Products\nGAF Silicone 250G Tote\n$12,500.00\nTote\n", encoding="utf-8")

    rows, _skipped = pl.load_input_rows(pdf_path)
    row = rows[0]

    assert row["package_size"] == "250 gal"
    assert row["price_per_gallon"] == 50.0


def test_pdf_bag_roll_case_rows_do_not_get_price_per_gallon(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Coatings - Terr 763 - Eff 5.6.25.pdf"
    pdf_path.write_text("Granules\nGAF Granules 2,200 lb Super Sack\n$880.00\nBag\n", encoding="utf-8")

    rows, _skipped = pl.load_input_rows(pdf_path)
    row = rows[0]

    assert row["unit_of_measure"] == "super sack"
    assert row["package_size"] == "2200 lb"
    assert row["price_per_gallon"] is None


def test_duplicate_pdf_rows_are_skipped(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Coatings - Terr 763 - Eff 5.6.25.pdf"
    pdf_path.write_text(
        "Primers\nGAF QuickPrime 5 Gal\n$200.00\nPail\nGAF QuickPrime 5 Gal\n$200.00\nPail\n",
        encoding="utf-8",
    )

    rows, _skipped, stats = pl.load_input_rows_with_stats(pdf_path)

    assert len(rows) == 1
    assert stats["duplicates_skipped"] == 1


def test_pdf_does_not_dedupe_different_package_sizes(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Coatings - Terr 763 - Eff 5.6.25.pdf"
    pdf_path.write_text(
        "\n".join(
            [
                "Primers",
                "GAF Bleed Block Asphalt Primer - 54G",
                "$2,700.00",
                "Drum",
                "GAF Bleed Block Asphalt Primer - 5G",
                "$300.00",
                "Pail",
                "Premium Brush Grade Acrylic Flashing 2 Gal",
                "$120.00",
                "Pail",
                "Premium Brush Grade Acrylic Flashing 5 Gal",
                "$250.00",
                "Pail",
            ]
        ),
        encoding="utf-8",
    )

    rows, _skipped, stats = pl.load_input_rows_with_stats(pdf_path)
    by_name = {row["product_name"]: row for row in rows}

    assert len(rows) == 4
    assert stats["duplicates_skipped"] == 0
    assert by_name["GAF Bleed Block Asphalt Primer - 54G"]["package_size"] == "54 gal"
    assert by_name["GAF Bleed Block Asphalt Primer - 54G"]["unit_of_measure"] == "drum"
    assert by_name["GAF Bleed Block Asphalt Primer - 54G"]["price_per_gallon"] == 50.0
    assert by_name["GAF Bleed Block Asphalt Primer - 5G"]["package_size"] == "5 gal"
    assert by_name["GAF Bleed Block Asphalt Primer - 5G"]["unit_of_measure"] == "pail"
    assert by_name["Premium Brush Grade Acrylic Flashing 2 Gal"]["package_size"] == "2 gal"
    assert by_name["Premium Brush Grade Acrylic Flashing 5 Gal"]["package_size"] == "5 gal"
    assert all(row["status"] == "active" for row in rows)
    raw = json.loads(by_name["GAF Bleed Block Asphalt Primer - 5G"]["raw_row_json"])
    assert raw["extracted_row"]["product_family"] == "GAF Bleed Block Asphalt Primer"
    assert raw["extracted_row"]["details"]


def test_pdf_dedupes_same_family_package_unit_price(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Coatings - Terr 763 - Eff 5.6.25.pdf"
    pdf_path.write_text(
        "\n".join(
            [
                "Primers",
                "GAF Bleed Block Asphalt Primer - 5G",
                "$300.00",
                "Pail",
                "GAF Bleed Block Asphalt Primer 5 Gal",
                "$300.00",
                "Pail",
            ]
        ),
        encoding="utf-8",
    )

    rows, _skipped, stats = pl.load_input_rows_with_stats(pdf_path)

    assert len(rows) == 1
    assert stats["duplicates_skipped"] == 1


def test_inline_pdf_package_price_uom_does_not_pollute_package_size() -> None:
    from jobscan.pricing import core

    parsed_54 = core.parse_pdf_pricing_line("GAF Bleed Block Asphalt Primer - 54G 296.00 Drum")
    parsed_5 = core.parse_pdf_pricing_line("GAF Bleed Block Asphalt Primer - 5G 125.00 Pail")

    assert parsed_54 is not None
    assert parsed_54["product_name"] == "GAF Bleed Block Asphalt Primer - 54G"
    assert parsed_54["package_size"] == "54 gal"
    assert parsed_54["unit_of_measure"] == "drum"
    assert parsed_54["price_per_gallon"] == round(296.0 / 54.0, 4)
    assert parsed_5 is not None
    assert parsed_5["product_name"] == "GAF Bleed Block Asphalt Primer - 5G"
    assert parsed_5["package_size"] == "5 gal"
    assert parsed_5["unit_of_measure"] == "pail"
    assert parsed_5["price_per_gallon"] == 25.0


def test_cleanup_pdf_pricing_does_not_touch_csv_rows() -> None:
    engine = FakeEngine()

    changed = pl.cleanup_pdf_pricing_catalog(engine)

    assert changed == 3
    sql = "\n".join(statement for statement, _params in engine.conn.executed)
    assert "LOWER(COALESCE(source_type, '')) = 'pdf'" in sql
    assert "unit_of_measure" in sql
    assert "price_per_gallon" in sql
    assert "package_size" in sql
    assert "status = 'inactive'" in sql


def test_loader_does_not_modify_input_pdf_content(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Coatings - Terr 763 - Eff 5.6.25.pdf"
    pdf_text_fixture(pdf_path)
    before = pdf_path.read_bytes()

    rows, _skipped = pl.load_input_rows(pdf_path)

    assert rows
    assert pdf_path.read_bytes() == before


def test_loader_does_not_modify_input_csv_content(tmp_path: Path) -> None:
    path = tmp_path / "master.csv"
    master_fixture(path)
    before = path.read_text(encoding="utf-8")

    rows, skipped = pl.load_input_rows(path, mark_current=True)

    assert rows
    assert skipped == 0
    assert path.read_text(encoding="utf-8") == before


def test_source_path_is_never_opened_in_write_mode(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "master.csv"
    master_fixture(path)
    real_open = builtins.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if Path(file) == path and any(flag in str(mode) for flag in ("w", "a", "x", "+")):
            raise AssertionError("source pricing file opened in write mode")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)

    rows, _skipped = pl.load_input_rows(path)

    assert rows


def test_export_current_writes_only_to_out_path_and_does_not_need_sources(tmp_path: Path) -> None:
    source = tmp_path / "data" / "master.csv"
    master_fixture(source)
    before = source.read_text(encoding="utf-8")
    out = tmp_path / "output" / "pricing_catalog_current.csv"
    engine = FakeEngine()
    engine.conn.export_rows = [
        {
            "pricing_item_id": "price-1",
            "vendor": "GAF",
            "category": "Coatings",
            "product_name": "S20 Silicone",
            "unit_price": 32.0,
            "is_current": True,
            "needs_review": False,
            "source_file": "master.csv",
        }
    ]

    count = pl.export_current_pricing_catalog(engine, out)

    assert count == 1
    assert out.exists()
    assert "S20 Silicone" in out.read_text(encoding="utf-8")
    assert source.read_text(encoding="utf-8") == before


def test_dashboard_pricing_query_helper_filters_searches_and_limits(monkeypatch) -> None:
    import dashboard.app as app
    from jobscan.db_connections import ReadQueryResult

    captured = {}

    def fake_load_df_uncached(query, params=None):
        captured["query"] = query
        captured["params"] = params
        return ReadQueryResult(ok=True, value=pd.DataFrame([{"product_name": "S20 Silicone"}]))

    monkeypatch.setattr(app, "load_df_uncached", fake_load_df_uncached)
    app.load_pricing_catalog_filtered.clear()

    df = app.load_pricing_catalog_filtered(
        "silicone",
        ("GAF",),
        ("Coatings",),
        ("active",),
        ("master.csv",),
        ("csv",),
        "Current only",
        "Needs review",
        "2026-01-01",
        "2026-12-31",
        250,
    )

    assert len(df) == 1
    assert "product_name ILIKE :search" in captured["query"]
    assert "vendor = ANY(:vendors)" in captured["query"]
    assert "source_file = ANY(:source_files)" in captured["query"]
    assert "source_type = ANY(:source_types)" in captured["query"]
    assert "LIMIT :limit" in captured["query"]
    assert captured["params"]["search"] == "%silicone%"
    assert captured["params"]["vendors"] == ["GAF"]
    assert captured["params"]["source_files"] == ["master.csv"]
    assert captured["params"]["source_types"] == ["csv"]
    assert captured["params"]["limit"] == 250


def test_dashboard_pricing_default_query_excludes_review_rows(monkeypatch) -> None:
    import dashboard.app as app
    from jobscan.db_connections import ReadQueryResult

    captured = {}

    def fake_load_df_uncached(query, params=None):
        captured["query"] = query
        captured["params"] = params
        return ReadQueryResult(ok=True, value=pd.DataFrame([{"product_name": "Clean Item"}]))

    monkeypatch.setattr(app, "load_df_uncached", fake_load_df_uncached)
    app.load_pricing_catalog_filtered.clear()

    app.load_pricing_catalog_filtered(
        "",
        (),
        (),
        ("active",),
        (),
        (),
        "Current only",
        "Reviewed / OK",
        None,
        None,
        2000,
    )

    assert "COALESCE(is_current, false) IS TRUE" in captured["query"]
    assert "COALESCE(needs_review, false) IS FALSE" in captured["query"]
    assert "status = ANY(:statuses)" in captured["query"]


def test_dashboard_full_current_catalog_query_helper(monkeypatch) -> None:
    import dashboard.app as app
    from jobscan.db_connections import ReadQueryResult

    captured = {}

    def fake_load_df_uncached(query, params=None):
        captured["query"] = query
        captured["params"] = params
        return ReadQueryResult(ok=True, value=pd.DataFrame([{"pricing_item_id": "price-1", "product_name": "S20 Silicone", "is_current": True}]))

    monkeypatch.setattr(app, "load_df_uncached", fake_load_df_uncached)
    app.load_current_pricing_catalog_export.clear()

    df = app.load_current_pricing_catalog_export()

    assert len(df) == 1
    assert "FROM pricing_catalog" in captured["query"]
    assert "COALESCE(is_current, false) IS TRUE" in captured["query"]


def test_dashboard_filtered_download_uses_filtered_query_results() -> None:
    import dashboard.app as app

    filtered = pd.DataFrame(
        [
            {"pricing_item_id": "price-filtered", "product_name": "Filtered Item", "unit_price": 12.0},
            {"pricing_item_id": "price-hidden-extra", "product_name": "Hidden Extra", "vendor_item_no": "X"},
        ]
    )

    export = app.pricing_export_dataframe(filtered)

    assert export["pricing_item_id"].tolist() == ["price-filtered", "price-hidden-extra"]
    assert "vendor_item_no" not in export.columns
