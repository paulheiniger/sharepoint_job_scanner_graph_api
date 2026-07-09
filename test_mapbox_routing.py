from __future__ import annotations

import pytest

from jobscan.estimator.mapbox_routing import (
    address_is_routeable,
    clear_mapbox_route_cache,
    mapbox_route_distance,
)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, *, params: dict, timeout: float) -> FakeResponse:
        self.calls.append((url, params))
        if "geocode" in url:
            q = params["q"]
            lon = -85.0 if "Equity" in q else -84.5
            lat = 38.0 if "Equity" in q else 39.0
            return FakeResponse({"features": [{"geometry": {"coordinates": [lon, lat]}}]})
        return FakeResponse({"routes": [{"distance": 16093.44, "duration": 900}]})


def test_mapbox_route_distance_uses_geocoding_and_directions(monkeypatch) -> None:
    clear_mapbox_route_cache()
    monkeypatch.setenv("MAPBOX_ACCESS_TOKEN", "test-token")
    session = FakeSession()

    route = mapbox_route_distance(
        "1132 Equity Street, Shelbyville, KY",
        "314 E Aberdeen Drive, Trenton, OH",
        session=session,
    )

    assert route is not None
    assert route.one_way_miles == pytest.approx(10.0)
    assert route.round_trip_miles == pytest.approx(20.0)
    assert route.duration_minutes_one_way == pytest.approx(15.0)
    assert len(session.calls) == 3
    assert session.calls[-1][1]["access_token"] == "test-token"


def test_mapbox_route_distance_skips_city_only_or_missing_token(monkeypatch) -> None:
    clear_mapbox_route_cache()
    monkeypatch.delenv("MAPBOX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("MAPBOX_TOKEN", raising=False)
    monkeypatch.delenv("MAPBOX_API_TOKEN", raising=False)

    assert address_is_routeable("314 E Aberdeen Drive, Trenton, OH") is True
    assert address_is_routeable("Cincinnati, OH") is False
    assert mapbox_route_distance("1132 Equity Street, Shelbyville, KY", "314 E Aberdeen Drive, Trenton, OH") is None
