from __future__ import annotations

import pytest

from jobscan.estimator.planning_snapshot import (
    PlanningSnapshotError,
    create_planning_snapshot,
    verify_planning_snapshot,
)


def roofing_scope(*, area_sqft: float = 5_000) -> dict:
    return {
        "template_type": "roofing",
        "declared_total_area_sqft": area_sqft,
        "area_scopes": [
            {
                "scope_id": "recover",
                "scope_role": "exclusive_area",
                "area_sqft": area_sqft,
                "action": "Prepare and recoat existing roof",
            }
        ],
    }


def snapshot_token(*, now: int = 1_000) -> str:
    return create_planning_snapshot(
        scope=roofing_scope(),
        site_address="830 South 1st Street, Louisville, KY 40203",
        labor_plan_guidance=[
            {
                "category": "labor_prep",
                "activity": "Roof preparation",
                "recommended_days": 1.25,
                "recommended_crew_size": 5,
                "recommended_total_hours": 50,
                "unbounded_evidence": "must not be copied into the token",
            }
        ],
        logistics_guidance=[
            {
                "category": "truck_expense",
                "include": True,
                "recommended_trip_count": 4,
                "round_trip_miles": 62,
                "unbounded_evidence": "must not be copied into the token",
            }
        ],
        signing_key="test-secret",
        ttl_seconds=900,
        now=now,
    )


def test_planning_snapshot_round_trip_returns_only_bounded_guidance() -> None:
    verified = verify_planning_snapshot(
        snapshot_token(),
        scope=roofing_scope(),
        site_address="830 South 1st Street, Louisville, KY 40203",
        signing_key="test-secret",
        now=1_500,
    )

    assert verified["labor_plan_guidance"] == [
        {
            "activity": "Roof preparation",
            "category": "labor_prep",
            "recommended_crew_size": 5,
            "recommended_days": 1.25,
            "recommended_total_hours": 50,
        }
    ]
    assert verified["logistics_guidance"] == [
        {
            "category": "truck_expense",
            "include": True,
            "recommended_trip_count": 4,
            "round_trip_miles": 62,
        }
    ]


@pytest.mark.parametrize(
    ("token_transform", "scope", "address", "now", "message"),
    [
        (lambda token: token + "altered", roofing_scope(), "830 South 1st Street, Louisville, KY 40203", 1_500, "signature"),
        (lambda token: token, roofing_scope(area_sqft=4_999), "830 South 1st Street, Louisville, KY 40203", 1_500, "scope"),
        (lambda token: token, roofing_scope(), "Different job site", 1_500, "scope"),
        (lambda token: token, roofing_scope(), "830 South 1st Street, Louisville, KY 40203", 1_901, "expired"),
    ],
)
def test_planning_snapshot_rejects_untrusted_or_stale_input(
    token_transform,
    scope: dict,
    address: str,
    now: int,
    message: str,
) -> None:
    with pytest.raises(PlanningSnapshotError, match=message):
        verify_planning_snapshot(
            token_transform(snapshot_token()),
            scope=scope,
            site_address=address,
            signing_key="test-secret",
            now=now,
        )
