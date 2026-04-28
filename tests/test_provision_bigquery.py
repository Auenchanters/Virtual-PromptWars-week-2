"""Tests for the BigQuery provisioning script.

Verifies the DDL produced is idempotent (CREATE IF NOT EXISTS), correctly
parameterized, and that the CLI surface accepts both project-prefixed and
bare dataset names.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.provision_bigquery import (  # noqa: E402
    CREATE_DATASET_DDL,
    CREATE_TABLE_DDL,
    DEFAULT_DATASET_FQN,
    DEFAULT_TABLE,
    _split_dataset_fqn,
    main,
    provision,
)


class _FakeQueryJob:
    def __init__(self) -> None:
        self.result_called = False

    def result(self) -> None:
        self.result_called = True


class _FakeBQClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(self, sql: str) -> _FakeQueryJob:
        self.queries.append(sql)
        return _FakeQueryJob()


def test_split_dataset_fqn_accepts_project_prefixed_form() -> None:
    assert _split_dataset_fqn("votewise.events") == ("votewise", "events")


def test_split_dataset_fqn_accepts_bare_dataset_name() -> None:
    assert _split_dataset_fqn("events") == ("", "events")


def test_split_dataset_fqn_rejects_three_part_name() -> None:
    with pytest.raises(ValueError, match="Bad dataset FQN"):
        _split_dataset_fqn("a.b.c")


def test_provision_runs_idempotent_ddl_in_correct_order() -> None:
    fake = _FakeBQClient()
    statements = provision(
        bq_client=fake, project="virtual-promptwars-week2", dataset="events", table="chat_turns"
    )
    assert len(statements) == 2
    # Dataset DDL first.
    assert "CREATE SCHEMA IF NOT EXISTS" in fake.queries[0]
    assert "virtual-promptwars-week2" in fake.queries[0]
    assert "events" in fake.queries[0]
    # Then table DDL.
    assert "CREATE TABLE IF NOT EXISTS" in fake.queries[1]
    assert "chat_turns" in fake.queries[1]
    assert "PARTITION BY DATE(ts)" in fake.queries[1]
    # Schema column names from app/analytics.py must all appear.
    for col in ("language", "topic", "latency_ms", "used_grounding", "citation_count"):
        assert col in fake.queries[1]


def test_provision_rejects_empty_project() -> None:
    with pytest.raises(ValueError, match="project"):
        provision(bq_client=_FakeBQClient(), project="", dataset="events", table="chat_turns")


def test_default_ddl_constants_reference_expected_tables() -> None:
    assert "{project}" in CREATE_DATASET_DDL
    assert "{table}" in CREATE_TABLE_DDL
    assert DEFAULT_DATASET_FQN == "votewise.events"
    assert DEFAULT_TABLE == "chat_turns"


def test_main_returns_2_when_project_cannot_be_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    rc = main(["--dataset", "events"])  # bare dataset, no project anywhere
    assert rc == 2


def test_main_provisions_when_project_supplied() -> None:
    """``main`` should call into ``provision`` with the resolved project + dataset."""
    seen: dict[str, Any] = {}
    fake_bq = _FakeBQClient()

    def _fake_factory(project: str) -> _FakeBQClient:
        seen["project"] = project
        return fake_bq

    rc = main(
        ["--project", "virtual-promptwars-week2", "--dataset", "votewise.events"],
        client_factory=_fake_factory,
    )
    assert rc == 0
    assert seen["project"] == "virtual-promptwars-week2"
    # Both DDL statements were issued.
    assert len(fake_bq.queries) == 2
    assert "CREATE SCHEMA IF NOT EXISTS" in fake_bq.queries[0]
    assert "CREATE TABLE IF NOT EXISTS" in fake_bq.queries[1]
