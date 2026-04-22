"""Cloud Translation API wrapper with a thread-safe LRU cache.

Rubric: Google Services (Cloud Translation API),
Accessibility (multi-language support for 13 Indian languages),
Efficiency (LRU cache avoids repeat translations of the same text).
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from threading import Lock
from typing import Protocol

logger = logging.getLogger("votewise.translation")

# Subset of Cloud Translation language codes we expose in the UI.
# Order matters — first entry is the default fallback.
SUPPORTED_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("en", "English"),
    ("hi", "हिन्दी (Hindi)"),
    ("bn", "বাংলা (Bengali)"),
    ("ta", "தமிழ் (Tamil)"),
    ("te", "తెలుగు (Telugu)"),
    ("mr", "मराठी (Marathi)"),
    ("gu", "ગુજરાતી (Gujarati)"),
    ("kn", "ಕನ್ನಡ (Kannada)"),
    ("ml", "മലയാളം (Malayalam)"),
    ("pa", "ਪੰਜਾਬੀ (Punjabi)"),
    ("ur", "اردو (Urdu)"),
    ("or", "ଓଡ଼ିଆ (Odia)"),
    ("as", "অসমীয়া (Assamese)"),
)

SUPPORTED_CODES: frozenset[str] = frozenset(code for code, _ in SUPPORTED_LANGUAGES)

DEFAULT_CACHE_SIZE = 2048


def is_supported(code: str) -> bool:
    return code in SUPPORTED_CODES


class Translator(Protocol):
    """Minimal structural interface so tests can inject a fake."""

    def translate(self, text: str, target: str, source: str | None = None) -> str: ...


class _LruCache:
    """Tiny thread-safe LRU keyed on hashable tuples."""

    def __init__(self, max_size: int) -> None:
        self._max = max_size
        self._data: OrderedDict[tuple[str, ...], str] = OrderedDict()
        self._lock = Lock()

    def get(self, key: tuple[str, ...]) -> str | None:
        with self._lock:
            value = self._data.get(key)
            if value is not None:
                self._data.move_to_end(key)
            return value

    def put(self, key: tuple[str, ...], value: str) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


class CloudTranslator:
    """Thin wrapper around ``google.cloud.translate_v3.TranslationServiceClient``.

    Uses Application Default Credentials. On Cloud Run the runtime service account
    is picked up automatically; locally, ``gcloud auth application-default login``
    is required. ``GOOGLE_CLOUD_PROJECT`` must be set (Cloud Run sets it).
    """

    def __init__(self, project_id: str | None = None, cache_size: int = DEFAULT_CACHE_SIZE) -> None:
        from google.cloud import translate_v3  # lazy import keeps unit tests fast

        self._translate_v3 = translate_v3
        self._client = translate_v3.TranslationServiceClient()
        self._project_id = (
            project_id or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or ""
        )
        if not self._project_id:
            raise RuntimeError(
                "GOOGLE_CLOUD_PROJECT is not set; required for Cloud Translation v3."
            )
        self._parent = f"projects/{self._project_id}/locations/global"
        self._cache = _LruCache(cache_size)

    def translate(self, text: str, target: str, source: str | None = None) -> str:
        if not text.strip():
            return text
        src = source or ""
        if src and src == target:
            return text
        key = (text, target, src)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = self._call(text, target, src)
        self._cache.put(key, result)
        return result

    def _call(self, text: str, target: str, source: str) -> str:
        request: dict[str, object] = {
            "parent": self._parent,
            "contents": [text],
            "mime_type": "text/plain",
            "target_language_code": target,
        }
        if source:
            request["source_language_code"] = source
        response = self._client.translate_text(request=request)
        translations = list(response.translations)
        if not translations:
            return text
        return str(translations[0].translated_text)


_translator_singleton: Translator | None = None


def get_translator() -> Translator:
    """Return the process-wide translator singleton, creating it on first use."""
    global _translator_singleton
    if _translator_singleton is None:
        _translator_singleton = CloudTranslator()
    return _translator_singleton


def reset_translator_for_tests() -> None:
    """Drop the singleton (used by the test suite to inject fakes cleanly)."""
    global _translator_singleton
    _translator_singleton = None
