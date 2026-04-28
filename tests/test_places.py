"""Tests for the Google Maps Platform / Places API booth-locator endpoint."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import _get_places, app, translate_limiter
from app.places import (
    BoothPlace,
    RealPlacesClient,
    _haversine_m,
    get_places_client,
    reset_places_client_for_tests,
)


def test_booth_search_happy_path(client: TestClient, fake_places) -> None:
    r = client.post(
        "/api/places/booth",
        json={"lat": 12.9716, "lng": 77.5946, "radius_m": 1500},
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["results"], list)
    assert len(body["results"]) == 2
    first = body["results"][0]
    assert first["name"] == "Govt High School Booth"
    assert first["distance_m"] == 320
    # Places client received the exact request parameters.
    assert fake_places.calls == [(12.9716, 77.5946, 1500)]


def test_booth_search_returns_503_on_places_failure(client: TestClient, fake_places) -> None:
    fake_places.raise_on_search = RuntimeError("places api down")
    r = client.post(
        "/api/places/booth",
        json={"lat": 12.97, "lng": 77.59, "radius_m": 2000},
    )
    assert r.status_code == 503
    assert "Booth lookup is temporarily unavailable" in r.json()["detail"]


def test_booth_search_validates_lat_lng(client: TestClient) -> None:
    # lat out of range
    r = client.post("/api/places/booth", json={"lat": 100, "lng": 77.5})
    assert r.status_code == 422
    # lng out of range
    r = client.post("/api/places/booth", json={"lat": 12.0, "lng": 200.0})
    assert r.status_code == 422


def test_booth_search_clamps_radius(client: TestClient) -> None:
    # radius below the floor
    r = client.post("/api/places/booth", json={"lat": 12.0, "lng": 77.0, "radius_m": 50})
    assert r.status_code == 422
    # radius above the ceiling
    r = client.post(
        "/api/places/booth",
        json={"lat": 12.0, "lng": 77.0, "radius_m": 50_000},
    )
    assert r.status_code == 422


def test_booth_search_rate_limited() -> None:
    """The Places endpoint shares the translation rate-limit bucket; verify 429."""
    from tests.conftest import FakePlaces

    fake = FakePlaces()
    app.dependency_overrides[_get_places] = lambda: fake
    translate_limiter.reset()
    try:
        with TestClient(app) as tc:
            for _ in range(translate_limiter.max_requests):
                r = tc.post(
                    "/api/places/booth",
                    json={"lat": 12.0, "lng": 77.0, "radius_m": 2000},
                )
                assert r.status_code == 200
            r = tc.post(
                "/api/places/booth",
                json={"lat": 12.0, "lng": 77.0, "radius_m": 2000},
            )
            assert r.status_code == 429
            assert int(r.headers["retry-after"]) >= 1
    finally:
        app.dependency_overrides.clear()


def test_haversine_distance_within_tolerance() -> None:
    # Distance from MG Road, Bengaluru to a point ~1 km north (lat +0.009 ≈ 1 km).
    d = _haversine_m(12.9716, 77.5946, 12.9716 + 0.009, 77.5946)
    assert 950 <= d <= 1100


def test_real_places_client_rejects_empty_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        RealPlacesClient(api_key="")


def test_get_places_client_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_places_client_for_tests()
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    try:
        with pytest.raises(RuntimeError, match="GOOGLE_MAPS_API_KEY"):
            get_places_client()
    finally:
        reset_places_client_for_tests()


def test_get_places_client_returns_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_places_client_for_tests()
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "fake-key")
    try:
        a = get_places_client()
        b = get_places_client()
        assert a is b
    finally:
        reset_places_client_for_tests()


def test_real_places_client_parses_search_response_and_sorts() -> None:
    """Direct unit test on RealPlacesClient.nearby_booths via a fake httpx client."""

    class _FakeResponse:
        status_code = 200

        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._payload

    class _FakeHttpxClient:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload
            self.last_post: dict[str, Any] | None = None

        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            self.last_post = {"url": url, **kwargs}
            return _FakeResponse(self._payload)

    payload: dict[str, Any] = {
        "places": [
            {
                "displayName": {"text": "Polling Station 99"},
                "formattedAddress": "Indiranagar, Bengaluru",
                "location": {"latitude": 12.9784, "longitude": 77.6408},
            },
            {
                "displayName": {"text": "Govt School Booth"},
                "formattedAddress": "MG Road, Bengaluru",
                "location": {"latitude": 12.9716, "longitude": 77.5946},
            },
        ]
    }
    fake_http = _FakeHttpxClient(payload)
    rpc = RealPlacesClient(api_key="fake-key", http_client=fake_http)  # type: ignore[arg-type]

    results = rpc.nearby_booths(12.9716, 77.5946, 2000)
    assert len(results) == 2
    # Closest result first.
    assert isinstance(results[0], BoothPlace)
    assert results[0].name == "Govt School Booth"
    assert results[0].distance_m == 0
    # Second is ~6 km away (Indiranagar from MG Road).
    assert 5000 <= results[1].distance_m <= 8000
    # Headers carry the API key + field mask.
    assert fake_http.last_post is not None
    headers = fake_http.last_post["headers"]
    assert headers["X-Goog-Api-Key"] == "fake-key"
    assert headers["X-Goog-FieldMask"].startswith("places.")
