"""SSE streaming endpoint tests."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.chat import ChatChunk, ChatMessage, ChatResult, Citation
from app.main import (
    _get_analytics,
    _get_redactor,
    _get_translator,
    app,
    get_gemini_client,
    rate_limiter,
)
from tests.conftest import FakeAnalytics, FakeRedactor, FakeTranslator


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


def test_stream_emits_error_event_when_generator_raises() -> None:
    """The streaming generator's except branch must emit a structured error event."""

    class _StreamMidFailure:
        def generate(self, history: list[ChatMessage], use_grounding: bool = True) -> ChatResult:
            return ChatResult(text="ok")

        def stream(
            self, history: list[ChatMessage], use_grounding: bool = True
        ) -> Iterator[ChatChunk]:
            yield ChatChunk(text="hello")
            raise RuntimeError("mid-stream-boom")

    app.dependency_overrides[get_gemini_client] = lambda: _StreamMidFailure()
    app.dependency_overrides[_get_translator] = FakeTranslator
    app.dependency_overrides[_get_analytics] = FakeAnalytics
    app.dependency_overrides[_get_redactor] = FakeRedactor
    rate_limiter.reset()
    try:
        with TestClient(app) as tc:
            r = tc.post("/api/chat/stream", json={"history": [], "message": "hi"})
            assert r.status_code == 200
            events = _parse_sse(r.text)
            errors = [e for e in events if e["type"] == "error"]
            assert errors, "expected an 'error' event in the SSE stream"
            assert "mid-stream-boom" in errors[0]["detail"]
            # No 'done' event after the error — the generator returns.
            assert not any(e["type"] == "done" for e in events)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("lang", ["hi", "ta", "ur", "bn", "kn"])
def test_stream_emits_translated_event_per_language(client: TestClient, lang: str) -> None:
    """Every supported non-English language receives a 'translated' SSE event."""
    r = client.post(
        "/api/chat/stream",
        json={"history": [], "message": "hello", "target_language": lang},
    )
    assert r.status_code == 200
    events = _parse_sse(r.text)
    translated = [e for e in events if e["type"] == "translated"]
    assert translated, f"expected translated event for {lang}"
    assert translated[0]["lang"] == lang
    assert translated[0]["text"].startswith(f"[{lang}] ")
