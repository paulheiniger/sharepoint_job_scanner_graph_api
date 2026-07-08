from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine

from jobscan.vsimple_projects import accepted_matches, align_vsimple_to_jobs, condense_vsimple_row, write_outputs_to_database


def test_condense_vsimple_row_maps_project_fields() -> None:
    row = {
        "id": "1704060",
        "record_id": "13710855781",
        "Record Type": "Estimate Roofing",
        "Status Name": "Proposal Submitted",
        "Name": "ACRE Bens Bargain Roof Restoration",
        "job_name": "Bens Bargain",
        "deal_type": "Coating System over Existing Roof",
        "project_type": "Roofing",
        "street_address": "1710 E 10th",
        "city_state_zip": "Jeffersonville, IN",
        "deal_owner": "Anthony Palmer",
        "lead_source": "Referral",
        "bid_amount": "$125,000",
        "est_square_feet": "45,000",
        "Created Date - Year": 2026,
        "Created Date - Month": "July",
        "Created Date - Day": 8,
        "scope_of_work": "Coating roof seams and penetrations.",
    }

    condensed = condense_vsimple_row(row)

    assert condensed["vsimple_id"] == "1704060"
    assert condensed["record_type"] == "Estimate Roofing"
    assert condensed["sales_stage"] == "Proposal Submitted"
    assert condensed["pipeline_status"] == "Proposed"
    assert condensed["division"] == "Roofing"
    assert condensed["project_category"] == "Roofing Restoration"
    assert condensed["bid_amount"] == 125000
    assert condensed["estimated_sqft"] == 45000
    assert condensed["created_date"] == "2026-07-08"
    assert "Coating roof seams" in condensed["scope_summary"]


def test_align_vsimple_to_jobs_scores_reasonable_match() -> None:
    vsimple = pd.DataFrame(
        [
            {
                "vsimple_id": "V1",
                "vsimple_record_id": "R1",
                "name": "ACRE Bens Bargain Roof Restoration",
                "job_name": "Bens Bargain",
                "customer": "ACRE",
                "status_name": "Proposal Submitted",
                "pipeline_status": "Proposed",
                "division": "Roofing",
                "bid_amount": 125000,
                "estimated_sqft": 45000,
                "created_date": "2026-07-08",
                "site_address": "1710 E 10th",
            }
        ]
    )
    jobs = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "customer": "ACRE",
                "job_name": "Bens Bargain",
                "division": "Roofing",
                "pipeline_status": "Proposed",
                "estimated_value": 124500,
                "estimated_sqft": 45200,
                "source_year": 2026,
                "site_address": "1710 East 10th Street",
                "folder_path": "ACRE Bens Bargain Roof Restoration",
            },
            {
                "job_id": "J2",
                "customer": "Other",
                "job_name": "Unrelated",
                "division": "Insulation",
                "pipeline_status": "Completed",
                "estimated_value": 5000,
            },
        ]
    )

    matches = align_vsimple_to_jobs(vsimple, jobs)

    assert matches.iloc[0]["job_id"] == "J1"
    assert matches.iloc[0]["match_status"] in {"matched", "review"}
    assert matches.iloc[0]["match_score"] > 70


def test_accepted_matches_keeps_matched_and_review_only() -> None:
    matches = pd.DataFrame(
        [
            {"match_status": "matched", "vsimple_id": "V1", "job_id": "J1"},
            {"match_status": "review", "vsimple_id": "V2", "job_id": "J2"},
            {"match_status": "weak", "vsimple_id": "V3", "job_id": "J3"},
        ]
    )

    accepted = accepted_matches(matches)

    assert accepted["vsimple_id"].tolist() == ["V1", "V2"]


def test_write_outputs_to_database_writes_accepted_match_table(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'vsimple.db'}")
    condensed = pd.DataFrame([{"vsimple_id": "V1", "name": "Example"}])
    matches = pd.DataFrame(
        [
            {"match_status": "review", "vsimple_id": "V1", "job_id": "J1"},
            {"match_status": "weak", "vsimple_id": "V2", "job_id": "J2"},
        ]
    )

    counts = write_outputs_to_database(condensed, matches, engine)

    accepted = pd.read_sql_query("SELECT * FROM vsimple_sharepoint_job_matches_accepted", engine)
    assert counts["vsimple_projects"] == 1
    assert counts["vsimple_sharepoint_job_matches"] == 2
    assert counts["vsimple_sharepoint_job_matches_accepted"] == 1
    assert accepted.iloc[0]["match_status"] == "review"
