"""Shared pytest fixtures.

Keeps the test suite fully offline:
- Gemini → FakeGeminiClient (generate + stream)
- Cloud Translation → FakeTranslator
- Cloud Text-to-Speech → FakeSpeaker

Every external dependency is swapped via FastAPI's ``app.dependency_overrides``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.chat import ChatChunk, ChatMessage, ChatResult, Citation, GeminiClient
from app.main import (
    _get_speaker,
    _get_translator,
    app,
    get_gemini_client,
    rate_limiter,
    translate_limiter,
    tts_limiter,
)

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
        # Yield the reply word-by-word so tests exercise multi-chunk streaming.
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


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _reset_limiters() -> None:
    rate_limiter.reset()
    translate_limiter.reset()
    tts_limiter.reset()


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
def client(
    fake_client: FakeGeminiClient,
    fake_translator: FakeTranslator,
    fake_speaker: FakeSpeaker,
) -> Iterator[TestClient]:
    def _gemini() -> GeminiClient:
        return fake_client

    def _translator() -> Any:
        return fake_translator

    def _speaker() -> Any:
        return fake_speaker

    app.dependency_overrides[get_gemini_client] = _gemini
    app.dependency_overrides[_get_translator] = _translator
    app.dependency_overrides[_get_speaker] = _speaker
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
