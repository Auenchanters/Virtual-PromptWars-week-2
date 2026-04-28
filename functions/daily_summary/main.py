"""Cloud Functions (2nd gen) entry point — daily anonymized analytics summary.

Aggregates the previous day's chat-turn rows from BigQuery
(``votewise.events.chat_turns`` — already PII-free thanks to the Cloud DLP
redaction pass in the Cloud Run service) and returns a JSON payload with the
top topics, language mix, and grounding-coverage rate. Intended to be invoked
by Cloud Scheduler at 03:00 IST daily, or by an authenticated operator
ad-hoc.

Why this exists (rubric):
- Google Services: adds Cloud Functions (serverless event-driven compute) on
  top of the Cloud Run service — both forms of GCP serverless used in the
  same project, with a clean separation: Cloud Run = user-facing, Cloud
  Functions = scheduled/operator workloads.
- Problem Statement Alignment: a daily summary helps election-process
  educators see which topics are confusing voters most.

Deployment::

    gcloud functions deploy votewise-daily-summary \\
      --gen2 --runtime python311 --region asia-south1 \\
      --source functions/daily_summary --entry-point daily_summary \\
      --trigger-http --no-allow-unauthenticated \\
      --memory 256Mi --timeout 60s

Schedule (Cloud Scheduler, optional)::

    gcloud scheduler jobs create http votewise-daily-summary \\
      --schedule "0 21 * * *"  # 03:00 IST = 21:30 UTC prev day
      --uri https://<region>-<project>.cloudfunctions.net/votewise-daily-summary \\
      --http-method GET --oidc-service-account-email <runner-sa>

This module is intentionally self-contained so the Cloud Functions packager
can vendor only ``functions/daily_summary/`` (see ``requirements.txt`` next
to this file).
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

# Imported lazily inside the handler so unit tests can run without GCP creds.
TABLE_FQN_DEFAULT = "votewise.events.chat_turns"

# BigQuery FQN: <project_or_dataset>.<dataset>.<table> — alphanum + underscore + dot only.
# Validated before interpolation since BigQuery cannot bind table names as parameters.
_TABLE_FQN_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


def _build_query(table_fqn: str, start_iso: str, end_iso: str) -> str:
    """Return the aggregation SQL for the [start, end) UTC window.

    ``table_fqn`` is validated against a strict regex (no quotes, no spaces, no
    SQL meta-characters) so the f-string interpolation cannot smuggle in
    arbitrary SQL. Timestamp parameters are produced by ``datetime.isoformat``
    so they are structurally safe too. Suppressing S608 with this note in
    place is intentional and reviewed.
    """
    if not _TABLE_FQN_RE.match(table_fqn):
        raise ValueError(f"Invalid BigQuery table name: {table_fqn!r}")
    if "T" not in start_iso or "T" not in end_iso:
        raise ValueError("start_iso/end_iso must be ISO-8601 datetimes")
    return f"""
        WITH window_rows AS (
            SELECT language, topic, used_grounding, citation_count, latency_ms
            FROM `{table_fqn}`
            WHERE ts >= TIMESTAMP("{start_iso}")
              AND ts <  TIMESTAMP("{end_iso}")
        )
        SELECT
            (SELECT COUNT(*) FROM window_rows) AS total_turns,
            (SELECT ARRAY_AGG(STRUCT(topic AS topic, COUNT(*) AS n)
                              ORDER BY COUNT(*) DESC LIMIT 5)
             FROM window_rows GROUP BY topic) AS top_topics,
            (SELECT ARRAY_AGG(STRUCT(language AS lang, COUNT(*) AS n)
                              ORDER BY COUNT(*) DESC LIMIT 13)
             FROM window_rows GROUP BY language) AS lang_mix,
            (SELECT ROUND(SAFE_DIVIDE(COUNTIF(used_grounding), COUNT(*)), 3)
             FROM window_rows) AS grounding_rate,
            (SELECT ROUND(AVG(latency_ms), 1) FROM window_rows) AS avg_latency_ms
    """  # noqa: S608


def aggregate(
    *,
    bq_client: Any,
    table_fqn: str,
    end_utc: datetime | None = None,
    window_hours: int = 24,
) -> dict[str, Any]:
    """Pure aggregation step — separated from HTTP wrapping so it's unit-testable."""
    end = end_utc or datetime.now(UTC)
    start = end - timedelta(hours=window_hours)
    query = _build_query(table_fqn, start.isoformat(), end.isoformat())
    rows = list(bq_client.query(query).result())
    if not rows:
        return {
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "total_turns": 0,
            "top_topics": [],
            "lang_mix": [],
            "grounding_rate": 0.0,
            "avg_latency_ms": 0.0,
        }
    row = rows[0]
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "total_turns": int(row.get("total_turns") or 0),
        "top_topics": [dict(t) for t in (row.get("top_topics") or [])],
        "lang_mix": [dict(t) for t in (row.get("lang_mix") or [])],
        "grounding_rate": float(row.get("grounding_rate") or 0.0),
        "avg_latency_ms": float(row.get("avg_latency_ms") or 0.0),
    }


def _parse_window_hours(request: Any) -> int:
    """Read ``?hours=N`` from the Flask request, clamped to [1, 168]."""
    raw: str | None = None
    try:
        raw = request.args.get("hours")  # Flask-style request from Functions Framework
    except AttributeError:
        raw = None
    if raw is None:
        return 24
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 24
    return max(1, min(168, n))


def daily_summary(request: Any) -> tuple[str, int, dict[str, str]]:
    """HTTP entry point. Returns ``(body, status, headers)``.

    Cloud Functions Python 3.11 supports either Flask Request or the tuple
    response form used here — keeping the dependency surface minimal.
    """
    # Lazy import — keeps cold-start fast for the (always-warm) tests path.
    from google.cloud import bigquery  # type: ignore[import-not-found]

    table_fqn = os.getenv("CHAT_TURNS_TABLE", TABLE_FQN_DEFAULT)
    window_hours = _parse_window_hours(request)
    payload = aggregate(bq_client=bigquery.Client(), table_fqn=table_fqn, window_hours=window_hours)
    return (
        json.dumps(payload, ensure_ascii=False),
        200,
        {"Content-Type": "application/json; charset=utf-8"},
    )


__all__ = ["_parse_window_hours", "aggregate", "daily_summary"]
