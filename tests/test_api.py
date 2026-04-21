from app.main import app, get_gemini_client, rate_limiter


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Chunav Sathi" in r.text
    assert r.headers["content-security-policy"].startswith("default-src 'self'")


def test_api_info_shape(client):
    r = client.get("/api/info")
    assert r.status_code == 200
    data = r.json()
    assert data["country"] == "India"
    assert "general_election_timeline" in data


def test_chat_happy_path(client, fake_client):
    r = client.post("/api/chat", json={"history": [], "message": "How do I register?"})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"].startswith("Use Form 6")
    assert "eci.gov.in" in body["disclaimer"]
    # Request should have reached the fake exactly once, with the user turn appended.
    assert len(fake_client.calls) == 1
    last_call = fake_client.calls[0]
    assert last_call[-1].role == "user"
    assert last_call[-1].text == "How do I register?"


def test_chat_rejects_blank_message(client):
    r = client.post("/api/chat", json={"history": [], "message": "   "})
    assert r.status_code == 422


def test_chat_rejects_oversized_message(client):
    r = client.post("/api/chat", json={"history": [], "message": "x" * 2000})
    assert r.status_code == 422


def test_chat_rejects_oversized_history(client):
    history = [{"role": "user", "text": "hi"}] * 25
    r = client.post("/api/chat", json={"history": history, "message": "hello"})
    assert r.status_code == 422


def test_chat_rate_limit(client, fake_client):
    # After rate_limiter.max_requests successful calls, the next one should 429.
    max_req = rate_limiter._max  # type: ignore[attr-defined]
    for _ in range(max_req):
        r = client.post("/api/chat", json={"history": [], "message": "hello"})
        assert r.status_code == 200
    r = client.post("/api/chat", json={"history": [], "message": "hello"})
    assert r.status_code == 429


def test_chat_returns_503_when_gemini_fails(failing_client_factory):
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_gemini_client] = failing_client_factory(
        RuntimeError("boom")
    )
    rate_limiter._hits.clear()  # type: ignore[attr-defined]
    try:
        with TestClient(app) as tc:
            r = tc.post("/api/chat", json={"history": [], "message": "hello"})
            assert r.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_security_headers_on_api(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
