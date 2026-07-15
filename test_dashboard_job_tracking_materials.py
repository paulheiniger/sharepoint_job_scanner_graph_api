import pandas as pd


def test_job_tracking_material_enrichment_and_split(monkeypatch) -> None:
    import dashboard.app as app

    summary = pd.DataFrame(
        [
            {
                "job_id": "ROOF1",
                "division": "Roofing",
                "project": "Roofing foam and coating",
                "actual_foam_sqft": 250,
                "actual_base_coat_1": 8,
                "estimated_foam_sqft": None,
                "estimated_base_coat_1": None,
                "estimated_base_coat_2": None,
            },
            {
                "job_id": "INS1",
                "division": "Insulation",
                "project": "Insulation foam",
                "actual_foam_sqft": 400,
                "estimated_foam_sqft": None,
            },
        ]
    )
    enrichment = pd.DataFrame(
        [
            {
                "job_id": "ROOF1",
                "estimated_materials_source": "estimate_template_rows",
                "estimate_material_rows_used": 4,
                "estimated_foam_sqft_from_estimate_rows": 1000,
                "estimated_foam_yield_from_estimate_rows": 3000,
                "estimated_base_coat_1_from_estimate_rows": 10,
                "estimated_base_coat_2_from_estimate_rows": 20,
                "estimated_primer_from_estimate_rows": 5,
            },
            {
                "job_id": "INS1",
                "estimated_materials_source": "estimate_template_rows",
                "estimate_material_rows_used": 1,
                "estimated_foam_sqft_from_estimate_rows": 1200,
                "estimated_foam_yield_from_estimate_rows": 3500,
            },
        ]
    )
    monkeypatch.setattr(app, "load_job_tracking_estimated_material_enrichment", lambda job_ids: enrichment)

    enriched = app.enrich_job_tracking_summary_with_estimated_materials(summary)

    roof = enriched[enriched["job_id"] == "ROOF1"].iloc[0]
    assert roof["estimated_foam_sqft"] == 1000
    assert roof["estimated_base_coat_1"] == 10
    assert roof["estimated_base_coat_2"] == 20
    assert roof["estimated_primer"] == 5
    assert roof["foam_sqft_variance"] == -750
    assert roof["base_coat_1_variance"] == -2

    insulation = enriched[enriched["job_id"] == "INS1"].iloc[0]
    assert insulation["estimated_foam_sqft"] == 1200
    assert insulation["estimated_foam_yield"] == 3500

    roofing_rows, insulation_rows = app.split_tracking_material_rows(enriched)

    assert roofing_rows["job_id"].tolist() == ["ROOF1"]
    assert insulation_rows["job_id"].tolist() == ["INS1"]
    assert "estimated_foam_sqft" in roofing_rows.columns
    assert "estimated_foam_sqft" in insulation_rows.columns
