from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class MapReference:
    ok: bool
    latitude: float | None = None
    longitude: float | None = None
    provider: str = "none"
    attribution: str = ""
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
        # The dashboard already has a Mapbox implementation. This provider is
        # intentionally isolated so the roof-measure core does not depend on
        # Mapbox or downloaded map imagery.
        return MapReference(ok=False, provider="mapbox", warning="Mapbox reference lookup is not wired in Milestone 1.")

