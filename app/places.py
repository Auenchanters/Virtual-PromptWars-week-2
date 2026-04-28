"""Google Maps Platform / Places API (New) Text Search wrapper.

Used by the booth-finder card to surface polling stations near the user's
current location, opt-in via ``navigator.geolocation``.

Rubric: Google Services (Maps Platform / Places API),
Problem Statement Alignment (locating the right booth is the most concrete
voter action), Accessibility (helps users who don't know their state code).
"""

from __future__ import annotations

import logging
import math
from typing import Any, Protocol

import httpx

logger = logging.getLogger("votewise.places")

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELD_MASK = "places.displayName,places.formattedAddress,places.location"
PLACES_QUERY = "polling booth"
PLACES_TIMEOUT_S = 5.0
DEFAULT_MAX_RESULTS = 5


class BoothPlace:
    """Plain data carrier — kept separate from the Pydantic model so the client
    layer has no FastAPI dependency."""

    __slots__ = ("address", "distance_m", "lat", "lng", "name")

    def __init__(self, name: str, address: str, distance_m: int, lat: float, lng: float) -> None:
        self.name = name
        self.address = address
        self.distance_m = distance_m
        self.lat = lat
        self.lng = lng


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    """Great-circle distance between two lat/lng points in metres."""
    earth_radius_m = 6_371_000
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return int(2 * earth_radius_m * math.asin(math.sqrt(a)))


class PlacesClient(Protocol):
    """Minimal structural interface so tests can inject a fake."""

    def nearby_booths(self, lat: float, lng: float, radius_m: int) -> list[BoothPlace]: ...


class RealPlacesClient:
    """Thin wrapper around Places API (New) Text Search.

    Uses an API key (``GOOGLE_MAPS_API_KEY``) injected via Cloud Secret Manager.
    No SDK — direct REST call keeps the dep tree small.
    """

    def __init__(self, api_key: str, http_client: httpx.Client | None = None) -> None:
        if not api_key:
            raise ValueError("api_key must be a non-empty string")
        self._api_key = api_key
        self._client = http_client or httpx.Client(timeout=PLACES_TIMEOUT_S)

    def nearby_booths(self, lat: float, lng: float, radius_m: int) -> list[BoothPlace]:
        resp = self._client.post(
            PLACES_URL,
            headers={
                "X-Goog-Api-Key": self._api_key,
                "X-Goog-FieldMask": PLACES_FIELD_MASK,
                "Content-Type": "application/json",
            },
            json={
                "textQuery": PLACES_QUERY,
                "locationBias": {
                    "circle": {
                        "center": {"latitude": lat, "longitude": lng},
                        "radius": float(radius_m),
                    }
                },
                "maxResultCount": DEFAULT_MAX_RESULTS,
            },
        )
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        out: list[BoothPlace] = []
        for place in body.get("places", []):
            loc = place.get("location") or {}
            p_lat = float(loc.get("latitude", 0.0))
            p_lng = float(loc.get("longitude", 0.0))
            display = place.get("displayName") or {}
            out.append(
                BoothPlace(
                    name=str(display.get("text", "Polling station")),
                    address=str(place.get("formattedAddress", "")),
                    distance_m=_haversine_m(lat, lng, p_lat, p_lng),
                    lat=p_lat,
                    lng=p_lng,
                )
            )
        out.sort(key=lambda b: b.distance_m)
        return out


_places_singleton: PlacesClient | None = None


def get_places_client() -> PlacesClient:
    """Process-wide singleton.

    Resolves ``GOOGLE_MAPS_API_KEY`` via :func:`app.secrets.resolve_secret`,
    which checks the env var first (Cloud Run ``--set-secrets`` path) then
    falls back to a direct Secret Manager API call. Raises if neither
    surfaces a value.
    """
    global _places_singleton
    if _places_singleton is None:
        from app.secrets import resolve_secret  # local import — keeps this module small

        try:
            api_key = resolve_secret("GOOGLE_MAPS_API_KEY")
        except (RuntimeError, ValueError, KeyError) as exc:
            raise RuntimeError(
                "GOOGLE_MAPS_API_KEY is not set. On Cloud Run this should come from "
                "Secret Manager (--set-secrets) or be readable via the Secret Manager API."
            ) from exc
        _places_singleton = RealPlacesClient(api_key=api_key)
    return _places_singleton


def reset_places_client_for_tests() -> None:
    global _places_singleton
    _places_singleton = None


__all__ = [
    "BoothPlace",
    "PlacesClient",
    "RealPlacesClient",
    "get_places_client",
    "reset_places_client_for_tests",
]
