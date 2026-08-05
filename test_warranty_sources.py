from __future__ import annotations

from datetime import date

from jobscan.warranty_sources import _match_candidate, load_gaco_warranty_list, parse_date, parse_years, years_between


def test_parse_date_repairs_common_pdf_spacing_artifacts() -> None:
    assert parse_date("Warranty begins 10/2 1/2039") == date(2039, 10, 21)
    assert parse_date("ends 11/272034") == date(2034, 11, 27)


def test_duration_parsing_is_bounded() -> None:
    assert parse_years("15 Years") == 15
    assert parse_years("52 Years") is None
    assert years_between(date(2024, 9, 16), date(2044, 9, 16)) == 20


def test_gaco_list_preserves_warranty_number_and_expiration(tmp_path) -> None:
    source = tmp_path / "gaco.csv"
    source.write_text(
        "Warranty Number,Building Name,Building Owner,Street,City,State,Zip,Expiration Date,Sq. Foot,Substrate,Installing Contractor,\n"
        "7810,Example School,County Schools,1 Main St,Shelbyville,KY,40065,9/13/2029,133600,METAL,\"Spray-Tec, Inc.\",Material and Labor\n",
        encoding="utf-8",
    )

    records = load_gaco_warranty_list(source, source_url="https://example.invalid/gaco.csv")

    assert len(records) == 1
    assert records[0].provider == "Gaco"
    assert records[0].expiration_date == date(2029, 9, 13)
    assert records[0].coverage_summary == "Material and Labor"
    assert records[0].raw["Warranty Number"] == "7810"


def test_match_candidate_is_bounded_and_includes_authoritative_job() -> None:
    candidate = _match_candidate(
        {
            "vsimple_id": "V-1",
            "name": "Example School",
            "customer": "County Schools",
            "site_address": "1 Main St",
            "sharepoint_url": "https://should-not-be-returned.invalid",
        },
        0.93456,
        {"job_id": "JOB-1"},
    )

    assert candidate == {
        "vsimple_id": "V-1",
        "job_id": "JOB-1",
        "name": "Example School",
        "customer": "County Schools",
        "site_address": "1 Main St",
        "score": 0.9346,
    }
