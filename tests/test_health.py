"""Tests for B5 weekly memory health report (JANE-82)."""

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock

import pytest

from jane_conversation.memory.health import (
    HealthReport,
    collect_health_report,
    format_for_log,
    persist_health_report,
)


def _mock_pool(conn):
    """Wrap a mock conn so ``async with pool.acquire() as conn`` returns it."""
    pool = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool


def _conn_with(prefs_rows=None, fetchval_values=None):
    """Make a mock conn whose fetch/fetchval are pre-programmed.

    fetchval is called four times per collect() in this exact order:
    extraction_calls, consolidation_ops, corrections, forget_invocations.
    """
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=prefs_rows or [])
    conn.fetchval = AsyncMock(side_effect=fetchval_values or [0, 0, 0, 0])
    return conn


@pytest.mark.asyncio
async def test_collect_returns_zero_counts_on_empty_db():
    """Empty DB → all zeros, empty prefs dict."""
    pool = _mock_pool(_conn_with())

    report = await collect_health_report(pool, days=7)

    assert report.prefs_per_person == {}
    assert report.prefs_total == 0
    assert report.extraction_calls == 0
    assert report.consolidation_ops == 0
    assert report.corrections == 0
    assert report.forget_invocations == 0


@pytest.mark.asyncio
async def test_collect_aggregates_prefs_per_person():
    """fetch returns three persons → dict + denormalized total."""
    rows = [
        {"person_name": "Alice", "cnt": 12},
        {"person_name": "Bob", "cnt": 7},
        {"person_name": "Charlie", "cnt": 3},
    ]
    pool = _mock_pool(_conn_with(prefs_rows=rows, fetchval_values=[148, 4, 11, 2]))

    report = await collect_health_report(pool, days=7)

    assert report.prefs_per_person == {"Alice": 12, "Bob": 7, "Charlie": 3}
    assert report.prefs_total == 22
    assert report.extraction_calls == 148
    assert report.consolidation_ops == 4
    assert report.corrections == 11
    assert report.forget_invocations == 2


@pytest.mark.asyncio
async def test_extraction_calls_uses_distinct_session_id_and_excludes_nulls():
    """SQL must count DISTINCT session_id, exclude NULL, exclude tool-forget."""
    conn = _conn_with()
    pool = _mock_pool(conn)

    await collect_health_report(pool, days=7)

    # fetchval call #1 is the extraction_calls query
    extraction_sql = conn.fetchval.call_args_list[0].args[0]
    assert "COUNT(DISTINCT session_id)" in extraction_sql
    assert "session_id IS NOT NULL" in extraction_sql
    assert "session_id NOT LIKE 'tool-forget-%'" in extraction_sql


@pytest.mark.asyncio
async def test_consolidation_ops_uses_created_at_not_start_ts():
    """Episodes counted by production-time (created_at), not event-time (start_ts)."""
    conn = _conn_with()
    pool = _mock_pool(conn)

    await collect_health_report(pool, days=7)

    consolidation_sql = conn.fetchval.call_args_list[1].args[0]
    assert "created_at > NOW() - " in consolidation_sql
    assert "start_ts" not in consolidation_sql


@pytest.mark.asyncio
async def test_corrections_only_counts_update_ops():
    """Corrections SQL filters to op = 'UPDATE' with the 7-day window."""
    conn = _conn_with()
    pool = _mock_pool(conn)

    await collect_health_report(pool, days=7)

    corrections_sql = conn.fetchval.call_args_list[2].args[0]
    assert "op = 'UPDATE'" in corrections_sql
    assert "INTERVAL '1 day'" in corrections_sql


@pytest.mark.asyncio
async def test_forget_query_filters_tool_session():
    """Forget SQL filters DELETE rows by session_id LIKE 'tool-forget-%'."""
    conn = _conn_with()
    pool = _mock_pool(conn)

    await collect_health_report(pool, days=7)

    forget_sql = conn.fetchval.call_args_list[3].args[0]
    assert "op = 'DELETE'" in forget_sql
    assert "session_id LIKE 'tool-forget-%'" in forget_sql


@pytest.mark.asyncio
async def test_persist_prefs_total_matches_per_person_sum():
    """Persisting a desynced report (total != sum of per-person) raises."""
    from datetime import datetime

    pool = _mock_pool(MagicMock(fetchval=AsyncMock(return_value=42)))
    bad = HealthReport(
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC),
        prefs_per_person={"Alice": 3, "Bob": 4},
        prefs_total=99,  # desynced — should be 7
    )

    with pytest.raises(ValueError, match="prefs_total desync"):
        await persist_health_report(pool, bad)


@pytest.mark.asyncio
async def test_persist_inserts_row_per_call():
    """No silent dedup: two calls with overlapping windows → two inserts."""
    from datetime import datetime

    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=[101, 102])
    pool = _mock_pool(conn)

    period = datetime.now(UTC)
    report = HealthReport(period_start=period, period_end=period, prefs_per_person={"Alice": 1}, prefs_total=1)

    id1 = await persist_health_report(pool, report)
    id2 = await persist_health_report(pool, report)

    assert id1 == 101
    assert id2 == 102
    assert conn.fetchval.call_count == 2


def test_format_for_log_shape():
    """Log line covers all five metrics + window."""
    from datetime import datetime

    report = HealthReport(
        period_start=datetime(2026, 4, 19, tzinfo=UTC),
        period_end=datetime(2026, 4, 26, tzinfo=UTC),
        prefs_per_person={"Alice": 12, "Bob": 7},
        prefs_total=19,
        extraction_calls=148,
        consolidation_ops=4,
        corrections=11,
        forget_invocations=2,
    )

    line = format_for_log(report)

    assert "Alice:12" in line
    assert "Bob:7" in line
    assert "total=19" in line
    assert "extractions=148" in line
    assert "consolidations=4" in line
    assert "corrections=11" in line
    assert "forgets=2" in line
    assert "2026-04-19..2026-04-26" in line
