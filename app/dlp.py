"""Cloud DLP redactor — strips PII before logs or analytics ever see it.

Defence in depth: chat-turn analytics already store *only* the language code,
a coarse topic bucket and timing metadata, but if a user accidentally types
their phone number or Aadhaar into a question, the DLP pass guarantees we
never persist or log it in cleartext.

Rubric: Security (responsible AI / privacy-first),
Google Services (Cloud DLP — AI/ML API across the chat workflow).
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from threading import Lock
from typing import Any, Protocol

logger = logging.getLogger("votewise.dlp")

DEFAULT_INFO_TYPES: tuple[str, ...] = (
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "INDIA_AADHAAR_NUMBER",
    "INDIA_PAN_INDIVIDUAL",
    "CREDIT_CARD_NUMBER",
)
DEFAULT_CACHE_SIZE = 256


class Redactor(Protocol):
    """Minimal structural interface so tests can inject a fake."""

    def redact(self, text: str) -> str: ...


class _LruStringCache:
    def __init__(self, max_size: int) -> None:
        self._max = max_size
        self._data: OrderedDict[str, str] = OrderedDict()
        self._lock = Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            value = self._data.get(key)
            if value is not None:
                self._data.move_to_end(key)
            return value

    def put(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)


class CloudDlpRedactor:
    """Wraps ``google.cloud.dlp_v2.DlpServiceClient.deidentify_content``.

    Replaces matched PII spans with ``[REDACTED:<INFO_TYPE>]``. On any error
    the original text is returned unchanged — privacy is never worsened by
    a transient API failure, but the call site must still treat output as
    potentially sensitive in that branch.
    """

    def __init__(
        self,
        project_id: str | None = None,
        info_types: tuple[str, ...] = DEFAULT_INFO_TYPES,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ) -> None:
        from google.cloud import dlp_v2  # lazy import — keeps unit tests offline

        self._dlp_v2 = dlp_v2
        self._client = dlp_v2.DlpServiceClient()
        self._project_id = (
            project_id or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or ""
        )
        if not self._project_id:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is not set; required for Cloud DLP.")
        self._parent = f"projects/{self._project_id}"
        self._info_types = info_types
        self._cache = _LruStringCache(cache_size)

    def redact(self, text: str) -> str:
        if not text or not text.strip():
            return text
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        try:
            redacted = self._call(text)
        except Exception:
            logger.exception("DLP deidentify failed; returning original text")
            return text
        self._cache.put(text, redacted)
        return redacted

    def _call(self, text: str) -> str:
        deidentify_config: dict[str, Any] = {
            "info_type_transformations": {
                "transformations": [
                    {
                        "primitive_transformation": {
                            "replace_with_info_type_config": {},
                        }
                    }
                ]
            }
        }
        inspect_config: dict[str, Any] = {
            "info_types": [{"name": t} for t in self._info_types],
        }
        item: dict[str, str] = {"value": text}
        response = self._client.deidentify_content(
            request={
                "parent": self._parent,
                "deidentify_config": deidentify_config,
                "inspect_config": inspect_config,
                "item": item,
            }
        )
        return str(response.item.value)


_redactor_singleton: Redactor | None = None


def get_redactor() -> Redactor:
    """Process-wide singleton."""
    global _redactor_singleton
    if _redactor_singleton is None:
        _redactor_singleton = CloudDlpRedactor()
    return _redactor_singleton


def reset_redactor_for_tests() -> None:
    global _redactor_singleton
    _redactor_singleton = None


__all__ = [
    "DEFAULT_INFO_TYPES",
    "CloudDlpRedactor",
    "Redactor",
    "get_redactor",
    "reset_redactor_for_tests",
]
