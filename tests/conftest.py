"""Shared pytest fixtures.

Keeps the test suite fully offline:
- Gemini → FakeGeminiClient (generate + stream)
- Cloud Translation → FakeTranslator
- Cloud Text-to-Speech → FakeSpeaker
- BigQuery → FakeAnalytics
- Cloud DLP → FakeRedactor
- Maps Platform Places API → FakePlaces

Every external dependency is swapped via FastAPI's ``app.dependency_overrides``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.chat import ChatChunk, ChatMessage, ChatResult, Citation, GeminiClient
from app.main import (
    _get_analytics,
    _get_places,
    _get_redactor,
    _get_speaker,
    _get_translator,
    app,
    get_gemini_client,
    rate_limiter,
    translate_limiter,
    tts_limiter,
)
from app.places import BoothPlace

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeGeminiClient:
    """Records each call's history. Returns a canned reply (and streams it in chunks)."""

    def __init__(
        self,
        reply: str = "Use Form 6 on voters.eci.gov.in to register.",
        citations: tuple[Citation, ...] = (),
    ) -> None:
        self.reply = reply
        self.citations = citations
        self.calls: list[list[ChatMessage]] = []
        self.stream_calls: list[list[ChatMessage]] = []
        self.last_use_grounding: bool | None = None

    def generate(self, history: list[ChatMessage], use_grounding: bool = True) -> ChatResult:
        self.calls.append(list(history))
        self.last_use_grounding = use_grounding
        return ChatResult(text=self.reply, citations=self.citations)

    def stream(self, history: list[ChatMessage], use_grounding: bool = True) -> Iterator[ChatChunk]:
        self.stream_calls.append(list(history))
        self.last_use_grounding = use_grounding
        words = self.reply.split(" ")
        for i, word in enumerate(words):
            piece = word if i == 0 else " " + word
            yield ChatChunk(text=piece)
        yield ChatChunk(text="", citations=self.citations, is_final=True)


class FakeTranslator:
    """Deterministic translator: prefixes text with ``[<target>]`` and logs every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def translate(self, text: str, target: str, source: str | None = None) -> str:
        self.calls.append((text, target, source))
        if target == source or not text.strip():
            return text
        return f"[{target}] {text}"


class FakeSpeaker:
    """Returns a fixed MP3-like byte blob so tests don't hit Cloud TTS."""

    FAKE_AUDIO = b"ID3FAKEAUDIOBYTES"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, text: str, lang: str) -> bytes:
        self.calls.append((text, lang))
        return self.FAKE_AUDIO


class FakeAnalytics:
    """Captures every chat-turn log so tests can assert on them."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.raise_on_log: Exception | None = None

    def log_chat_turn(
        self,
        language: str,
        topic: str,
        latency_ms: int,
        used_grounding: bool,
        citation_count: int,
    ) -> None:
        if self.raise_on_log is not None:
            raise self.raise_on_log
        self.rows.append(
            {
                "language": language,
                "topic": topic,
                "latency_ms": latency_ms,
                "used_grounding": used_grounding,
                "citation_count": citation_count,
            }
        )


class FakeRedactor:
    """Regex stand-in for Cloud DLP — replaces phone/email/aadhaar with [REDACTED:<TYPE>]."""

    PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("EMAIL_ADDRESS", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
        ("INDIA_AADHAAR_NUMBER", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
        ("PHONE_NUMBER", re.compile(r"\b(?:\+?91[-\s]?)?[6-9]\d{9}\b")),
    )

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.raise_on_redact: Exception | None = None

    def redact(self, text: str) -> str:
        self.calls.append(text)
        if self.raise_on_redact is not None:
            raise self.raise_on_redact
        out = text
        for label, pat in self.PATTERNS:
            out = pat.sub(f"[REDACTED:{label}]", out)
        return out


class FakePlaces:
    """Returns a canned list of booths so tests don't call the real Places API."""

    DEFAULT = (
        BoothPlace("Govt High School Booth", "MG Road, Bengaluru", 320, 12.9716, 77.5946),
        BoothPlace("Polling Station 42", "1st Cross, Indiranagar", 870, 12.9784, 77.6408),
    )

    def __init__(self, results: tuple[BoothPlace, ...] = DEFAULT) -> None:
        self.results = results
        self.calls: list[tuple[float, float, int]] = []
        self.raise_on_search: Exception | None = None

    def nearby_booths(self, lat: float, lng: float, radius_m: int) -> list[BoothPlace]:
        self.calls.append((lat, lng, radius_m))
        if self.raise_on_search is not None:
            raise self.raise_on_search
        return list(self.results)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _reset_limiters() -> None:
    rate_limiter.reset()
    translate_limiter.reset()
    tts_limiter.reset()


@pytest.fixture(autouse=True)
def _isolate_singletons_and_overrides() -> Iterator[None]:
    """Test-order isolation guard.

    Wipes every module-level singleton (translator, speaker, analytics,
    redactor, places client) and every FastAPI dependency override before
    AND after each test, so a test that injects state via `monkeypatch.setenv`
    + `get_*()` cannot leak into the next test. Limiters are reset too.
    """
    from app.analytics import reset_analytics_for_tests
    from app.dlp import reset_redactor_for_tests
    from app.places import reset_places_client_for_tests
    from app.speech import reset_speaker_for_tests
    from app.translation import reset_translator_for_tests

    def _reset_all() -> None:
        reset_translator_for_tests()
        reset_speaker_for_tests()
        reset_analytics_for_tests()
        reset_redactor_for_tests()
        reset_places_client_for_tests()
        _reset_limiters()
        app.dependency_overrides.clear()

    _reset_all()
    try:
        yield
    finally:
        _reset_all()


@pytest.fixture
def fake_client() -> FakeGeminiClient:
    return FakeGeminiClient()


@pytest.fixture
def fake_translator() -> FakeTranslator:
    return FakeTranslator()


@pytest.fixture
def fake_speaker() -> FakeSpeaker:
    return FakeSpeaker()


@pytest.fixture
def fake_analytics() -> FakeAnalytics:
    return FakeAnalytics()


@pytest.fixture
def fake_redactor() -> FakeRedactor:
    return FakeRedactor()


@pytest.fixture
def fake_places() -> FakePlaces:
    return FakePlaces()


@pytest.fixture
def client(
    fake_client: FakeGeminiClient,
    fake_translator: FakeTranslator,
    fake_speaker: FakeSpeaker,
    fake_analytics: FakeAnalytics,
    fake_redactor: FakeRedactor,
    fake_places: FakePlaces,
) -> Iterator[TestClient]:
    def _gemini() -> GeminiClient:
        return fake_client

    def _translator() -> Any:
        return fake_translator

    def _speaker() -> Any:
        return fake_speaker

    def _analytics() -> Any:
        return fake_analytics

    def _redactor() -> Any:
        return fake_redactor

    def _places() -> Any:
        return fake_places

    app.dependency_overrides[get_gemini_client] = _gemini
    app.dependency_overrides[_get_translator] = _translator
    app.dependency_overrides[_get_speaker] = _speaker
    app.dependency_overrides[_get_analytics] = _analytics
    app.dependency_overrides[_get_redactor] = _redactor
    app.dependency_overrides[_get_places] = _places
    _reset_limiters()
    try:
        with TestClient(app) as tc:
            yield tc
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def failing_client_factory() -> Callable[[Exception], Callable[[], GeminiClient]]:
    """Return a dependency-override factory that raises the given exception on generate()."""

    class _Failing:
        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def generate(self, history: list[ChatMessage], use_grounding: bool = True) -> ChatResult:
            raise self._exc

        def stream(
            self, history: list[ChatMessage], use_grounding: bool = True
        ) -> Iterator[ChatChunk]:
            raise self._exc

    def _factory(exc: Exception) -> Callable[[], GeminiClient]:
        return lambda: _Failing(exc)

    return _factory
