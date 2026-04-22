"""SSE streaming endpoint tests."""

from __future__ import annotations

import contextlib
import json

from fastapi.testclient import TestClient

from app.chat import Citation


def _parse_sse(raw: str) -> list[dict]:
    events: list[dict] = []
    for frame in raw.split("\n\n"):
        for line in frame.splitlines():
            if line.startswith("data:"):
                with contextlib.suppress(json.JSONDecodeError):
                    events.append(json.loads(line[5:].strip()))
    return events


def test_stream_emits_meta_chunks_and_done(client: TestClient) -> None:
    r = client.post(
        "/api/chat/stream",
        json={"history": [], "message": "how do I register?"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    assert events[0] == {"type": "meta", "language": "en"}
    assert any(e["type"] == "chunk" for e in events)
    done = next(e for e in events if e["type"] == "done")
    assert done["language"] == "en"
    assert "disclaimer" in done


def test_stream_emits_citations_in_done(client: TestClient, fake_client) -> None:
    fake_client.citations = (
        Citation(title="ECI", uri="https://eci.gov.in/a"),
        Citation(title="PIB", uri="https://pib.gov.in/b"),
    )
    r = client.post(
        "/api/chat/stream",
        json={"history": [], "message": "latest schedule"},
    )
    assert r.status_code == 200
    done = next(e for e in _parse_sse(r.text) if e["type"] == "done")
    uris = [c["uri"] for c in done["citations"]]
    assert "https://eci.gov.in/a" in uris


def test_stream_translates_after_full_reply(client: TestClient, fake_translator) -> None:
    r = client.post(
        "/api/chat/stream",
        json={"history": [], "message": "hi", "target_language": "hi"},
    )
    assert r.status_code == 200
    events = _parse_sse(r.text)
    translated = [e for e in events if e["type"] == "translated"]
    assert translated, "expected a 'translated' event for non-English streams"
    assert translated[0]["lang"] == "hi"
    # One translation call for input, one for the final concatenated reply.
    assert any(call[1] == "en" for call in fake_translator.calls)
    assert any(call[1] == "hi" for call in fake_translator.calls)
