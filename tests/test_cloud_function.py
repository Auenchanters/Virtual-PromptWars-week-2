"""Tests for the Cloud Functions daily-summary aggregator.

The HTTP entry point ``daily_summary`` is a thin wrapper around ``aggregate``;
this file unit-tests the pure aggregation step against a fake BigQuery client.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# The functions/ directory isn't on sys.path by default — make the Cloud
# Function importable as ``functions.daily_summary.main``.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from functions.daily_summary.main import (  # noqa: E402
    TABLE_FQN_DEFAULT,
    _build_query,
    _parse_window_hours,
    aggregate,
)


class _FakeRow(dict[str, Any]):
    """dict-like row that BigQuery's RowIterator yields."""


class _FakeQueryJob:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows

    def result(self) -> list[_FakeRow]:
        return self._rows


class _FakeBQClient:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows
        self.queries: list[str] = []

    def query(self, sql: str) -> _FakeQueryJob:
        self.queries.append(sql)
        return _FakeQueryJob(self._rows)


def test_build_query_uses_bounded_window_and_default_table() -> None:
    sql = _build_query(TABLE_FQN_DEFAULT, "2026-04-01T00:00:00+00:00", "2026-04-02T00:00:00+00:00")
    assert "votewise.events.chat_turns" in sql
    assert 'TIMESTAMP("2026-04-01T00:00:00+00:00")' in sql
    assert 'TIMESTAMP("2026-04-02T00:00:00+00:00")' in sql
    # Sanity: aggregations the dashboard relies on.
    for needle in ("total_turns", "top_topics", "lang_mix", "grounding_rate", "avg_latency_ms"):
        assert needle in sql


def test_aggregate_returns_zero_payload_when_table_is_empty() -> None:
    fake = _FakeBQClient(rows=[])
    end = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)
    out = aggregate(bq_client=fake, table_fqn=TABLE_FQN_DEFAULT, end_utc=end)
    assert out["total_turns"] == 0
    assert out["top_topics"] == []
    assert out["lang_mix"] == []
    assert out["grounding_rate"] == 0.0
    assert out["window"]["end"] == end.isoformat()


def test_aggregate_returns_summary_for_single_aggregated_row() -> None:
    row = _FakeRow(
        {
            "total_turns": 42,
            "top_topics": [{"topic": "registration", "n": 30}, {"topic": "voter_id", "n": 8}],
            "lang_mix": [{"lang": "en", "n": 25}, {"lang": "hi", "n": 17}],
            "grounding_rate": 0.881,
            "avg_latency_ms": 612.4,
        }
    )
    fake = _FakeBQClient(rows=[row])
    out = aggregate(bq_client=fake, table_fqn=TABLE_FQN_DEFAULT, window_hours=24)
    assert out["total_turns"] == 42
    assert out["top_topics"][0]["topic"] == "registration"
    assert out["lang_mix"][1]["lang"] == "hi"
    assert out["grounding_rate"] == 0.881
    assert out["avg_latency_ms"] == 612.4
    # Window is exactly 24 hours wide.
    start = datetime.fromisoformat(out["window"]["start"])
    end = datetime.fromisoformat(out["window"]["end"])
    assert (end - start).total_seconds() == 24 * 3600


def test_aggregate_uses_custom_window_hours() -> None:
    fake = _FakeBQClient(rows=[])
    out = aggregate(bq_client=fake, table_fqn=TABLE_FQN_DEFAULT, window_hours=6)
    start = datetime.fromisoformat(out["window"]["start"])
    end = datetime.fromisoformat(out["window"]["end"])
    assert (end - start).total_seconds() == 6 * 3600


def test_build_query_rejects_invalid_table_name() -> None:
    import pytest

    for bad in ("foo", "foo.bar", "foo;DROP", "a.b.c.d", "a.b.c'--"):
        with pytest.raises(ValueError, match="Invalid BigQuery table name"):
            _build_query(bad, "2026-04-01T00:00:00+00:00", "2026-04-02T00:00:00+00:00")


def test_build_query_rejects_non_iso_timestamps() -> None:
    import pytest

    with pytest.raises(ValueError, match="ISO-8601"):
        _build_query(TABLE_FQN_DEFAULT, "2026-04-01", "2026-04-02")


class _FakeFlaskArgs:
    def __init__(self, hours: str | None) -> None:
        self._hours = hours

    def get(self, key: str) -> str | None:
        return self._hours if key == "hours" else None


class _FakeFlaskRequest:
    def __init__(self, hours: str | None = None) -> None:
        self.args = _FakeFlaskArgs(hours)


def test_parse_window_hours_defaults_to_24_when_missing() -> None:
    assert _parse_window_hours(_FakeFlaskRequest()) == 24


def test_parse_window_hours_defaults_to_24_for_non_int_input() -> None:
    assert _parse_window_hours(_FakeFlaskRequest("not-a-number")) == 24


def test_parse_window_hours_clamps_to_supported_range() -> None:
    assert _parse_window_hours(_FakeFlaskRequest("0")) == 1  # below floor
    assert _parse_window_hours(_FakeFlaskRequest("9999")) == 168  # above ceiling
    assert _parse_window_hours(_FakeFlaskRequest("48")) == 48  # in-range


def test_parse_window_hours_handles_request_without_args_attribute() -> None:
    """Functions Framework can hand us a non-Flask object during local testing."""

    class _NoArgs:
        pass

    assert _parse_window_hours(_NoArgs()) == 24
