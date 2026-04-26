"""Tests for B2 weekly memory consolidation pass (JANE-81)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from jane_conversation.memory.consolidation_pass import (
    LAST_CONSOLIDATION_KEY,
    PREFS_ADDED_COUNTER_KEY,
    RECENTLY_REMOVED_KEY,
    RECENTLY_REMOVED_TTL_SECONDS,
    THRESHOLD_DEBOUNCE_HOURS,
    THRESHOLD_NEW_PREFS,
    ConsolidationDiff,
    backfill_last_consolidation_ts,
    fetch_recently_removed_for_prompt,
    is_recently_removed,
    run_consolidation_pass,
    should_trigger_threshold,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mk_record(d: dict):
    """Mock asyncpg Record: dict-like __getitem__ + .get."""
    rec = MagicMock()
    rec.__getitem__ = lambda _self, key: d[key]
    rec.get = d.get
    rec.keys = lambda: d.keys()
    return rec


class _FakeConn:
    """asyncpg-like connection. fetch/execute/fetchrow are AsyncMocks; transaction()
    returns a real async-context-manager that supports nesting (savepoints)."""

    def __init__(self):
        self.fetch = AsyncMock(return_value=[])
        self.execute = AsyncMock(return_value="DELETE 0")
        self.fetchrow = AsyncMock(return_value=None)
        self._transaction_depth = 0

    def transaction(self):
        outer = self

        class _Ctx:
            async def __aenter__(self):
                outer._transaction_depth += 1
                return outer

            async def __aexit__(self, exc_type, exc, tb):
                outer._transaction_depth -= 1
                # Re-raise exceptions inside savepoints (asyncpg semantics).
                return False

        return _Ctx()


def _mk_pool(conn):
    pool = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=cm)
    return pool


def _mk_redis():
    """Redis mock with the methods consolidation_pass touches."""
    r = MagicMock()
    r.get = AsyncMock(return_value=None)
    r.set = AsyncMock()
    r.zadd = AsyncMock()
    r.zscore = AsyncMock(return_value=None)
    r.zrem = AsyncMock(return_value=0)
    r.zrevrange = AsyncMock(return_value=[])
    r.zremrangebyscore = AsyncMock(return_value=0)
    r.expire = AsyncMock()
    r.incr = AsyncMock()
    return r


def _mk_structured():
    s = MagicMock()
    s.load_persons = AsyncMock(return_value=[])
    s.canonical_person = AsyncMock(side_effect=lambda name, fallback="", persons_cache=None: name or fallback)
    return s


# Snapshot stub used by all tests — _snapshot_counts hits fetchrow on _SNAPSHOT_SQL.
SNAPSHOT_BEFORE = {"prefs_live": 10, "prefs_tombstoned": 3, "entries_live": 5, "entries_tombstoned": 1}
SNAPSHOT_AFTER = {"prefs_live": 10, "prefs_tombstoned": 1, "entries_live": 5, "entries_tombstoned": 0}


def _setup_snapshots(conn, before=None, after=None):
    """Wire fetchrow to return BEFORE then AFTER snapshots in order."""
    if before is None:
        before = SNAPSHOT_BEFORE
    if after is None:
        after = SNAPSHOT_AFTER
    conn.fetchrow.side_effect = [_mk_record(before), _mk_record(after)]


# ---------------------------------------------------------------------------
# 1-3: purge behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_removes_old_tombstones_via_returning_clause(monkeypatch):
    """The DELETE FROM preferences uses RETURNING; purged_rows drives all 3 counters."""
    conn = _FakeConn()
    _setup_snapshots(conn)
    # First fetch = the RETURNING DELETE; subsequent fetches (if any) = empty.
    conn.fetch.side_effect = [
        [
            _mk_record({"person_name": "Alice", "key": "coffee_brand"}),
            _mk_record({"person_name": "Bob", "key": "lights_evening"}),
        ],
    ]
    conn.execute.return_value = "DELETE 1"
    pool = _mk_pool(conn)
    redis = _mk_redis()
    structured = _mk_structured()

    # Stub the side modules so we don't need real Gemini / health writes.
    from jane_conversation.memory import consolidation_pass as cp

    monkeypatch.setattr(cp, "preference_optimizer", MagicMock(sweep_all=AsyncMock(return_value={})))
    monkeypatch.setattr(cp, "collect_health_report", AsyncMock(return_value=MagicMock(extra={})))
    monkeypatch.setattr(cp, "persist_health_report", AsyncMock(return_value=42))
    monkeypatch.setattr(cp, "correction_status_counts", AsyncMock(return_value={}))

    diff = await run_consolidation_pass(pool, redis, structured, MagicMock(), MagicMock(), trigger="manual")

    assert diff.tombstones_purged_prefs == 2
    assert diff.removed_keys_total == 2
    assert "Alice:coffee brand" in diff.removed_keys_sample  # _normalize_pref_key replaces _ with space
    assert "Bob:lights evening" in diff.removed_keys_sample
    assert diff.tombstones_purged_entries == 1


@pytest.mark.asyncio
async def test_purge_query_excludes_recently_referenced_loser_ids(monkeypatch):
    """SQL must contain the 90-day preference_merges loser_id NOT IN clause."""
    conn = _FakeConn()
    _setup_snapshots(conn)
    conn.fetch.side_effect = [[]]  # no rows purged
    pool = _mk_pool(conn)
    redis = _mk_redis()

    from jane_conversation.memory import consolidation_pass as cp

    monkeypatch.setattr(cp, "preference_optimizer", MagicMock(sweep_all=AsyncMock(return_value={})))
    monkeypatch.setattr(cp, "collect_health_report", AsyncMock(return_value=MagicMock(extra={})))
    monkeypatch.setattr(cp, "persist_health_report", AsyncMock(return_value=1))
    monkeypatch.setattr(cp, "correction_status_counts", AsyncMock(return_value={}))

    await run_consolidation_pass(pool, redis, _mk_structured(), MagicMock(), MagicMock(), trigger="weekly")

    delete_sql = conn.fetch.call_args.args[0]
    assert "DELETE FROM preferences" in delete_sql
    assert "RETURNING person_name, key" in delete_sql
    assert "preference_merges" in delete_sql
    assert "INTERVAL '90 days'" in delete_sql


@pytest.mark.asyncio
async def test_diff_captures_before_after_and_duration(monkeypatch):
    conn = _FakeConn()
    _setup_snapshots(conn)
    conn.fetch.side_effect = [[]]
    pool = _mk_pool(conn)

    from jane_conversation.memory import consolidation_pass as cp

    monkeypatch.setattr(cp, "preference_optimizer", MagicMock(sweep_all=AsyncMock(return_value={})))
    monkeypatch.setattr(cp, "collect_health_report", AsyncMock(return_value=MagicMock(extra={})))
    monkeypatch.setattr(cp, "persist_health_report", AsyncMock(return_value=1))
    monkeypatch.setattr(cp, "correction_status_counts", AsyncMock(return_value={}))

    diff = await run_consolidation_pass(pool, _mk_redis(), _mk_structured(), MagicMock(), MagicMock(), trigger="weekly")

    assert diff.before == SNAPSHOT_BEFORE
    assert diff.after == SNAPSHOT_AFTER
    assert diff.duration_ms >= 0
    assert diff.trigger == "weekly"


# ---------------------------------------------------------------------------
# 4: savepoint isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_savepoint_isolates_purge_failure(monkeypatch):
    """Purge raises → all four post-purge counters reset to 0; trim + dedup still run."""
    conn = _FakeConn()
    _setup_snapshots(conn)
    conn.fetch.side_effect = RuntimeError("simulated DB failure on DELETE")
    pool = _mk_pool(conn)
    redis = _mk_redis()

    from jane_conversation.memory import consolidation_pass as cp

    sweep_mock = AsyncMock(return_value={})
    monkeypatch.setattr(cp, "preference_optimizer", MagicMock(sweep_all=sweep_mock))
    monkeypatch.setattr(cp, "collect_health_report", AsyncMock(return_value=MagicMock(extra={})))
    monkeypatch.setattr(cp, "persist_health_report", AsyncMock(return_value=1))
    monkeypatch.setattr(cp, "correction_status_counts", AsyncMock(return_value={}))

    diff = await run_consolidation_pass(pool, redis, _mk_structured(), MagicMock(), MagicMock(), trigger="manual")

    # All four purge counters reset
    assert diff.removed_keys_sample == []
    assert diff.removed_keys_total == 0
    assert diff.tombstones_purged_prefs == 0
    assert diff.tombstones_purged_entries == 0
    # Errors captured
    assert any("purge:" in e for e in diff.errors)
    # Trim + dedup still ran
    assert redis.zremrangebyscore.await_count == 1
    assert sweep_mock.await_count == 1


# ---------------------------------------------------------------------------
# 5-6: threshold trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_threshold_trigger_fires_at_50_with_old_last_run():
    redis = _mk_redis()
    redis.get = AsyncMock(
        side_effect=[
            str(THRESHOLD_NEW_PREFS),  # counter
            (datetime.now(UTC) - timedelta(days=2)).isoformat(),  # last run
        ]
    )
    assert await should_trigger_threshold(redis) is True


@pytest.mark.asyncio
async def test_threshold_debounces_within_24h():
    redis = _mk_redis()
    redis.get = AsyncMock(
        side_effect=[
            "200",  # counter way above threshold
            (datetime.now(UTC) - timedelta(hours=THRESHOLD_DEBOUNCE_HOURS - 1)).isoformat(),
        ]
    )
    assert await should_trigger_threshold(redis) is False


@pytest.mark.asyncio
async def test_threshold_returns_false_when_counter_below_threshold():
    redis = _mk_redis()
    redis.get = AsyncMock(return_value="10")
    assert await should_trigger_threshold(redis) is False


# ---------------------------------------------------------------------------
# 7: ZSET trim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidation_trims_expired_zset_entries(monkeypatch):
    """zremrangebyscore is called with cutoff = run_ts - 30d."""
    conn = _FakeConn()
    _setup_snapshots(conn)
    conn.fetch.side_effect = [[]]
    pool = _mk_pool(conn)
    redis = _mk_redis()

    from jane_conversation.memory import consolidation_pass as cp

    monkeypatch.setattr(cp, "preference_optimizer", MagicMock(sweep_all=AsyncMock(return_value={})))
    monkeypatch.setattr(cp, "collect_health_report", AsyncMock(return_value=MagicMock(extra={})))
    monkeypatch.setattr(cp, "persist_health_report", AsyncMock(return_value=1))
    monkeypatch.setattr(cp, "correction_status_counts", AsyncMock(return_value={}))

    await run_consolidation_pass(pool, redis, _mk_structured(), MagicMock(), MagicMock(), trigger="weekly")

    redis.zremrangebyscore.assert_awaited_once()
    call = redis.zremrangebyscore.await_args
    assert call.args[0] == RECENTLY_REMOVED_KEY
    assert call.args[1] == 0
    # cutoff should be (run_ts - 30d) ≈ now - 30d
    expected_cutoff_min = int((datetime.now(UTC) - timedelta(seconds=RECENTLY_REMOVED_TTL_SECONDS + 60)).timestamp())
    expected_cutoff_max = int((datetime.now(UTC) - timedelta(seconds=RECENTLY_REMOVED_TTL_SECONDS - 60)).timestamp())
    assert expected_cutoff_min <= call.args[2] <= expected_cutoff_max


# ---------------------------------------------------------------------------
# 8: emit health row with extra
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidation_writes_health_row_with_extra(monkeypatch):
    """persist_health_report receives a HealthReport with extra.consolidation populated."""
    conn = _FakeConn()
    _setup_snapshots(conn)
    conn.fetch.side_effect = [
        [_mk_record({"person_name": "Alice", "key": "coffee"})],
    ]
    conn.execute.return_value = "DELETE 0"
    pool = _mk_pool(conn)

    from jane_conversation.memory import consolidation_pass as cp

    monkeypatch.setattr(cp, "preference_optimizer", MagicMock(sweep_all=AsyncMock(return_value={})))
    fake_report = MagicMock(extra={})
    monkeypatch.setattr(cp, "collect_health_report", AsyncMock(return_value=fake_report))
    persist_mock = AsyncMock(return_value=99)
    monkeypatch.setattr(cp, "persist_health_report", persist_mock)
    monkeypatch.setattr(cp, "correction_status_counts", AsyncMock(return_value={"open": 1}))

    await run_consolidation_pass(pool, _mk_redis(), _mk_structured(), MagicMock(), MagicMock(), trigger="manual")

    persist_mock.assert_awaited_once()
    written = persist_mock.await_args.args[1]
    assert "consolidation" in written.extra
    # B4 (JANE-83): the same emission carries corrections_lifecycle counts.
    assert written.extra.get("corrections_lifecycle") == {"open": 1}
    cd = written.extra["consolidation"]
    assert cd["trigger"] == "manual"
    assert cd["tombstones_purged_prefs"] == 1
    assert cd["removed_keys_total"] == 1


# ---------------------------------------------------------------------------
# 9: backfill last consolidation timestamp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_last_consolidation_ts_from_pg():
    """Redis empty → backfill copies run_at from latest health row's extra.consolidation."""
    redis = _mk_redis()
    redis.get = AsyncMock(return_value=None)  # Redis empty

    pool = MagicMock()
    conn = MagicMock()
    expected_ts = "2026-04-26T08:00:00+00:00"
    conn.fetchrow = AsyncMock(return_value=_mk_record({"ts": expected_ts}))
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=cm)

    await backfill_last_consolidation_ts(pool, redis)

    redis.set.assert_awaited_once_with(LAST_CONSOLIDATION_KEY, expected_ts)


@pytest.mark.asyncio
async def test_backfill_last_consolidation_ts_no_op_when_redis_already_set():
    """If Redis already has the key, backfill leaves it alone."""
    redis = _mk_redis()
    redis.get = AsyncMock(return_value="2026-04-20T12:00:00+00:00")

    pool = MagicMock()  # should never be acquired

    await backfill_last_consolidation_ts(pool, redis)

    redis.set.assert_not_awaited()


# ---------------------------------------------------------------------------
# Bonus: prompt fetcher + is_recently_removed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_recently_removed_decodes_bytes_and_caps_at_limit():
    redis = _mk_redis()
    redis.zrevrange = AsyncMock(return_value=[b"Alice:coffee", "Bob:lights"])

    out = await fetch_recently_removed_for_prompt(redis, limit=5)

    assert out == ["Alice:coffee", "Bob:lights"]
    redis.zrevrange.assert_awaited_once_with(RECENTLY_REMOVED_KEY, 0, 4)


@pytest.mark.asyncio
async def test_is_recently_removed_true_when_zscore_present():
    redis = _mk_redis()
    redis.zscore = AsyncMock(return_value=1714128000)
    assert await is_recently_removed(redis, "Alice", "coffee") is True


@pytest.mark.asyncio
async def test_is_recently_removed_false_when_zscore_none():
    redis = _mk_redis()
    redis.zscore = AsyncMock(return_value=None)
    assert await is_recently_removed(redis, "Alice", "coffee") is False


# ---------------------------------------------------------------------------
# ConsolidationDiff.to_extra_dict / .summary
# ---------------------------------------------------------------------------


def test_consolidation_diff_to_extra_dict_is_json_safe():
    """Round-trip through json.dumps to confirm no datetime / set leaks."""
    import json

    diff = ConsolidationDiff(
        run_at=datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
        trigger="weekly",
        duration_ms=4321,
        tombstones_purged_prefs=2,
        removed_keys_sample=["Alice:coffee"],
        removed_keys_total=2,
    )
    out = json.dumps(diff.to_extra_dict())
    assert "2026-04-26T12:00:00+00:00" in out
    assert "Alice:coffee" in out


def test_consolidation_diff_summary_one_line():
    diff = ConsolidationDiff(
        run_at=datetime.now(UTC),
        trigger="manual",
        duration_ms=100,
        tombstones_purged_prefs=2,
        tombstones_purged_entries=0,
        merges_auto=1,
        merges_arbitrated=0,
        errors=["dedup: x"],
    )
    line = diff.summary()
    assert "trigger=manual" in line
    assert "purged_prefs=2" in line
    assert "merges=1 (1 auto, 0 arbitrated)" in line
    assert "errors=1" in line


# ---------------------------------------------------------------------------
# OpApplier guard tests live in tests/test_ops.py — extending below
# ---------------------------------------------------------------------------
# (Those are added to tests/test_ops.py in the same commit.)


# Silence unused-import lint when fields aren't all touched directly.
_ = (PREFS_ADDED_COUNTER_KEY,)
