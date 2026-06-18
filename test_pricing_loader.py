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
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class FakeConnection:
    def __init__(self):
        self.pricing_ids: set[str] = set()
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
    assert row["package_size"] == "5 Gal"
    assert row["effective_date"] == "2025-05-06"
    raw = json.loads(row["raw_row_json"])
    assert raw["source_details"]["page_number"] == 1
    assert raw["source_details"]["source_line"] == "GAF Unisil 5 Gal - Standard Colors"


def test_ambiguous_pdf_line_becomes_needs_review(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Spray Tec Pricing Information 2025.pdf"
    pdf_path.write_text("Spray Tec Pricing\nAmbiguous coating note\n", encoding="utf-8")

    rows, _skipped = pl.load_input_rows(pdf_path)

    assert rows
    assert rows[0]["source_type"] == "pdf"
    assert rows[0]["source_page"] == 1
    assert rows[0]["needs_review"] is True
    assert rows[0]["unit_price"] is None


def test_pdf_load_is_idempotent_and_reports_pdf_counts(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Coatings - Terr 763 - Eff 5.6.25.pdf"
    pdf_text_fixture(pdf_path)
    engine = FakeEngine()

    first = pl.load_pricing(engine, [pdf_path], mark_current=True)
    second = pl.load_pricing(engine, [pdf_path], mark_current=True)

    assert first.pdf_files_discovered == 1
    assert first.pdf_files_parsed == 1
    assert first.pdf_pages_read >= 1
    assert first.pdf_rows_extracted >= 2
    assert first.pdf_rows_loaded == first.rows_inserted + first.rows_updated
    assert first.pdf_rows_needing_review >= 1
    assert second.rows_inserted == 0
    assert second.rows_updated == first.rows_inserted


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
        "Current only",
        "Needs review",
        "2026-01-01",
        "2026-12-31",
        250,
    )

    assert len(df) == 1
    assert "product_name ILIKE :search" in captured["query"]
    assert "vendor = ANY(:vendors)" in captured["query"]
    assert "LIMIT :limit" in captured["query"]
    assert captured["params"]["search"] == "%silicone%"
    assert captured["params"]["vendors"] == ["GAF"]
    assert captured["params"]["limit"] == 250


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
