"""Tests for B4 corrections lifecycle (JANE-83).

Mock-pool style consistent with `test_structured_memory.py`. Real DB transitions
exercised in the dev VM E2E.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jane_conversation.memory.correction_lifecycle import (
    LifecycleSummary,
    correction_status_counts,
    sweep_corrections,
)


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


class TestSweepCorrections:
    @pytest.mark.asyncio
    async def test_sweep_runs_four_statements_in_order(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"

        await sweep_corrections(pool)

        # Order: applied→resolved (1), force-close (2), open→applied (3), DELETE (4).
        # Force-close MUST precede open→applied so a 91 d-old open hits the
        # 90 d cap and becomes resolved/auto_close, not transitioned to applied.
        assert conn.execute.call_count == 4
        sqls = [call[0][0] for call in conn.execute.call_args_list]
        # Statement 1 = applied → resolved (37 d window).
        assert "INTERVAL '37 days'" in sqls[0]
        assert "auto_close" not in sqls[0]
        # Statement 2 = force-close at 90 d, tagged auto_close.
        assert "INTERVAL '90 days'" in sqls[1]
        assert "auto_close" in sqls[1]
        # Statement 3 = open → applied (NOT EXISTS recurrence anti-join).
        assert "NOT EXISTS" in sqls[2]
        assert "INTERVAL '7 days'" in sqls[2]
        # Statement 4 = DELETE resolved older than 30 d.
        assert sqls[3].lstrip().startswith("DELETE")

    @pytest.mark.asyncio
    async def test_sweep_returns_summary_with_correct_counts(self, mock_pool):
        pool, conn = mock_pool
        # Order matches the SQL order in sweep_corrections:
        # applied→resolved, force-close, open→applied, delete.
        conn.execute.side_effect = ["UPDATE 2", "UPDATE 1", "UPDATE 5", "DELETE 3"]

        summary = await sweep_corrections(pool)

        assert isinstance(summary, LifecycleSummary)
        assert summary.transitioned_to_resolved == 2
        assert summary.force_closed == 1
        assert summary.transitioned_to_applied == 5
        assert summary.deleted == 3

    @pytest.mark.asyncio
    async def test_quiet_day_returns_zeros(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"

        summary = await sweep_corrections(pool)

        assert summary.transitioned_to_applied == 0
        assert summary.transitioned_to_resolved == 0
        assert summary.force_closed == 0
        assert summary.deleted == 0
        assert summary.any() is False

    @pytest.mark.asyncio
    async def test_open_to_applied_uses_7d_window_and_anti_join(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"
        await sweep_corrections(pool)

        # Statement 3 = open → applied.
        sql = conn.execute.call_args_list[2][0][0]
        assert "INTERVAL '7 days'" in sql
        assert "NOT EXISTS" in sql
        assert "IS NOT DISTINCT FROM" in sql  # NULL-safe user_name match
        assert "e2.id != e.id" in sql

    @pytest.mark.asyncio
    async def test_applied_to_resolved_uses_37d_age_window(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"
        await sweep_corrections(pool)

        sql = conn.execute.call_args_list[0][0][0]  # statement 1
        assert "INTERVAL '37 days'" in sql
        assert "status = 'applied'" in sql
        assert "resolved_at = NOW()" in sql

    @pytest.mark.asyncio
    async def test_force_close_at_90d_writes_auto_close_metadata(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"
        await sweep_corrections(pool)

        sql = conn.execute.call_args_list[1][0][0]  # statement 2 (force-close runs before open→applied)
        assert "INTERVAL '90 days'" in sql
        assert '"auto_close": true' in sql
        # `||` does shallow merge so coexisting metadata keys survive.
        assert "COALESCE(metadata, '{}'::jsonb) ||" in sql

    @pytest.mark.asyncio
    async def test_delete_targets_resolved_at_older_than_30d(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"
        await sweep_corrections(pool)

        sql = conn.execute.call_args_list[3][0][0]  # statement 4
        assert sql.lstrip().startswith("DELETE")
        assert "resolved_at < NOW() - INTERVAL '30 days'" in sql
        assert "status = 'resolved'" in sql


class TestCorrectionStatusCounts:
    @pytest.mark.asyncio
    async def test_groups_by_status(self, mock_pool):
        pool, conn = mock_pool
        conn.fetch.return_value = [
            {"status": "open", "cnt": 4},
            {"status": "applied", "cnt": 2},
            {"status": "resolved", "cnt": 7},
        ]

        counts = await correction_status_counts(pool)

        assert counts == {"open": 4, "applied": 2, "resolved": 7}

    @pytest.mark.asyncio
    async def test_empty_table_returns_empty_dict(self, mock_pool):
        pool, conn = mock_pool
        conn.fetch.return_value = []

        counts = await correction_status_counts(pool)

        assert counts == {}

    @pytest.mark.asyncio
    async def test_query_filters_to_correction_event_type(self, mock_pool):
        pool, conn = mock_pool
        conn.fetch.return_value = []

        await correction_status_counts(pool)

        sql = conn.fetch.call_args[0][0]
        assert "event_type = 'correction'" in sql
        assert "GROUP BY status" in sql


class TestLifecycleSummary:
    def test_to_dict_round_trips_fields(self):
        s = LifecycleSummary(
            transitioned_to_applied=1,
            transitioned_to_resolved=2,
            force_closed=3,
            deleted=4,
        )
        assert s.to_dict() == {
            "transitioned_to_applied": 1,
            "transitioned_to_resolved": 2,
            "force_closed": 3,
            "deleted": 4,
        }

    def test_any_false_when_all_zero(self):
        assert LifecycleSummary().any() is False

    def test_any_true_when_any_field_nonzero(self):
        assert LifecycleSummary(deleted=1).any() is True
        assert LifecycleSummary(transitioned_to_applied=1).any() is True
        assert LifecycleSummary(force_closed=1).any() is True
