"""End-to-end security-hardening tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.security import DEFAULT_MAX_BODY_BYTES


def test_required_security_headers(client: TestClient) -> None:
    r = client.get("/health")
    h = {k.lower(): v for k, v in r.headers.items()}
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert h["cross-origin-opener-policy"] == "same-origin"
    assert h["cross-origin-resource-policy"] == "same-origin"
    assert "permissions-policy" in h
    csp = h["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "'unsafe-inline'" not in csp
    assert "worker-src 'self'" in csp


def test_hsts_present_under_https_forwarded(client: TestClient) -> None:
    r = client.get("/health", headers={"x-forwarded-proto": "https"})
    assert "strict-transport-security" in {k.lower() for k in r.headers}


def test_hsts_absent_under_plain_http(client: TestClient) -> None:
    r = client.get("/health")
    assert "strict-transport-security" not in {k.lower() for k in r.headers}


def test_request_id_header_present(client: TestClient) -> None:
    r = client.get("/health")
    assert r.headers.get("x-request-id")


def test_body_size_limit_returns_413(client: TestClient) -> None:
    payload = "x" * (DEFAULT_MAX_BODY_BYTES + 1024)
    r = client.post(
        "/api/chat",
        content=payload,
        headers={"content-type": "application/json", "content-length": str(len(payload))},
    )
    assert r.status_code == 413


def test_invalid_content_length_returns_400(client: TestClient) -> None:
    r = client.post(
        "/api/chat",
        content=b"{}",
        headers={"content-type": "application/json", "content-length": "not-a-number"},
    )
    assert r.status_code == 400
