"""Tests for B1 — Two-stage preference dedup (Stage 2 sweep)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jane_conversation.memory import preference_optimizer as popt
from jane_conversation.memory.preference_merge_helpers import (
    merge_values as _merge_values,
)
from jane_conversation.memory.preference_merge_helpers import (
    pick_winner as _pick_winner,
)
from jane_conversation.memory.preference_optimizer import sweep_person

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_pool(fetchval_return=None):
    """Mock asyncpg pool. Per-call overrides are set on pool._conn.*."""
    pool = MagicMock()
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=fetchval_return)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    acq = MagicMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acq)
    pool._conn = conn
    return pool


def _row(pref_id, key, value, confidence=1.0, has_emb=True):
    return {
        "id": pref_id,
        "key": key,
        "value": value,
        "confidence": confidence,
        "last_reinforced": datetime.now(UTC),
        "has_emb": has_emb,
    }


def _full_row(pref_id, key, value, confidence=1.0, person="Alice"):
    return {
        "id": pref_id,
        "person_name": person,
        "key": key,
        "value": value,
        "confidence": confidence,
        "last_reinforced": datetime.now(UTC),
    }


def _mk_structured():
    s = MagicMock()
    s.delete_preference = AsyncMock(return_value={"key": "x", "value": "y"})
    s.save_preference = AsyncMock()
    return s


# ---------------------------------------------------------------------------
# Value merge + winner pick unit tests
# ---------------------------------------------------------------------------


class TestMergeHelpers:
    def test_merge_concatenates_non_substring(self):
        assert _merge_values("tea", "coffee") == "tea; coffee"

    def test_merge_keeps_winner_when_loser_is_substring(self):
        assert _merge_values("coffee and pizza", "coffee") == "coffee and pizza"

    def test_merge_value_length_cap(self):
        winner = "x" * 399
        assert _merge_values(winner, "extra") == winner  # concat would overflow → keep winner

    def test_merge_ignores_empty_loser(self):
        assert _merge_values("tea", "") == "tea"
        assert _merge_values("tea", "   ") == "tea"

    def test_merge_returns_loser_when_winner_empty(self):
        assert _merge_values("", "coffee") == "coffee"

    def test_pick_winner_by_confidence(self):
        a = {"id": 1, "confidence": 0.5, "last_reinforced": None}
        b = {"id": 2, "confidence": 0.9, "last_reinforced": None}
        winner, loser = _pick_winner(a, b)
        assert winner["id"] == 2 and loser["id"] == 1

    def test_pick_winner_tiebreaks_by_last_reinforced(self):
        old = datetime.now(UTC) - timedelta(days=30)
        new = datetime.now(UTC)
        a = {"id": 1, "confidence": 0.9, "last_reinforced": old}
        b = {"id": 2, "confidence": 0.9, "last_reinforced": new}
        winner, _ = _pick_winner(a, b)
        assert winner["id"] == 2


# ---------------------------------------------------------------------------
# Sweep guards
# ---------------------------------------------------------------------------


class TestSweepGuards:
    @pytest.mark.asyncio
    async def test_skips_few_prefs(self):
        pool = _make_pool(fetchval_return=None)  # no recent sweep
        pool._conn.fetch.return_value = [_row(i, f"k{i}", f"v{i}") for i in range(3)]
        result = await sweep_person(pool, MagicMock(), MagicMock(), _mk_structured(), "Alice")
        assert result.skipped_few_prefs
        assert result.auto_merges == 0

    @pytest.mark.asyncio
    async def test_skips_too_many_prefs(self):
        pool = _make_pool(fetchval_return=None)
        pool._conn.fetch.return_value = [_row(i, f"k{i}", f"v{i}") for i in range(201)]
        result = await sweep_person(pool, MagicMock(), MagicMock(), _mk_structured(), "Alice")
        assert result.skipped_too_many

    @pytest.mark.asyncio
    async def test_skips_recent(self):
        pool = _make_pool(fetchval_return=1)  # recent_sweep check returns truthy
        result = await sweep_person(pool, MagicMock(), MagicMock(), _mk_structured(), "Alice")
        assert result.skipped_recent


# ---------------------------------------------------------------------------
# Auto-merge (>= 0.95)
# ---------------------------------------------------------------------------


class TestAutoMerge:
    @pytest.mark.asyncio
    async def test_auto_merges_high_similarity_pair(self):
        pool = _make_pool(fetchval_return=None)
        # Need >= MIN_PREFS_TO_SWEEP (4) to bypass skip guard.
        live_rows = [
            _row(1, "food preferences", "coffee"),
            _row(2, "food preferences", "tea"),
            _row(3, "hobbies", "guitar"),
            _row(4, "sports", "tennis"),
        ]
        pair_rows = [{"a_id": 1, "b_id": 2, "sim": 0.97}]
        pool._conn.fetch.side_effect = [live_rows, pair_rows]
        pool._conn.fetchrow.side_effect = [
            _full_row(1, "food preferences", "coffee", 0.8),
            _full_row(2, "food preferences", "tea", 0.95),
            _full_row(2, "food preferences", "tea", 0.95),  # live re-check of winner
        ]
        structured = _mk_structured()
        result = await sweep_person(pool, MagicMock(), MagicMock(), structured, "Alice")
        assert result.auto_merges == 1
        assert result.after_count == 3
        structured.delete_preference.assert_awaited_once()
        # preference_merges INSERT was called
        insert_calls = [
            c for c in pool._conn.execute.await_args_list if c.args and "INSERT INTO preference_merges" in c.args[0]
        ]
        assert len(insert_calls) == 1


# ---------------------------------------------------------------------------
# Gemini arbitration (0.85 <= sim < 0.95)
# ---------------------------------------------------------------------------


def _mk_gemini(merge_decision: bool | None):
    """Build a mock client.models.generate_content that returns a given merge decision."""
    client = MagicMock()
    if merge_decision is None:
        raw = "not json"
    else:
        raw = f'{{"merge": {str(merge_decision).lower()}, "reason": "x"}}'
    part = MagicMock()
    part.text = raw
    content = MagicMock()
    content.parts = [part]
    cand = MagicMock()
    cand.content = content
    resp = MagicMock()
    resp.candidates = [cand]
    client.models.generate_content = MagicMock(return_value=resp)
    return client


class TestArbitration:
    @pytest.mark.asyncio
    async def test_merges_when_gemini_says_yes(self):
        pool = _make_pool(fetchval_return=None)
        pool._conn.fetch.side_effect = [
            [
                _row(1, "food preferences", "coffee"),
                _row(2, "drink pref", "coffee"),
                _row(3, "hobbies", "guitar"),
                _row(4, "sports", "tennis"),
            ],
            [{"a_id": 1, "b_id": 2, "sim": 0.90}],
        ]
        pool._conn.fetchrow.side_effect = [
            _full_row(1, "food preferences", "coffee"),  # arbitrate pref A
            _full_row(2, "drink pref", "coffee"),  # arbitrate pref B
            _full_row(1, "food preferences", "coffee"),  # merge: load A
            _full_row(2, "drink pref", "coffee", 0.9),  # merge: load B (higher conf)
            _full_row(2, "drink pref", "coffee", 0.9),  # live re-check winner
        ]
        client = _mk_gemini(True)
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=lambda fn: fn())
        structured = _mk_structured()
        result = await sweep_person(pool, client, hass, structured, "Alice")
        assert result.arbitrated_merges == 1
        assert result.arbitrated_vetoed == 0

    @pytest.mark.asyncio
    async def test_no_merge_when_gemini_says_no(self):
        pool = _make_pool(fetchval_return=None)
        pool._conn.fetch.side_effect = [
            [
                _row(1, "food preferences", "coffee"),
                _row(2, "food preferences", "decaf coffee"),
                _row(3, "hobbies", "guitar"),
                _row(4, "sports", "tennis"),
            ],
            [{"a_id": 1, "b_id": 2, "sim": 0.90}],
        ]
        pool._conn.fetchrow.side_effect = [
            _full_row(1, "food preferences", "coffee"),
            _full_row(2, "food preferences", "decaf coffee"),
        ]
        client = _mk_gemini(False)
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=lambda fn: fn())
        structured = _mk_structured()
        result = await sweep_person(pool, client, hass, structured, "Alice")
        assert result.arbitrated_merges == 0
        assert result.arbitrated_vetoed == 1
        structured.delete_preference.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bad_json_is_safe_default_no_merge(self):
        pool = _make_pool(fetchval_return=None)
        pool._conn.fetch.side_effect = [
            [_row(1, "a", "x"), _row(2, "b", "y"), _row(3, "c", "z"), _row(4, "d", "w")],
            [{"a_id": 1, "b_id": 2, "sim": 0.88}],
        ]
        pool._conn.fetchrow.side_effect = [_full_row(1, "a", "x"), _full_row(2, "b", "y")]
        client = _mk_gemini(None)  # returns garbage
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=lambda fn: fn())
        result = await sweep_person(pool, client, hass, MagicMock(), "Alice")
        assert result.arbitrated_merges == 0
        assert result.arbitrated_vetoed == 0


# ---------------------------------------------------------------------------
# Threshold + pair dedup within pass
# ---------------------------------------------------------------------------


class TestThresholdsAndPassDedup:
    @pytest.mark.asyncio
    async def test_no_pairs_when_all_below_threshold(self):
        pool = _make_pool(fetchval_return=None)
        pool._conn.fetch.side_effect = [
            [_row(i, f"k{i}", f"v{i}") for i in range(5)],
            [],  # SQL already filters < 0.85 — no rows returned
        ]
        result = await sweep_person(pool, MagicMock(), MagicMock(), _mk_structured(), "Alice")
        assert result.auto_merges == 0
        assert result.arbitrated_merges == 0

    @pytest.mark.asyncio
    async def test_row_only_merged_once_per_pass(self):
        """Rows where A-B sim=0.97 and B-C sim=0.96: B is consumed by first merge."""
        pool = _make_pool(fetchval_return=None)
        pool._conn.fetch.side_effect = [
            [_row(1, "a", "x"), _row(2, "b", "y"), _row(3, "c", "z"), _row(4, "d", "w")],
            [
                {"a_id": 1, "b_id": 2, "sim": 0.97},
                {"a_id": 2, "b_id": 3, "sim": 0.96},
            ],
        ]
        # Merge for pair 1-2: two loads (a, b) + live re-check (winner)
        pool._conn.fetchrow.side_effect = [
            _full_row(1, "a", "x", 0.9),
            _full_row(2, "b", "y", 0.5),
            _full_row(1, "a", "x", 0.9),  # re-check
        ]
        structured = _mk_structured()
        result = await sweep_person(pool, MagicMock(), MagicMock(), structured, "Alice")
        assert result.auto_merges == 1  # second pair skipped (b already consumed)


# ---------------------------------------------------------------------------
# Mid-merge abort (live re-check fails)
# ---------------------------------------------------------------------------


class TestMidMergeAbort:
    @pytest.mark.asyncio
    async def test_merge_aborted_when_winner_tombstoned_midway(self):
        pool = _make_pool(fetchval_return=None)
        pool._conn.fetch.side_effect = [
            [_row(1, "a", "x"), _row(2, "b", "y"), _row(3, "c", "z"), _row(4, "d", "w")],
            [{"a_id": 1, "b_id": 2, "sim": 0.97}],
        ]
        pool._conn.fetchrow.side_effect = [
            _full_row(1, "a", "x", 0.5),
            _full_row(2, "b", "y", 0.9),
            None,  # live re-check: winner forgotten mid-sweep
        ]
        structured = _mk_structured()
        result = await sweep_person(pool, MagicMock(), MagicMock(), structured, "Alice")
        assert result.auto_merges == 0
        assert result.aborted_mid_merge == 1
        structured.delete_preference.assert_not_awaited()


# ---------------------------------------------------------------------------
# Embedding backfill
# ---------------------------------------------------------------------------


class TestEmbeddingBackfill:
    @pytest.mark.asyncio
    async def test_backfills_only_missing(self):
        pool = _make_pool(fetchval_return=None)
        pool._conn.fetch.side_effect = [
            [
                _row(1, "a", "x", has_emb=True),
                _row(2, "b", "y", has_emb=False),
                _row(3, "c", "z", has_emb=True),
                _row(4, "d", "w", has_emb=False),
            ],
            [],
        ]
        gen_calls = []

        async def fake_gen(hass, client, text):
            gen_calls.append(text)
            return [0.1] * 768

        with (
            patch.object(popt, "generate_embedding", side_effect=fake_gen),
            patch.object(popt, "store_preference_embedding", new=AsyncMock()) as store_mock,
        ):
            result = await sweep_person(pool, MagicMock(), MagicMock(), _mk_structured(), "Alice")
        assert len(gen_calls) == 2  # only rows 2 and 4 needed embeddings
        assert store_mock.await_count == 2
        assert result.before_count == 4
