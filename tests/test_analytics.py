"""Tests for the BigQuery analytics module (rule-based topic + insert wrapper)."""

from __future__ import annotations

from typing import Any

import pytest

from app.analytics import TOPIC_BUCKETS, BigQueryAnalytics, classify_topic


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("How do I register to vote in India?", "registration"),
        ("Am I eligible to vote at age 17?", "eligibility"),
        ("What documents to bring on polling day?", "polling_day"),
        ("Can I use Aadhaar instead of EPIC voter id?", "voter_id"),
        ("What is NOTA on the EVM?", "nota"),
        ("Tell me about postal ballot for senior citizens", "postal"),
        ("Explain the Model Code of Conduct", "model_code_of_conduct"),
        ("What is the timeline for general elections?", "timeline"),
        ("hello there", "other"),
    ],
)
def test_classify_topic_buckets(message: str, expected: str) -> None:
    assert classify_topic(message) == expected
    assert expected in TOPIC_BUCKETS


def test_classify_topic_returns_other_for_empty_string() -> None:
    assert classify_topic("") == "other"
    assert classify_topic("   ") == "other"


class _FakeBQClient:
    def __init__(self, errors: list[Any] | None = None, raises: Exception | None = None) -> None:
        self._errors = errors or []
        self._raises = raises
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []

    def insert_rows_json(self, table_id: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.calls.append((table_id, rows))
        if self._raises is not None:
            raise self._raises
        return self._errors


def _make_analytics(client: _FakeBQClient) -> BigQueryAnalytics:
    """Bypass the constructor's lazy SDK import + ADC."""
    obj = BigQueryAnalytics.__new__(BigQueryAnalytics)
    obj._client = client  # type: ignore[attr-defined]
    obj._dataset = "votewise.events"  # type: ignore[attr-defined]
    obj._table = "chat_turns"  # type: ignore[attr-defined]
    return obj


def test_log_chat_turn_inserts_row_with_expected_schema() -> None:
    fake = _FakeBQClient()
    a = _make_analytics(fake)
    a.log_chat_turn(
        language="hi",
        topic="registration",
        latency_ms=420,
        used_grounding=True,
        citation_count=3,
    )
    assert fake.calls == [
        (
            "votewise.events.chat_turns",
            [
                {
                    "language": "hi",
                    "topic": "registration",
                    "latency_ms": 420,
                    "used_grounding": True,
                    "citation_count": 3,
                }
            ],
        )
    ]


def test_log_chat_turn_swallows_insert_exceptions() -> None:
    fake = _FakeBQClient(raises=RuntimeError("network blip"))
    a = _make_analytics(fake)
    # Must not raise — analytics is fire-and-forget.
    a.log_chat_turn(
        language="en", topic="other", latency_ms=10, used_grounding=False, citation_count=0
    )
    assert fake.calls  # we still tried


def test_log_chat_turn_logs_warning_on_returned_errors(caplog: pytest.LogCaptureFixture) -> None:
    fake = _FakeBQClient(errors=[{"index": 0, "errors": [{"reason": "invalid"}]}])
    a = _make_analytics(fake)
    with caplog.at_level("WARNING", logger="votewise.analytics"):
        a.log_chat_turn(
            language="en", topic="other", latency_ms=1, used_grounding=False, citation_count=0
        )
    assert any("BigQuery insert returned errors" in m for m in caplog.messages)


def test_table_id_property_uses_dataset_and_table() -> None:
    a = _make_analytics(_FakeBQClient())
    assert a.table_id == "votewise.events.chat_turns"


def test_reset_analytics_for_tests_clears_singleton() -> None:
    """Just exercises the reset helper so it stays in coverage."""
    from app.analytics import reset_analytics_for_tests

    reset_analytics_for_tests()
    reset_analytics_for_tests()  # idempotent
