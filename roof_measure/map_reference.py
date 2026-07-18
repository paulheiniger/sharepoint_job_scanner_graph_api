from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from urllib.parse import quote

import requests
from typing import Protocol


@dataclass
class MapReference:
    ok: bool
    latitude: float | None = None
    longitude: float | None = None
    provider: str = "none"
    attribution: str = ""
    warning: str = ""


@dataclass
class MapboxStaticImage:
    ok: bool
    image_bytes: bytes | None = None
    file_name: str = "mapbox-satellite.png"
    latitude: float | None = None
    longitude: float | None = None
    zoom: float | None = None
    pixels_per_foot: float | None = None
    provider: str = "mapbox"
    attribution: str = "Imagery from Mapbox satellite tiles. Verify measurements before quoting."
    warning: str = ""


@dataclass
class BuildingFootprint:
    footprint_id: str
    rings: list[list[tuple[float, float]]]
    label: str
    provider: str = "mapbox"
    attribution: str = "Building footprint data from Mapbox Streets. Verify against imagery before quoting."


@dataclass
class BuildingFootprintLookup:
    ok: bool
    footprints: list[BuildingFootprint]
    provider: str = "mapbox"
    attribution: str = "Building footprint data from Mapbox Streets. Verify against imagery before quoting."
    warning: str = ""


class MapReferenceProvider(Protocol):
    def geocode(self, address: str) -> MapReference:
        ...


class NoMapProvider:
    def geocode(self, address: str) -> MapReference:
        return MapReference(ok=False, warning="No map provider configured.")


class MapboxReferenceProvider:
    def __init__(self, access_token: str):
        self.access_token = access_token.strip()

    def geocode(self, address: str) -> MapReference:
        if not self.access_token:
            return MapReference(ok=False, provider="mapbox", warning="Mapbox token is not configured.")
        address_text = (address or "").strip()
        if not address_text:
            return MapReference(ok=False, provider="mapbox", warning="Address is required for Mapbox lookup.")
        try:
            response = requests.get(
                f"https://api.mapbox.com/geocoding/v5/mapbox.places/{quote(address_text)}.json",
                params={"access_token": self.access_token, "limit": 1},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return MapReference(ok=False, provider="mapbox", warning=f"Mapbox geocoding failed: {type(exc).__name__}: {exc}")
        features = payload.get("features") if isinstance(payload, dict) else None
        if not features:
            return MapReference(ok=False, provider="mapbox", warning="Mapbox did not return a geocoding match.")
        center = features[0].get("center") if isinstance(features[0], dict) else None
        if not isinstance(center, list) or len(center) < 2:
            return MapReference(ok=False, provider="mapbox", warning="Mapbox geocoding response did not include coordinates.")
        try:
            longitude = float(center[0])
            latitude = float(center[1])
        except (TypeError, ValueError):
            return MapReference(ok=False, provider="mapbox", warning="Mapbox geocoding coordinates were not numeric.")
        return MapReference(
            ok=True,
            latitude=latitude,
            longitude=longitude,
            provider="mapbox",
            attribution="Geocoded by Mapbox.",
        )

    def static_satellite_image(
        self,
        address: str,
        *,
        zoom: float = 19.0,
        width: int = 1280,
        height: int = 1280,
    ) -> MapboxStaticImage:
        reference = self.geocode(address)
        if not reference.ok or reference.latitude is None or reference.longitude is None:
            return MapboxStaticImage(ok=False, warning=reference.warning)
        width = max(128, min(int(width), 1280))
        height = max(128, min(int(height), 1280))
        zoom = max(0.0, min(float(zoom), 22.0))
        lon = reference.longitude
        lat = reference.latitude
        try:
            response = requests.get(
                (
                    "https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/"
                    f"{lon},{lat},{zoom},0,0/{width}x{height}"
                ),
                params={"access_token": self.access_token},
                timeout=12,
            )
            response.raise_for_status()
        except Exception as exc:
            return MapboxStaticImage(ok=False, warning=f"Mapbox static imagery failed: {type(exc).__name__}: {exc}")
        pixels_per_foot = _web_mercator_pixels_per_foot(latitude=lat, zoom=zoom)
        return MapboxStaticImage(
            ok=True,
            image_bytes=response.content,
            latitude=lat,
            longitude=lon,
            zoom=zoom,
            pixels_per_foot=pixels_per_foot,
        )

    def building_footprints(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_meters: int = 250,
        limit: int = 25,
    ) -> BuildingFootprintLookup:
        if not self.access_token:
            return BuildingFootprintLookup(ok=False, footprints=[], warning="Mapbox token is not configured.")
        try:
            response = requests.get(
                f"https://api.mapbox.com/v4/mapbox.mapbox-streets-v8/tilequery/{longitude},{latitude}.json",
                params={
                    "access_token": self.access_token,
                    "layers": "building",
                    "radius": max(1, min(int(radius_meters), 5000)),
                    "limit": max(1, min(int(limit), 50)),
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return BuildingFootprintLookup(
                ok=False,
                footprints=[],
                warning=f"Mapbox building footprint lookup failed: {type(exc).__name__}: {exc}",
            )
        footprints: list[BuildingFootprint] = []
        for index, feature in enumerate(payload.get("features") or [], start=1):
            if not isinstance(feature, dict):
                continue
            rings = _feature_polygon_rings(feature)
            if not rings:
                continue
            properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
            feature_id = str(feature.get("id") or properties.get("id") or f"mapbox-building-{index}")
            label = str(properties.get("name") or properties.get("type") or f"Building {index}")
            footprints.append(BuildingFootprint(footprint_id=feature_id, rings=rings, label=label))
        if not footprints:
            return BuildingFootprintLookup(ok=False, footprints=[], warning="Mapbox returned no building footprints near this address.")
        return BuildingFootprintLookup(ok=True, footprints=footprints)


def footprint_rings_to_image_pixels(
    rings: list[list[tuple[float, float]]],
    *,
    center_latitude: float,
    center_longitude: float,
    zoom: float,
    width: int,
    height: int,
) -> list[list[tuple[float, float]]]:
    world_size = 512 * (2**float(zoom))
    center_x, center_y = _mercator_world_pixels(center_longitude, center_latitude, world_size)
    image_rings: list[list[tuple[float, float]]] = []
    for ring in rings:
        image_ring = []
        for longitude, latitude in ring:
            world_x, world_y = _mercator_world_pixels(longitude, latitude, world_size)
            image_ring.append((width / 2 + world_x - center_x, height / 2 + world_y - center_y))
        if len(image_ring) >= 3:
            image_rings.append(image_ring)
    return image_rings


def openstreetmap_building_footprints(
    *,
    latitude: float,
    longitude: float,
    radius_meters: int = 250,
    limit: int = 50,
) -> BuildingFootprintLookup:
    radius = max(1, min(int(radius_meters), 1000))
    query = (
        "[out:json][timeout:20];"
        f"way(around:{radius},{float(latitude):.7f},{float(longitude):.7f})[building];"
        "out geom;"
    )
    endpoints = [
        endpoint.strip()
        for endpoint in os.getenv(
            "OSM_OVERPASS_URL",
            "https://overpass.kumi.systems/api/interpreter,https://overpass-api.de/api/interpreter",
        ).split(",")
        if endpoint.strip()
    ]
    payload = None
    errors: list[str] = []
    for endpoint in endpoints[:2]:
        try:
            response = requests.post(
                endpoint,
                data={"data": query},
                headers={"User-Agent": "SprayTecRoofMeasure/1.0 (footprint-prior)"},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            break
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    if not isinstance(payload, dict):
        return BuildingFootprintLookup(
            ok=False,
            footprints=[],
            provider="openstreetmap",
            attribution="© OpenStreetMap contributors.",
            warning="OpenStreetMap building footprint lookup failed: " + "; ".join(errors),
        )
    footprints: list[BuildingFootprint] = []
    for index, element in enumerate((payload.get("elements") or [])[: max(1, min(int(limit), 100))], start=1):
        if not isinstance(element, dict):
            continue
        geometry = element.get("geometry")
        if not isinstance(geometry, list):
            continue
        ring: list[tuple[float, float]] = []
        for point in geometry:
            if not isinstance(point, dict):
                continue
            try:
                ring.append((float(point["lon"]), float(point["lat"])))
            except (KeyError, TypeError, ValueError):
                continue
        if len(ring) < 3:
            continue
        tags = element.get("tags") if isinstance(element.get("tags"), dict) else {}
        label = str(tags.get("name") or tags.get("building") or f"Building {index}")
        footprints.append(
            BuildingFootprint(
                footprint_id=f"osm-{element.get('id') or index}",
                rings=[ring],
                label=f"OSM {label} {index}",
                provider="openstreetmap",
                attribution="© OpenStreetMap contributors.",
            )
        )
    if not footprints:
        return BuildingFootprintLookup(
            ok=False,
            footprints=[],
            provider="openstreetmap",
            attribution="© OpenStreetMap contributors.",
            warning="OpenStreetMap returned no building footprints near this address.",
        )
    return BuildingFootprintLookup(
        ok=True,
        footprints=footprints,
        provider="openstreetmap",
        attribution="© OpenStreetMap contributors.",
    )


def postgres_building_footprints(
    *,
    latitude: float,
    longitude: float,
    radius_meters: int = 250,
    limit: int = 50,
    database_url: str = "",
) -> BuildingFootprintLookup:
    """Read locally imported footprint candidates using their indexed bounding boxes."""
    try:
        import psycopg2
        from jobscan.env import load_project_env

        load_project_env()
        configured_url = (
            database_url
            or os.getenv("DATABASE_URL")
            or os.getenv("NEON_DATABASE_URL")
            or os.getenv("NEON_PSQL_URL")
            or ""
        ).strip().replace("postgresql+psycopg2://", "postgresql://", 1)
        if not configured_url:
            return BuildingFootprintLookup(
                ok=False,
                footprints=[],
                provider="postgres",
                attribution="Locally imported building footprints.",
                warning="Local building footprint database is not configured.",
            )
        radius = max(1, min(int(radius_meters), 5000))
        latitude_delta = radius / 111_320.0
        longitude_delta = radius / max(1.0, 111_320.0 * math.cos(math.radians(float(latitude))))
        with psycopg2.connect(configured_url, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT source, source_feature_id, geometry_geojson, source_properties
                    FROM building_footprints
                    WHERE min_longitude <= %s
                      AND max_longitude >= %s
                      AND min_latitude <= %s
                      AND max_latitude >= %s
                    ORDER BY
                        POWER((min_longitude + max_longitude) / 2 - %s, 2)
                        + POWER((min_latitude + max_latitude) / 2 - %s, 2)
                    LIMIT %s
                    """,
                    (
                        float(longitude) + longitude_delta,
                        float(longitude) - longitude_delta,
                        float(latitude) + latitude_delta,
                        float(latitude) - latitude_delta,
                        float(longitude),
                        float(latitude),
                        max(1, min(int(limit), 100)),
                    ),
                )
                rows = cursor.fetchall()
    except Exception as exc:
        return BuildingFootprintLookup(
            ok=False,
            footprints=[],
            provider="postgres",
            attribution="Locally imported building footprints.",
            warning=f"Local building footprint lookup failed: {type(exc).__name__}: {exc}",
        )

    footprints: list[BuildingFootprint] = []
    for source, feature_id, geometry, properties in rows:
        if isinstance(geometry, str):
            try:
                geometry = json.loads(geometry)
            except json.JSONDecodeError:
                continue
        if not isinstance(geometry, dict):
            continue
        rings = _feature_polygon_rings({"geometry": geometry})
        if not rings:
            continue
        properties = properties if isinstance(properties, dict) else {}
        label = str(properties.get("name") or f"{source} building {feature_id}")
        footprints.append(
            BuildingFootprint(
                footprint_id=f"postgres-{source}-{feature_id}",
                rings=rings,
                label=label,
                provider="postgres",
                attribution="Locally imported building footprints. Verify against imagery before quoting.",
            )
        )
    if not footprints:
        return BuildingFootprintLookup(
            ok=False,
            footprints=[],
            provider="postgres",
            attribution="Locally imported building footprints.",
            warning="No locally imported building footprints cover this location.",
        )
    return BuildingFootprintLookup(
        ok=True,
        footprints=footprints,
        provider="postgres",
        attribution="Locally imported building footprints. Verify against imagery before quoting.",
    )


def geojson_building_footprints(raw: bytes | str) -> BuildingFootprintLookup:
    try:
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return BuildingFootprintLookup(ok=False, footprints=[], provider="uploaded_geojson", warning=f"Could not read footprint GeoJSON: {exc}")
    features = payload.get("features") if isinstance(payload, dict) and payload.get("type") == "FeatureCollection" else [payload]
    footprints: list[BuildingFootprint] = []
    for index, feature in enumerate(features or [], start=1):
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry") if feature.get("type") == "Feature" else feature
        if not isinstance(geometry, dict):
            continue
        rings = _feature_polygon_rings({"geometry": geometry})
        if not rings:
            continue
        properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        label = str(properties.get("name") or properties.get("building") or f"Uploaded footprint {index}")
        footprints.append(
            BuildingFootprint(
                footprint_id=f"uploaded-{index}",
                rings=rings,
                label=label,
                provider="uploaded_geojson",
                attribution="Uploaded building footprint. Verify its alignment to imagery before quoting.",
            )
        )
    if not footprints:
        return BuildingFootprintLookup(ok=False, footprints=[], provider="uploaded_geojson", warning="GeoJSON did not contain a Polygon or MultiPolygon footprint.")
    return BuildingFootprintLookup(
        ok=True,
        footprints=footprints,
        provider="uploaded_geojson",
        attribution="Uploaded building footprint. Verify its alignment to imagery before quoting.",
    )


def _feature_polygon_rings(feature: dict) -> list[list[tuple[float, float]]]:
    geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        polygon_sets = [coordinates]
    elif geometry_type == "MultiPolygon":
        polygon_sets = coordinates
    else:
        polygon_sets = None
    if not isinstance(polygon_sets, list):
        return []
    rings: list[list[tuple[float, float]]] = []
    for polygon in polygon_sets:
        if not isinstance(polygon, list):
            continue
        for raw_ring in polygon:
            if not isinstance(raw_ring, list):
                continue
            ring: list[tuple[float, float]] = []
            for coordinate in raw_ring:
                if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
                    continue
                try:
                    ring.append((float(coordinate[0]), float(coordinate[1])))
                except (TypeError, ValueError):
                    continue
            if len(ring) >= 3:
                rings.append(ring)
    return rings


def _mercator_world_pixels(longitude: float, latitude: float, world_size: float) -> tuple[float, float]:
    clipped_latitude = max(-85.05112878, min(85.05112878, float(latitude)))
    x = (float(longitude) + 180.0) / 360.0 * world_size
    latitude_radians = math.radians(clipped_latitude)
    y = (1 - math.asinh(math.tan(latitude_radians)) / math.pi) / 2 * world_size
    return x, y


def _web_mercator_pixels_per_foot(*, latitude: float, zoom: float) -> float:
    meters_per_pixel = math.cos(math.radians(latitude)) * 40075016.686 / (512 * (2**zoom))
    feet_per_pixel = meters_per_pixel * 3.280839895
    if feet_per_pixel <= 0:
        return 0.0
    return 1.0 / feet_per_pixel
