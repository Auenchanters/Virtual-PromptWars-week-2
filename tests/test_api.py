"""HTTP-level tests for the core read + chat endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import _get_translator, app, get_gemini_client, rate_limiter
from tests.conftest import FakeTranslator


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_index_served(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "VoteWise India" in r.text
    assert r.headers["content-security-policy"].startswith("default-src 'self'")


def test_api_info_shape_and_etag(client: TestClient) -> None:
    r = client.get("/api/info")
    assert r.status_code == 200
    data = r.json()
    assert data["country"] == "India"
    assert "general_election_timeline" in data
    etag = r.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"')
    # Re-request with If-None-Match should yield 304.
    r2 = client.get("/api/info", headers={"If-None-Match": etag})
    assert r2.status_code == 304


def test_api_states(client: TestClient) -> None:
    r = client.get("/api/states")
    assert r.status_code == 200
    states = r.json()["states_and_uts"]
    codes = [s["code"] for s in states]
    assert "MH" in codes and "DL" in codes and len(states) >= 30


def test_api_languages_lists_codes(client: TestClient) -> None:
    r = client.get("/api/languages")
    assert r.status_code == 200
    codes = [entry["code"] for entry in r.json()["languages"]]
    for expected in ("en", "hi", "ta", "bn"):
        assert expected in codes


def test_chat_happy_path(client: TestClient, fake_client) -> None:
    r = client.post("/api/chat", json={"history": [], "message": "How do I register?"})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"].startswith("Use Form 6")
    assert "eci.gov.in" in body["disclaimer"]
    assert body["language"] == "en"
    assert body["reply_en"] is None
    assert len(fake_client.calls) == 1
    last_call = fake_client.calls[0]
    assert last_call[-1].role == "user"
    assert last_call[-1].text == "How do I register?"


def test_chat_rejects_blank_message(client: TestClient) -> None:
    r = client.post("/api/chat", json={"history": [], "message": "   "})
    assert r.status_code == 422


def test_chat_rejects_oversized_message(client: TestClient) -> None:
    r = client.post("/api/chat", json={"history": [], "message": "x" * 2000})
    assert r.status_code == 422


def test_chat_rejects_oversized_history(client: TestClient) -> None:
    history = [{"role": "user", "text": "hi"}] * 25
    r = client.post("/api/chat", json={"history": history, "message": "hello"})
    assert r.status_code == 422


def test_chat_rejects_unknown_target_language(client: TestClient) -> None:
    r = client.post(
        "/api/chat",
        json={"history": [], "message": "hi", "target_language": "xx"},
    )
    assert r.status_code == 422


def test_chat_rate_limit_sets_retry_after(client: TestClient) -> None:
    max_req = rate_limiter.max_requests
    for _ in range(max_req):
        r = client.post("/api/chat", json={"history": [], "message": "hello"})
        assert r.status_code == 200
    r = client.post("/api/chat", json={"history": [], "message": "hello"})
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1


def test_chat_returns_503_when_gemini_fails(failing_client_factory) -> None:
    app.dependency_overrides[get_gemini_client] = failing_client_factory(RuntimeError("boom"))
    app.dependency_overrides[_get_translator] = FakeTranslator
    rate_limiter.reset()
    try:
        with TestClient(app) as tc:
            r = tc.post("/api/chat", json={"history": [], "message": "hello"})
            assert r.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_chat_returns_citations_when_grounding(client: TestClient, fake_client) -> None:
    from app.chat import Citation

    fake_client.citations = (Citation(title="ECI press note", uri="https://eci.gov.in/x"),)
    r = client.post("/api/chat", json={"history": [], "message": "latest news"})
    assert r.status_code == 200
    citations = r.json()["citations"]
    assert citations[0]["uri"] == "https://eci.gov.in/x"
