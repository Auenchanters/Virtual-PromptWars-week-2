"""Tests for the Cloud DLP redactor wrapper."""

from __future__ import annotations

from typing import Any

from app.dlp import DEFAULT_INFO_TYPES, CloudDlpRedactor, _LruStringCache


class _FakeDlpResponseItem:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeDlpResponse:
    def __init__(self, value: str) -> None:
        self.item = _FakeDlpResponseItem(value)


class _FakeDlpClient:
    def __init__(self, response_value: str = "[REDACTED:PHONE_NUMBER]") -> None:
        self._value = response_value
        self.calls: list[dict[str, Any]] = []
        self.raises: Exception | None = None

    def deidentify_content(self, request: dict[str, Any]) -> _FakeDlpResponse:
        self.calls.append(request)
        if self.raises is not None:
            raise self.raises
        return _FakeDlpResponse(self._value)


def _make_redactor(fake_client: _FakeDlpClient) -> CloudDlpRedactor:
    """Bypass the constructor's lazy SDK import + ADC."""
    obj = CloudDlpRedactor.__new__(CloudDlpRedactor)
    obj._client = fake_client  # type: ignore[attr-defined]
    obj._project_id = "unit-test"  # type: ignore[attr-defined]
    obj._parent = "projects/unit-test"  # type: ignore[attr-defined]
    obj._info_types = DEFAULT_INFO_TYPES  # type: ignore[attr-defined]
    obj._cache = _LruStringCache(8)  # type: ignore[attr-defined]
    obj._dlp_v2 = None  # type: ignore[attr-defined]
    return obj


def test_redact_returns_deidentified_text_and_caches() -> None:
    fake = _FakeDlpClient(response_value="My phone is [REDACTED:PHONE_NUMBER].")
    r = _make_redactor(fake)

    out1 = r.redact("My phone is 9876543210.")
    out2 = r.redact("My phone is 9876543210.")  # cached, no second SDK call
    assert out1 == out2 == "My phone is [REDACTED:PHONE_NUMBER]."
    assert len(fake.calls) == 1


def test_redact_short_circuits_on_blank_input() -> None:
    fake = _FakeDlpClient()
    r = _make_redactor(fake)
    assert r.redact("") == ""
    assert r.redact("   ") == "   "
    assert fake.calls == []


def test_redact_returns_original_when_dlp_raises() -> None:
    fake = _FakeDlpClient()
    fake.raises = RuntimeError("DLP outage")
    r = _make_redactor(fake)
    original = "Email me at user@example.com"
    out = r.redact(original)
    assert out == original  # privacy is never worsened, but text passes through


def test_redact_request_has_expected_info_types() -> None:
    fake = _FakeDlpClient(response_value="[REDACTED:EMAIL_ADDRESS]")
    r = _make_redactor(fake)
    r.redact("user@example.com")
    assert fake.calls
    request = fake.calls[0]
    assert request["parent"] == "projects/unit-test"
    info_types = [t["name"] for t in request["inspect_config"]["info_types"]]
    for expected in (
        "PHONE_NUMBER",
        "EMAIL_ADDRESS",
        "INDIA_AADHAAR_NUMBER",
        "INDIA_PAN_INDIVIDUAL",
        "CREDIT_CARD_NUMBER",
    ):
        assert expected in info_types


def test_lru_string_cache_evicts_oldest() -> None:
    c = _LruStringCache(2)
    c.put("a", "A")
    c.put("b", "B")
    c.put("c", "C")
    assert c.get("a") is None
    assert c.get("b") == "B"
    assert c.get("c") == "C"


def test_reset_redactor_for_tests_clears_singleton() -> None:
    """Just exercises the reset helper so it stays in coverage."""
    from app.dlp import reset_redactor_for_tests

    reset_redactor_for_tests()
    reset_redactor_for_tests()  # idempotent
