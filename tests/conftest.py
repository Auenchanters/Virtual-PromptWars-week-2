"""Shared pytest fixtures.

Keeps tests offline: the real Gemini client is replaced by a fake via FastAPI's
dependency override.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from app.chat import ChatMessage, GeminiClient
from app.main import app, get_gemini_client, rate_limiter


class FakeGeminiClient:
    """Records the most recent history it was called with and returns a canned reply."""

    def __init__(self, reply: str = "Use Form 6 on voters.eci.gov.in to register.") -> None:
        self.reply = reply
        self.calls: list[list[ChatMessage]] = []

    def generate(self, history: list[ChatMessage]) -> str:
        self.calls.append(list(history))
        return self.reply


@pytest.fixture
def fake_client() -> FakeGeminiClient:
    return FakeGeminiClient()


@pytest.fixture
def client(fake_client: FakeGeminiClient) -> Iterator[TestClient]:
    def _override() -> GeminiClient:
        return fake_client

    app.dependency_overrides[get_gemini_client] = _override
    rate_limiter.reset()
    try:
        with TestClient(app) as tc:
            yield tc
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def failing_client_factory() -> Callable[[Exception], Callable[[], GeminiClient]]:
    """Return a dependency override that raises the given exception on generate()."""

    class _Failing:
        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def generate(self, history: list[ChatMessage]) -> str:
            raise self._exc

    def _factory(exc: Exception) -> Callable[[], GeminiClient]:
        return lambda: _Failing(exc)

    return _factory
