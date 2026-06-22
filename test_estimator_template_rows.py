from __future__ import annotations

import json

import pandas as pd
from sqlalchemy import create_engine, text

from jobscan.estimator import template_rows as tr


def content_row(row_number: int, text_content: str, **kwargs):
    row = {
        "document_id": "DOC1",
        "job_id": "JOB1",
        "source_file": "Estimate.xlsx",
        "sheet_name": "Estimate",
        "row_number": row_number,
        "cell_range": f"A{row_number}:J{row_number}",
        "text_content": text_content,
    }
    row.update(kwargs)
    return row


def create_sqlite_schema(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE documents (
                    document_id TEXT PRIMARY KEY,
                    job_id TEXT,
                    file_name TEXT,
                    file_extension TEXT,
                    document_type TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE document_content (
                    content_id TEXT PRIMARY KEY,
                    document_id TEXT,
                    job_id TEXT,
                    sheet_name TEXT,
                    row_number INTEGER,
                    cell_range TEXT,
                    text_content TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE estimate_template_rows (
                    template_row_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    job_id TEXT,
                    source_file TEXT,
                    sheet_name TEXT,
                    row_number INTEGER,
                    cell_range TEXT,
                    template_bucket TEXT,
                    template_section TEXT,
                    line_item_kind TEXT,
                    row_label TEXT,
                    raw_text TEXT,
                    cell_values TEXT,
                    formula_cells TEXT,
                    selected_item_name TEXT,
                    quantity NUMERIC,
                    unit TEXT,
                    unit_price NUMERIC,
                    estimated_units NUMERIC,
                    estimated_cost NUMERIC,
                    days NUMERIC,
                    crew_size NUMERIC,
                    total_hours NUMERIC,
                    daily_rate NUMERIC,
                    trips NUMERIC,
                    round_trip_miles NUMERIC,
                    cost_per_mile NUMERIC,
                    warranty_years NUMERIC,
                    overhead_pct NUMERIC,
                    profit_pct NUMERIC,
                    parsed_confidence NUMERIC,
                    needs_review BOOLEAN DEFAULT FALSE,
                    parser_version TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )


def test_parse_cell_labeled_text_numeric_and_formulas() -> None:
    cell_values, formula_cells, malformed_count = tr.parse_cell_labeled_text(
        "A26: 11 | B26: Gaco Silicone | D26: 1 | E26: 42 | G26: =C26*D26 | bad fragment"
    )

    assert cell_values == {"A26": 11, "B26": "Gaco Silicone", "D26": 1, "E26": 42}
    assert formula_cells == {"G26": "=C26*D26"}
    assert malformed_count == 1


def test_row_26_maps_to_coating_and_extracts_material_fields() -> None:
    parsed = tr.parse_document_content_row(
        content_row(26, "A26: 11 | B26: Gaco Silicone | C26: 150 | E26: 42 | G26: =formula | H26: 6300")
    )

    assert parsed["template_bucket"] == "coating"
    assert parsed["line_item_kind"] == "material"
    assert parsed["selected_item_name"] == "Gaco Silicone"
    assert parsed["quantity"] == 150
    assert parsed["unit_price"] == 42
    assert parsed["estimated_units"] is None
    assert parsed["estimated_cost"] == 6300
    assert parsed["formula_cells"] == {"G26": "=formula"}


def test_row_39_maps_to_primer() -> None:
    parsed = tr.parse_document_content_row(content_row(39, "A39: Primer | B39: Bleed Block | C39: 2 | E39: 125"))

    assert parsed["template_bucket"] == "primer"
    assert parsed["selected_item_name"] == "Bleed Block"


def test_row_106_sales_inspection_extracts_travel_fields() -> None:
    parsed = tr.parse_document_content_row(content_row(106, "A106: Sales/Inspection | B106: 2 | C106: 180 | E106: .75 | H106: 270"))

    assert parsed["template_bucket"] == "sales_inspection_trips"
    assert parsed["line_item_kind"] == "travel"
    assert parsed["trips"] == 2
    assert parsed["round_trip_miles"] == 180
    assert parsed["cost_per_mile"] == 0.75
    assert parsed["estimated_cost"] == 270


def test_row_108_truck_expense_extracts_travel_fields() -> None:
    parsed = tr.parse_document_content_row(content_row(108, "A108: Truck | B108: 3 | C108: 90 | E108: 0.75 | H108: 202.5"))

    assert parsed["template_bucket"] == "truck_expense"
    assert parsed["trips"] == 3
    assert parsed["estimated_cost"] == 202.5


def test_row_116_labor_prep_extracts_labor_fields() -> None:
    parsed = tr.parse_document_content_row(
        content_row(116, "A116: Pwash/Prep | B116: 4 | C116: 5 | D116: 220 | H116: 7607.6 | J116: 1901.9")
    )

    assert parsed["template_bucket"] == "labor_prep"
    assert parsed["line_item_kind"] == "labor"
    assert parsed["days"] == 4
    assert parsed["crew_size"] == 5
    assert parsed["total_hours"] == 220
    assert parsed["estimated_cost"] == 7607.6
    assert parsed["daily_rate"] == 1901.9


def test_row_139_labor_traveling() -> None:
    parsed = tr.parse_document_content_row(content_row(139, "A139: Traveling | C139: 16 | E139: 3 | G139: 72 | H139: 3456"))

    assert parsed["template_bucket"] == "labor_traveling"
    assert parsed["line_item_kind"] == "travel"
    assert parsed["total_hours"] == 16
    assert parsed["crew_size"] == 3
    assert parsed["unit_price"] == 72


def test_row_154_warranty_extracts_years() -> None:
    parsed = tr.parse_document_content_row(content_row(154, "A154: Warranty | C154: 20 | E154: 12000 | H154: 2400"))

    assert parsed["template_bucket"] == "warranty"
    assert parsed["warranty_years"] == 20
    assert parsed["quantity"] == 12000
    assert parsed["estimated_cost"] == 2400


def test_overhead_profit_and_worksheet_price_rows() -> None:
    overhead = tr.parse_document_content_row(content_row(165, "A165: Overhead | F165: 10% | H165: 5000"))
    profit = tr.parse_document_content_row(content_row(167, "A167: Profit | F167: 25 | H167: 12000"))
    price = tr.parse_document_content_row(content_row(169, "A169: Price | H169: 85000"))

    assert overhead["template_bucket"] == "overhead"
    assert overhead["overhead_pct"] == 10
    assert profit["template_bucket"] == "profit"
    assert profit["profit_pct"] == 25
    assert price["template_bucket"] == "worksheet_price"
    assert price["estimated_cost"] == 85000


def test_row_173_misc_materials_adder_uses_column_f_amount() -> None:
    parsed = tr.parse_document_content_row(content_row(173, "A173: Misc. Materials | F173: 3500"))

    assert parsed["template_bucket"] == "misc_materials"
    assert parsed["template_section"] == "estimate_adders"
    assert parsed["line_item_kind"] == "material"
    assert parsed["selected_item_name"] == "Misc. Materials"
    assert parsed["estimated_cost"] == 3500
    assert parsed["needs_review"] is False


def test_row_174_misc_insurance_adder() -> None:
    parsed = tr.parse_document_content_row(content_row(174, "A174: Misc. Insurance | F174: 1600"))

    assert parsed["template_bucket"] == "misc_insurance"
    assert parsed["template_section"] == "estimate_adders"
    assert parsed["line_item_kind"] == "insurance"
    assert parsed["estimated_cost"] == 1600
    assert parsed["needs_review"] is False


def test_row_175_lift_rental_adder() -> None:
    parsed = tr.parse_document_content_row(content_row(175, "A175: Lift Rental | F175: 3500"))

    assert parsed["template_bucket"] == "lift"
    assert parsed["template_section"] == "estimate_adders"
    assert parsed["line_item_kind"] == "equipment"
    assert parsed["estimated_cost"] == 3500


def test_placeholder_adder_without_amount_is_skipped() -> None:
    parsed = tr.parse_document_content_row(content_row(176, "A176: Additional Amount w/o Markup"))

    assert parsed is None


def test_placeholder_adder_with_h_cell_but_no_numeric_amount_is_skipped() -> None:
    parsed = tr.parse_document_content_row(content_row(176, "A176: Additional Amount w/o Markup | H176: =F176"))

    assert parsed is None


def test_placeholder_adder_label_in_other_cell_is_skipped() -> None:
    parsed = tr.parse_document_content_row(content_row(176, "B176: Additional Amount w/o Markup | F176: "))

    assert parsed is None


def test_placeholder_adder_rows_do_not_parse_as_unknown_review_rows() -> None:
    rows = tr.parse_document_content_rows(
        [
            content_row(173, "A173: Additional Amount w/o Markup"),
            content_row(174, "B174: Additional Amount w/o Markup | H174: =F174"),
            content_row(175, "A175: Misc. Materials | F175: 3500"),
        ]
    )

    assert len(rows) == 1
    assert rows[0]["template_bucket"] == "misc_materials"
    assert all(row["template_bucket"] != "unknown" for row in rows)
    assert all(row["needs_review"] is False for row in rows)


def test_custom_adder_with_amount_maps_to_generic_adder() -> None:
    parsed = tr.parse_document_content_row(content_row(177, "A177: Crane coordination | F177: 900"))

    assert parsed["template_bucket"] == "estimate_adder"
    assert parsed["template_section"] == "estimate_adders"
    assert parsed["line_item_kind"] == "other"
    assert parsed["estimated_cost"] == 900
    assert parsed["needs_review"] is False


def test_adder_formula_in_column_f_is_preserved() -> None:
    parsed = tr.parse_document_content_row(content_row(178, "A178: Misc. Equipment | F178: =SUM(F173:F177)"))

    assert parsed["template_bucket"] == "misc_equipment"
    assert parsed["line_item_kind"] == "equipment"
    assert parsed["estimated_cost"] is None
    assert parsed["formula_cells"] == {"F178": "=SUM(F173:F177)"}
    assert parsed["needs_review"] is True


def test_missing_and_malformed_cells_do_not_crash() -> None:
    parsed = tr.parse_document_content_row(content_row(999, "malformed only | A999: Mystery"))

    assert parsed["template_bucket"] == "unknown"
    assert parsed["needs_review"] is True
    assert parsed["cell_values"] == {"A999": "Mystery"}


def test_idempotent_upsert_preserves_document_content() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    create_sqlite_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO documents (document_id, job_id, file_name, file_extension, document_type)
                VALUES ('DOC1', 'JOB1', 'Estimate.xlsx', '.xlsx', 'estimate')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO document_content (
                    content_id, document_id, job_id, sheet_name, row_number, cell_range, text_content
                )
                VALUES (
                    'CONTENT1', 'DOC1', 'JOB1', 'Estimate', 116, 'A116:J116',
                    'A116: Pwash/Prep | B116: 4 | C116: 5 | D116: 220 | H116: 7607.6 | J116: 1901.9'
                )
                """
            )
        )

    first = tr.parse_existing_document_content(engine)
    second = tr.parse_existing_document_content(engine)

    assert first["rows_upserted"] == 1
    assert second["rows_upserted"] == 1
    with engine.connect() as conn:
        content_count = conn.execute(text("SELECT COUNT(*) FROM document_content")).scalar_one()
        template_count = conn.execute(text("SELECT COUNT(*) FROM estimate_template_rows")).scalar_one()
        raw_text = conn.execute(text("SELECT text_content FROM document_content WHERE content_id = 'CONTENT1'")).scalar_one()
    assert content_count == 1
    assert template_count == 1
    assert "Pwash/Prep" in raw_text


def test_parse_existing_is_bounded_visible_and_xlsx_only(capsys) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    create_sqlite_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO documents (document_id, job_id, file_name, file_extension, document_type)
                VALUES
                    ('DOC1', 'JOB1', 'Estimate 1.xlsx', '.xlsx', 'estimate'),
                    ('DOC2', 'JOB2', 'Proposal.pdf', '.pdf', 'proposal'),
                    ('DOC3', 'JOB3', 'Estimate 3.xlsx', '.xlsx', 'estimate')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO document_content (
                    content_id, document_id, job_id, sheet_name, row_number, cell_range, text_content
                )
                VALUES
                    (
                        'CONTENT1', 'DOC1', 'JOB1', 'Estimate', 116, 'A116:J116',
                        'A116: Pwash/Prep | B116: 4 | C116: 5 | D116: 220 | H116: 7607.6'
                    ),
                    (
                        'CONTENT2', 'DOC2', 'JOB2', NULL, NULL, NULL,
                        'PDF paragraph that should not be scanned A116: fake'
                    ),
                    (
                        'CONTENT3', 'DOC3', 'JOB3', 'Estimate', 26, 'A26:H26',
                        'A26: 11 | B26: Silicone | C26: 10 | E26: 40 | H26: 400'
                    )
                """
            )
        )

    summary = tr.parse_existing_document_content(engine, limit_documents=1, progress=True)

    assert summary["documents_considered"] == 1
    assert summary["rows_read"] == 1
    assert summary["rows_upserted"] == 1
    captured = capsys.readouterr().out
    assert "Template row parse: documents considered: 1" in captured
    assert "[1/1] Estimate 1.xlsx" in captured
    with engine.connect() as conn:
        parsed_documents = conn.execute(text("SELECT DISTINCT document_id FROM estimate_template_rows")).scalars().all()
    assert parsed_documents == ["DOC1"]


def test_parse_existing_only_unparsed_skips_current_parser_rows() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    create_sqlite_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO documents (document_id, job_id, file_name, file_extension, document_type)
                VALUES
                    ('DOC1', 'JOB1', 'Estimate 1.xlsx', '.xlsx', 'estimate'),
                    ('DOC2', 'JOB2', 'Estimate 2.xlsx', '.xlsx', 'estimate')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO document_content (
                    content_id, document_id, job_id, sheet_name, row_number, cell_range, text_content
                )
                VALUES
                    (
                        'CONTENT1', 'DOC1', 'JOB1', 'Estimate', 116, 'A116:J116',
                        'A116: Pwash/Prep | B116: 4 | C116: 5 | D116: 220 | H116: 7607.6'
                    ),
                    (
                        'CONTENT2', 'DOC2', 'JOB2', 'Estimate', 26, 'A26:H26',
                        'A26: 11 | B26: Silicone | C26: 10 | E26: 40 | H26: 400'
                    )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO estimate_template_rows (
                    template_row_id, document_id, job_id, sheet_name, row_number,
                    cell_range, template_bucket, line_item_kind, needs_review, parser_version
                )
                VALUES ('existing', 'DOC1', 'JOB1', 'Estimate', 116, 'A116:J116', 'labor_prep', 'labor', false, :parser_version)
                """
            ),
            {"parser_version": tr.PARSER_VERSION},
        )

    summary = tr.parse_existing_document_content(engine, only_unparsed=True)

    assert summary["documents_considered"] == 1
    with engine.connect() as conn:
        parsed_documents = conn.execute(
            text("SELECT DISTINCT document_id FROM estimate_template_rows ORDER BY document_id")
        ).scalars().all()
    assert parsed_documents == ["DOC1", "DOC2"]


def test_unused_placeholder_adder_deletes_stale_template_row() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    create_sqlite_schema(engine)
    stale_id = tr.stable_template_row_id("DOC1", "Estimate", 176, "A176:J176")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO documents (document_id, job_id, file_name, file_extension, document_type)
                VALUES ('DOC1', 'JOB1', 'Estimate.xlsx', '.xlsx', 'estimate')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO document_content (
                    content_id, document_id, job_id, sheet_name, row_number, cell_range, text_content
                )
                VALUES (
                    'CONTENT1', 'DOC1', 'JOB1', 'Estimate', 176, 'A176:J176',
                    'A176: Additional Amount w/o Markup | H176: =F176'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO estimate_template_rows (
                    template_row_id, document_id, job_id, sheet_name, row_number,
                    cell_range, template_bucket, line_item_kind, needs_review
                )
                VALUES (:template_row_id, 'DOC1', 'JOB1', 'Estimate', 176, 'A176:J176', 'unknown', 'unknown', true)
                """
            ),
            {"template_row_id": stale_id},
        )

    summary = tr.parse_existing_document_content(engine)

    assert summary["placeholder_rows_deleted"] == 1
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM estimate_template_rows")).scalar_one()
    assert count == 0


def test_query_helpers_and_summaries() -> None:
    rows = pd.DataFrame(
        [
            tr.parse_document_content_row(content_row(116, "A116: Prep | B116: 2 | C116: 4 | D116: 64 | H116: 2000")),
            tr.parse_document_content_row(content_row(26, "A26: 11 | B26: Silicone | C26: 10 | E26: 40 | H26: 400")),
            tr.parse_document_content_row(content_row(169, "A169: Price | H169: 10000")),
        ]
    )

    summary = tr.bucket_summary(rows)
    labor = tr.labor_task_summary(rows)
    met = tr.material_equipment_travel_summary(rows)
    totals = tr.totals_for_document(rows)

    assert set(summary["template_bucket"]) == {"labor_prep", "coating", "worksheet_price"}
    assert labor.iloc[0]["median_total_hours"] == 64
    assert met.iloc[0]["median_unit_price"] == 40
    assert totals["worksheet_price"] == 10000
