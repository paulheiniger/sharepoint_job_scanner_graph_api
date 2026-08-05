from __future__ import annotations

from datetime import date

from openpyxl import Workbook

from jobscan.warranty_master import (
    load_customers,
    load_projects,
    parse_contact_ids,
    parse_duration,
)


def write_export(path, headers, rows) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Export"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def test_customer_and_project_exports_join_by_contact_info(tmp_path) -> None:
    customers_path = tmp_path / "customers.xlsx"
    write_export(
        customers_path,
        ["id", "record_id", "Name", "first_name", "last_name", "email", "phone_number", "URL"],
        [["C-100", "R-100", "Pat Person : Example", "Pat", "Person", "pat@example.com", "502-555-0100", "https://example.invalid/C-100"]],
    )
    projects_path = tmp_path / "projects.xlsx"
    write_export(
        projects_path,
        [
            "Name",
            "URL",
            "contact_info",
            "warranty",
            "completion_date - Year",
            "completion_date - Month",
            "completion_date - Day",
        ],
        [["Example Roof", "https://app.vsimple.com/spray-tec/pre-orders/200", "[100, C-100]", "15 Year", 2026, "June", 1]],
    )

    customers = load_customers(customers_path)
    projects, relationships, sources = load_projects(projects_path, recent=False)

    assert customers[0]["email"] == "pat@example.com"
    assert projects[0]["vsimple_id"] == "200"
    assert projects[0]["duration_years"] == 15
    assert projects[0]["start_date"] == date(2026, 6, 1)
    assert projects[0]["expiration_date"] == date(2041, 6, 1)
    assert relationships == [
        {
            "vsimple_id": "200",
            "customer_id": "100",
            "relationship_source": "contact_info",
            "source_file": "projects.xlsx",
            "source_sheet": "Export",
            "source_row": 2,
        }
    ]
    assert sources[0].source_system == "vsimple_project_warranty_export"


def test_recent_warranty_list_accepts_numeric_duration_and_completion_date(tmp_path) -> None:
    path = tmp_path / "recent.xlsx"
    write_export(
        path,
        ["Vsimple Id", "Vsimple URL", "Name", "Warranty", "Date of Completion", "Contact Name", "Contact Email"],
        [[2049732, "https://example.invalid/2049732", "Quantum Ink", 10, "06/20/2026", "Josh Hoskins", "josh@example.com"]],
    )

    projects, relationships, sources = load_projects(path, recent=True)

    assert not relationships
    assert projects[0]["duration_years"] == 10
    assert projects[0]["start_date"] == date(2026, 6, 20)
    assert projects[0]["expiration_date"] == date(2036, 6, 20)
    assert projects[0]["reported_contact_email"] == "josh@example.com"
    assert sources[0].source_system == "recent_completed_warranty_list"


def test_contact_and_duration_parsers_are_bounded() -> None:
    assert parse_contact_ids({"contact_info": "[129165, 166553]"}) == ["129165", "166553"]
    assert parse_duration(20) == 20
    assert parse_duration("2 Yr. Workmanship") == 2
    assert parse_duration("TBD") is None
    assert parse_duration(52) is None

