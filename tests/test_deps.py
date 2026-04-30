"""Direct unit tests for the helpers in :mod:`app.deps`.

These helpers are exercised by every router via integration tests, but their
own ``test_deps.py`` makes the contract explicit and gives us branch coverage
on the ``client is None`` and 429 paths that integration tests cannot hit.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.chat import ChatMessage, ChatResult
from app.deps import (
    _check_rate,
    _client_ip,
    _run_generate,
    _run_translate,
    _sse,
)
from app.limiter import RateLimiter


def _make_request(headers: list[tuple[bytes, bytes]], client: tuple[str, int] | None) -> Request:
    """Build a minimal Starlette ``Request`` for header / client tests."""
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": client,
    }
    return Request(scope)


# --------------------------------------------------------------------------- #
# _client_ip
# --------------------------------------------------------------------------- #


def test_client_ip_uses_x_forwarded_for_last_hop() -> None:
    req = _make_request(
        headers=[(b"x-forwarded-for", b"1.2.3.4, 9.9.9.9")],
        client=("127.0.0.1", 0),
    )
    assert _client_ip(req) == "9.9.9.9"


def test_client_ip_strips_whitespace_in_forwarded_chain() -> None:
    req = _make_request(
        headers=[(b"x-forwarded-for", b"  10.0.0.1   ,    8.8.8.8   ")],
        client=("127.0.0.1", 0),
    )
    assert _client_ip(req) == "8.8.8.8"


def test_client_ip_falls_back_to_request_client_host() -> None:
    req = _make_request(headers=[], client=("203.0.113.1", 0))
    assert _client_ip(req) == "203.0.113.1"


def test_client_ip_returns_unknown_when_no_client() -> None:
    req = _make_request(headers=[], client=None)
    assert _client_ip(req) == "unknown"


# --------------------------------------------------------------------------- #
# _check_rate
# --------------------------------------------------------------------------- #


def test_check_rate_passes_when_under_limit() -> None:
    limiter = RateLimiter(max_requests=3, window_seconds=60)
    req = _make_request(headers=[], client=("203.0.113.2", 0))
    # Three calls allowed; should not raise.
    for _ in range(3):
        _check_rate(limiter, req)


def test_check_rate_raises_429_with_retry_after() -> None:
    limiter = RateLimiter(max_requests=1, window_seconds=60)
    req = _make_request(headers=[], client=("203.0.113.3", 0))
    _check_rate(limiter, req)  # first call consumes the bucket
    with pytest.raises(HTTPException) as exc:
        _check_rate(limiter, req)
    assert exc.value.status_code == 429
    assert exc.value.headers is not None
    assert "Retry-After" in exc.value.headers
    assert int(exc.value.headers["Retry-After"]) >= 1


# --------------------------------------------------------------------------- #
# _sse
# --------------------------------------------------------------------------- #


def test_sse_encodes_mapping_to_data_frame() -> None:
    out = _sse({"type": "chunk", "text": "hello"})
    assert out.startswith(b"data: ")
    assert out.endswith(b"\n\n")
    payload = json.loads(out[len(b"data: ") : -2])
    assert payload == {"type": "chunk", "text": "hello"}


def test_sse_preserves_unicode_without_ascii_escape() -> None:
    """Devanagari must NOT be escaped — Hindi chunks should ride the wire as-is."""
    out = _sse({"type": "chunk", "text": "नमस्ते"})
    assert "नमस्ते".encode() in out


# --------------------------------------------------------------------------- #
# _run_generate / _run_translate (anyio thread offload)
# --------------------------------------------------------------------------- #


class _StubClient:
    def __init__(self) -> None:
        self.last_history: list[ChatMessage] | None = None
        self.last_use_grounding: bool | None = None

    def generate(self, history: list[ChatMessage], use_grounding: bool = True) -> ChatResult:
        self.last_history = list(history)
        self.last_use_grounding = use_grounding
        return ChatResult(text="ok")

    def stream(self, history: list[ChatMessage], use_grounding: bool = True) -> Any:
        raise NotImplementedError


class _StubTranslator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def translate(self, text: str, target: str, source: str | None = None) -> str:
        self.calls.append((text, target, source))
        return f"<{target}> {text}"


async def test_run_generate_offloads_via_anyio() -> None:
    stub = _StubClient()
    history = [ChatMessage(role="user", text="hi")]
    result = await _run_generate(stub, history, True)
    assert result.text == "ok"
    assert stub.last_history == history
    assert stub.last_use_grounding is True


async def test_run_translate_offloads_via_anyio() -> None:
    stub = _StubTranslator()
    out = await _run_translate(stub, "hello", "hi", "en")
    assert out == "<hi> hello"
    assert stub.calls == [("hello", "hi", "en")]


async def test_run_translate_default_source_is_none() -> None:
    stub = _StubTranslator()
    out = await _run_translate(stub, "hello", "hi")
    assert out == "<hi> hello"
    assert stub.calls == [("hello", "hi", None)]
