from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import requests


GEOCODING_URL = "https://api.mapbox.com/search/geocode/v6/forward"
DIRECTIONS_URL_TEMPLATE = "https://api.mapbox.com/directions/v5/mapbox/driving/{coordinates}"
METERS_PER_MILE = 1609.344
_ROUTE_CACHE: dict[tuple[str, str], "MapboxRouteDistance"] = {}


@dataclass(frozen=True)
class MapboxRouteDistance:
    origin_address: str
    destination_address: str
    origin_coordinates: tuple[float, float]
    destination_coordinates: tuple[float, float]
    one_way_miles: float
    round_trip_miles: float
    duration_minutes_one_way: float | None = None
    source: str = "mapbox_directions"


class MapboxRoutingError(RuntimeError):
    pass


def mapbox_access_token() -> str:
    return (
        os.getenv("MAPBOX_ACCESS_TOKEN")
        or os.getenv("MAPBOX_TOKEN")
        or os.getenv("MAPBOX_API_TOKEN")
        or ""
    ).strip()


def mapbox_routing_enabled() -> bool:
    value = os.getenv("MAPBOX_ROUTING_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def address_is_routeable(value: Any) -> bool:
    text = _normalize_address(value)
    if not text:
        return False
    has_street_number = bool(re.search(r"\b\d{1,6}\b", text))
    has_street_hint = bool(
        re.search(
            r"\b(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|ct|court|blvd|boulevard|pkwy|parkway|way|hwy|highway|route)\b",
            text,
            re.I,
        )
    )
    return has_street_number and has_street_hint


def mapbox_one_way_miles(
    origin_address: Any,
    destination_address: Any,
    *,
    access_token: str | None = None,
    timeout: float | None = None,
    session: Any = None,
) -> float | None:
    result = mapbox_route_distance(
        origin_address,
        destination_address,
        access_token=access_token,
        timeout=timeout,
        session=session,
    )
    return result.one_way_miles if result else None


def mapbox_route_distance(
    origin_address: Any,
    destination_address: Any,
    *,
    access_token: str | None = None,
    timeout: float | None = None,
    session: Any = None,
) -> MapboxRouteDistance | None:
    origin = _normalize_address(origin_address)
    destination = _normalize_address(destination_address)
    if not origin or not destination or not mapbox_routing_enabled():
        return None
    if not address_is_routeable(origin) or not address_is_routeable(destination):
        return None
    token = (access_token if access_token is not None else mapbox_access_token()).strip()
    if not token:
        return None
    cache_key = (origin.lower(), destination.lower())
    if cache_key in _ROUTE_CACHE:
        return _ROUTE_CACHE[cache_key]
    timeout_seconds = timeout if timeout is not None else _timeout_seconds()
    http = session or requests
    origin_coordinates = _mapbox_geocode(origin, token, timeout_seconds, http)
    destination_coordinates = _mapbox_geocode(destination, token, timeout_seconds, http)
    route = _mapbox_directions(origin_coordinates, destination_coordinates, token, timeout_seconds, http)
    result = MapboxRouteDistance(
        origin_address=origin,
        destination_address=destination,
        origin_coordinates=origin_coordinates,
        destination_coordinates=destination_coordinates,
        one_way_miles=round(route["distance_meters"] / METERS_PER_MILE, 1),
        round_trip_miles=round(route["distance_meters"] / METERS_PER_MILE * 2, 1),
        duration_minutes_one_way=round(route["duration_seconds"] / 60, 1) if route.get("duration_seconds") is not None else None,
    )
    _ROUTE_CACHE[cache_key] = result
    return result


def clear_mapbox_route_cache() -> None:
    _ROUTE_CACHE.clear()


def _normalize_address(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _timeout_seconds() -> float:
    try:
        return float(os.getenv("MAPBOX_TIMEOUT_SECONDS", "8"))
    except (TypeError, ValueError):
        return 8.0


def _mapbox_geocode(address: str, token: str, timeout: float, http: Any) -> tuple[float, float]:
    response = http.get(
        GEOCODING_URL,
        params={"q": address, "limit": 1, "access_token": token},
        timeout=timeout,
    )
    if getattr(response, "status_code", 0) >= 400:
        raise MapboxRoutingError(f"Mapbox geocoding failed with status {response.status_code}.")
    payload = response.json()
    features = payload.get("features") if isinstance(payload, dict) else None
    if not features:
        raise MapboxRoutingError(f"Mapbox geocoding returned no result for address: {address}")
    feature = features[0]
    coordinates = ((feature.get("geometry") or {}).get("coordinates") or [])
    if len(coordinates) >= 2:
        return float(coordinates[0]), float(coordinates[1])
    properties = feature.get("properties") or {}
    nested = properties.get("coordinates") or {}
    lon = nested.get("longitude")
    lat = nested.get("latitude")
    if lon is None or lat is None:
        raise MapboxRoutingError(f"Mapbox geocoding result did not include coordinates for address: {address}")
    return float(lon), float(lat)


def _mapbox_directions(
    origin_coordinates: tuple[float, float],
    destination_coordinates: tuple[float, float],
    token: str,
    timeout: float,
    http: Any,
) -> dict[str, float | None]:
    coordinates = (
        f"{origin_coordinates[0]},{origin_coordinates[1]};"
        f"{destination_coordinates[0]},{destination_coordinates[1]}"
    )
    response = http.get(
        DIRECTIONS_URL_TEMPLATE.format(coordinates=coordinates),
        params={"alternatives": "false", "overview": "false", "access_token": token},
        timeout=timeout,
    )
    if getattr(response, "status_code", 0) >= 400:
        raise MapboxRoutingError(f"Mapbox directions failed with status {response.status_code}.")
    payload = response.json()
    routes = payload.get("routes") if isinstance(payload, dict) else None
    if not routes:
        raise MapboxRoutingError("Mapbox directions returned no route.")
    route = routes[0]
    distance = route.get("distance")
    if distance is None:
        raise MapboxRoutingError("Mapbox directions route did not include distance.")
    return {
        "distance_meters": float(distance),
        "duration_seconds": float(route["duration"]) if route.get("duration") is not None else None,
    }
