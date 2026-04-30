"""Direct tests for Pydantic validators in :mod:`app.models`.

The router-level integration tests cover the happy path through every model.
These tests pin the exact validator behaviour (whitespace stripping, blank
rejection, language code allow-listing, history length cap) so a regression
in the validator surface is caught even without firing up TestClient.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.deps import MAX_HISTORY_MESSAGES
from app.models import (
    ChatRequest,
    ChatTurn,
    TranslateRequest,
    TtsRequest,
)

# --------------------------------------------------------------------------- #
# ChatTurn
# --------------------------------------------------------------------------- #


def test_chat_turn_accepts_user_and_assistant_roles() -> None:
    assert ChatTurn(role="user", text="hi").role == "user"
    assert ChatTurn(role="assistant", text="hi").role == "assistant"


def test_chat_turn_rejects_invalid_role() -> None:
    with pytest.raises(ValidationError):
        ChatTurn(role="system", text="hi")


def test_chat_turn_rejects_blank_text() -> None:
    with pytest.raises(ValidationError):
        ChatTurn(role="user", text="")


# --------------------------------------------------------------------------- #
# ChatRequest validators
# --------------------------------------------------------------------------- #


def test_chat_request_strips_whitespace_in_message() -> None:
    req = ChatRequest(message="   hello world   ")
    assert req.message == "hello world"


def test_chat_request_rejects_blank_after_strip() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="     ")


def test_chat_request_validates_target_language_allow_list() -> None:
    # Supported language is accepted and lower-cased.
    assert ChatRequest(message="hi", target_language="HI").target_language == "hi"
    # Unsupported codes are rejected.
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", target_language="zz")


def test_chat_request_default_language_is_english() -> None:
    req = ChatRequest(message="hello")
    assert req.target_language == "en"
    assert req.use_grounding is True


def test_chat_request_history_max_length_enforced() -> None:
    too_many = [ChatTurn(role="user", text=f"msg-{i}") for i in range(MAX_HISTORY_MESSAGES + 1)]
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", history=too_many)


# --------------------------------------------------------------------------- #
# TranslateRequest validators
# --------------------------------------------------------------------------- #


def test_translate_request_validates_target_language() -> None:
    req = TranslateRequest(text="hello", target="HI")
    assert req.target == "hi"
    with pytest.raises(ValidationError):
        TranslateRequest(text="hello", target="zz")


def test_translate_request_optional_source_passes_through() -> None:
    req = TranslateRequest(text="hello", target="hi", source="en")
    assert req.source == "en"


# --------------------------------------------------------------------------- #
# TtsRequest validators
# --------------------------------------------------------------------------- #


def test_tts_request_validates_lang_allow_list() -> None:
    req = TtsRequest(text="hello", lang="EN")
    assert req.lang == "en"
    with pytest.raises(ValidationError):
        TtsRequest(text="hello", lang="zz")
