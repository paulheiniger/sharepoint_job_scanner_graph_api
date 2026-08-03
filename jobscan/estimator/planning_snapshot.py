from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from .decision_proposals import canonicalize_structured_roofing_scope


class PlanningSnapshotError(ValueError):
    pass


def create_planning_snapshot(
    *,
    scope: dict[str, Any],
    site_address: str,
    labor_plan_guidance: list[dict[str, Any]],
    logistics_guidance: list[dict[str, Any]],
    signing_key: str,
    ttl_seconds: int = 900,
    now: int | None = None,
) -> str:
    if not signing_key:
        return ""
    issued_at = int(time.time()) if now is None else int(now)
    payload = {
        "v": 1,
        "iat": issued_at,
        "exp": issued_at + min(max(int(ttl_seconds), 60), 3_600),
        "scope": planning_scope_fingerprint(scope, site_address=site_address),
        "labor": [_labor_projection(row) for row in labor_plan_guidance],
        "logistics": [_logistics_projection(row) for row in logistics_guidance],
    }
    encoded_payload = _encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = _encode(
        hmac.new(
            signing_key.encode("utf-8"),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
    )
    return f"{encoded_payload}.{signature}"


def verify_planning_snapshot(
    token: str,
    *,
    scope: dict[str, Any],
    site_address: str,
    signing_key: str,
    now: int | None = None,
) -> dict[str, Any]:
    if not token or not signing_key:
        raise PlanningSnapshotError("Planning snapshot is unavailable.")
    encoded_payload, separator, supplied_signature = token.partition(".")
    if not separator:
        raise PlanningSnapshotError("Planning snapshot is malformed.")
    expected_signature = _encode(
        hmac.new(
            signing_key.encode("utf-8"),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
    )
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise PlanningSnapshotError("Planning snapshot signature is invalid.")
    try:
        payload = json.loads(_decode(encoded_payload).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanningSnapshotError("Planning snapshot payload is invalid.") from exc
    current_time = int(time.time()) if now is None else int(now)
    if int(payload.get("exp") or 0) < current_time:
        raise PlanningSnapshotError("Planning snapshot has expired.")
    expected_scope = planning_scope_fingerprint(scope, site_address=site_address)
    if not hmac.compare_digest(
        str(payload.get("scope") or ""),
        expected_scope,
    ):
        raise PlanningSnapshotError("Planning snapshot scope does not match.")
    return {
        "labor_plan_guidance": list(payload.get("labor") or []),
        "logistics_guidance": list(payload.get("logistics") or []),
    }


def planning_scope_fingerprint(
    scope: dict[str, Any],
    *,
    site_address: str,
) -> str:
    canonical = canonicalize_structured_roofing_scope(scope or {})
    geometry = [
        {
            "id": str(row.get("scope_id") or "").strip(),
            "parent": str(row.get("parent_scope_id") or "").strip(),
            "role": str(row.get("scope_role") or row.get("role") or "").strip().lower(),
            "area": _number(row.get("area_sqft")),
            "deck": _number(row.get("decking_replacement_sqft")),
        }
        for row in canonical.get("area_scopes") or []
        if isinstance(row, dict)
    ]
    normalized = {
        "template": str(canonical.get("template_type") or "roofing").lower(),
        "site": " ".join(str(site_address or "").lower().split()),
        "total": _number(canonical.get("canonical_area_total_sqft")),
        "foam": _number(canonical.get("foam_basis_sqft")),
        "coating": _number(canonical.get("coating_basis_sqft")),
        "board": _number(canonical.get("board_basis_sqft")),
        "deck": _number(canonical.get("decking_replacement_sqft")),
        "geometry": sorted(
            geometry,
            key=lambda row: (row["id"], row["parent"], row["role"], row["area"]),
        ),
    }
    raw = json.dumps(normalized, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _labor_projection(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "category",
        "activity",
        "recommended_total_hours",
        "recommended_crew_size",
        "recommended_days",
        "current_people_daily_rate",
        "estimated_labor_cost_candidate",
        "calibration_status",
        "method",
    )
    return {key: row.get(key) for key in keys if row.get(key) is not None}


def _logistics_projection(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "category",
        "include",
        "recommended_trip_count",
        "recommended_hours_per_trip",
        "recommended_crew_size",
        "estimated_total_person_hours",
        "estimated_labor_cost_candidate",
        "round_trip_miles",
        "method",
    )
    return {key: row.get(key) for key in keys if row.get(key) is not None}


def _number(value: Any) -> float:
    try:
        return round(float(value or 0), 3)
    except (TypeError, ValueError):
        return 0.0


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
