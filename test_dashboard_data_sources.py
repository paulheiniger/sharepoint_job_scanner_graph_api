from __future__ import annotations

import inspect

import dashboard.app as app
from dashboard.data_sources import (
    DASHBOARD_CORE_PAGES,
    DASHBOARD_LEGACY_PAGES,
    PAGE_SOURCE_REFERENCES,
    all_dashboard_pages,
    audit_notes_for_page,
    references_for_page,
)


def test_every_navigable_dashboard_page_has_source_references() -> None:
    pages = all_dashboard_pages()

    assert len(pages) == len(set(pages))
    assert set(pages) == set(DASHBOARD_CORE_PAGES + DASHBOARD_LEGACY_PAGES)
    assert set(PAGE_SOURCE_REFERENCES) == set(pages)
    assert all(references_for_page(page) for page in pages)


def test_source_references_have_audit_ready_fields() -> None:
    for page in all_dashboard_pages():
        for reference in references_for_page(page):
            assert set(reference) == {"source", "source_type", "used_for", "lineage_or_rule"}
            assert all(str(value).strip() for value in reference.values())


def test_high_risk_rollups_have_reconciliation_notes() -> None:
    for page in [
        "Sales Dashboard",
        "Operations Dashboard",
        "Job Board",
        "Jobs Needing Action",
        "Closeout / Billing Risk",
        "Estimate Analytics",
        "Estimate Quality Issues",
    ]:
        assert audit_notes_for_page(page)


def test_page_dispatch_always_renders_source_footer() -> None:
    source = inspect.getsource(app.render_dashboard_page)

    assert "render_dashboard_source_references(page)" in source
    assert app.DASHBOARD_CORE_PAGES == DASHBOARD_CORE_PAGES
    assert app.DASHBOARD_LEGACY_PAGES == DASHBOARD_LEGACY_PAGES


def test_sales_and_operations_sources_disclose_fallback_paths() -> None:
    sales_text = " ".join(str(value) for row in references_for_page("Sales Dashboard") for value in row.values()).lower()
    operations_text = " ".join(
        str(value) for row in references_for_page("Operations Dashboard") for value in row.values()
    ).lower()

    assert "job_board_static_snapshot" in sales_text
    assert "vsimple" in sales_text
    assert "operations_dashboard_ops_snapshot" in operations_text
    assert "otherwise" in operations_text
