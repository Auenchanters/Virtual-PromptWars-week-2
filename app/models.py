"""Pydantic request/response models and TypedDicts for SSE events.

Rubric: Code Quality (typed payloads, single source of truth for the API
shapes the routers and tests assert against).
"""

from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel, Field, field_validator

from app.deps import MAX_HISTORY_MESSAGES, MAX_MESSAGE_CHARS
from app.speech import MAX_TTS_CHARS
from app.translation import SUPPORTED_CODES


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    history: list[ChatTurn] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    target_language: str = Field(default="en", min_length=2, max_length=5)
    use_grounding: bool = True

    @field_validator("message")
    @classmethod
    def _strip_message(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("message must not be blank")
        return stripped

    @field_validator("target_language")
    @classmethod
    def _validate_language(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in SUPPORTED_CODES:
            raise ValueError(f"unsupported target_language: {v}")
        return v


class CitationModel(BaseModel):
    title: str
    uri: str


class ChatResponse(BaseModel):
    reply: str
    reply_en: str | None = None
    disclaimer: str
    citations: list[CitationModel] = Field(default_factory=list)
    language: str = "en"


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS * 2)
    target: str = Field(min_length=2, max_length=5)
    source: str | None = Field(default=None, max_length=5)

    @field_validator("target")
    @classmethod
    def _validate_target(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in SUPPORTED_CODES:
            raise ValueError(f"unsupported target: {v}")
        return v


class TranslateResponse(BaseModel):
    text: str
    target: str


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TTS_CHARS)
    lang: str = Field(min_length=2, max_length=5)

    @field_validator("lang")
    @classmethod
    def _validate_lang(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in SUPPORTED_CODES:
            raise ValueError(f"unsupported lang: {v}")
        return v


class BoothSearchRequest(BaseModel):
    """Request body for ``POST /api/places/booth`` (Google Maps Places API)."""

    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)
    radius_m: int = Field(default=2000, ge=100, le=20000)


class BoothResult(BaseModel):
    name: str
    address: str
    distance_m: int = Field(ge=0)
    lat: float
    lng: float


class BoothSearchResponse(BaseModel):
    results: list[BoothResult] = Field(default_factory=list)


# --- SSE event TypedDicts (used at construction sites in routers/chat.py) --- #


class SSEMeta(TypedDict):
    type: str
    language: str


class SSEChunk(TypedDict):
    type: str
    text: str


class SSETranslated(TypedDict):
    type: str
    text: str
    lang: str


class SSEDone(TypedDict):
    type: str
    disclaimer: str
    language: str
    citations: list[dict[str, str]]
    reply_en: str | None


class SSEError(TypedDict):
    type: str
    detail: str


__all__ = [
    "BoothResult",
    "BoothSearchRequest",
    "BoothSearchResponse",
    "ChatRequest",
    "ChatResponse",
    "ChatTurn",
    "CitationModel",
    "SSEChunk",
    "SSEDone",
    "SSEError",
    "SSEMeta",
    "SSETranslated",
    "TranslateRequest",
    "TranslateResponse",
    "TtsRequest",
]
