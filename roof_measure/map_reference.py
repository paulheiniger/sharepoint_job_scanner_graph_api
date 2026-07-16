from __future__ import annotations

from dataclasses import dataclass
import math
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
                timeout=30,
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


def _web_mercator_pixels_per_foot(*, latitude: float, zoom: float) -> float:
    meters_per_pixel = math.cos(math.radians(latitude)) * 40075016.686 / (512 * (2**zoom))
    feet_per_pixel = meters_per_pixel * 3.280839895
    if feet_per_pixel <= 0:
        return 0.0
    return 1.0 / feet_per_pixel
