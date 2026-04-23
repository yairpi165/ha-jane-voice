"""Tests for A3 — operations-based extraction (ops.py + ops_applier.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jane_conversation.memory.ops import (
    MemoryOp,
    OpResult,
    OpValidationError,
    _parse_one,
    parse_ops_json,
)
from jane_conversation.memory.ops_applier import OpApplier

# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


@pytest.fixture
def backend_mock():
    b = MagicMock()
    b.load = AsyncMock(return_value="")
    b.save = AsyncMock()
    b.delete_category = AsyncMock(return_value="prior content")
    b.append_event = AsyncMock()
    return b


@pytest.fixture
def structured_mock():
    s = MagicMock()
    s.save_preference = AsyncMock()
    s.save_person = AsyncMock()
    s.delete_preference = AsyncMock(return_value={"key": "x", "value": "y", "confidence": 1.0, "inferred": False, "source": "extraction"})
    s.load_preference = AsyncMock(return_value=None)
    s.load_person = AsyncMock(return_value=None)
    return s


@pytest.fixture
def pool_mock():
    """PG pool that never reports a replay (idempotency check returns None)."""
    pool = MagicMock()
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    acq = MagicMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acq)
    pool._conn = conn  # expose for assertions
    return pool


@pytest.fixture
def applier(backend_mock, structured_mock, pool_mock):
    return OpApplier(backend=backend_mock, structured=structured_mock, pg_pool=pool_mock)


# -------------------------------------------------------------------------
# parse / validate
# -------------------------------------------------------------------------


class TestParseOps:
    def test_parse_valid_add_preference(self):
        ops = parse_ops_json({
            "ops": [{
                "op": "ADD",
                "target": {"table": "preferences", "key": {"person": "Yair", "key": "coffee"}},
                "payload": {"value": "black", "inferred": False},
                "reason": "user said so",
                "confidence": 0.9,
            }]
        })
        assert len(ops) == 1
        assert ops[0].op == "ADD"
        assert ops[0].target_table == "preferences"
        assert ops[0].payload["value"] == "black"

    def test_parse_valid_update_memory_entries(self):
        ops = parse_ops_json({
            "ops": [{
                "op": "UPDATE",
                "target": {"table": "memory_entries", "key": {"category": "user", "user_name": "Yair"}},
                "payload": {"content": "Name: Yair\nBirthday: Jun 15"},
                "reason": "update",
            }]
        })
        assert len(ops) == 1
        assert ops[0].target_key["category"] == "user"

    def test_parse_rejects_unknown_table(self):
        with pytest.raises(OpValidationError):
            _parse_one({"op": "ADD", "target": {"table": "unknown", "key": {}},
                        "payload": {}, "reason": "x"})

    def test_parse_rejects_delete_on_persons(self):
        with pytest.raises(OpValidationError, match="DELETE on persons"):
            _parse_one({"op": "DELETE", "target": {"table": "persons", "key": {"name": "X"}},
                        "reason": "remove"})

    def test_parse_rejects_missing_reason_for_non_noop(self):
        with pytest.raises(OpValidationError, match="reason required"):
            _parse_one({"op": "ADD", "target": {"table": "preferences",
                                                  "key": {"person": "A", "key": "b"}},
                        "payload": {"value": "c"}})

    def test_parse_accepts_noop_without_payload(self):
        op = _parse_one({"op": "NOOP", "reason": "nothing to save"})
        assert op.op == "NOOP"
        assert op.target_table is None

    def test_parse_drops_invalid_op_and_keeps_others(self):
        ops = parse_ops_json({
            "ops": [
                {"op": "BOGUS", "reason": "x"},
                {"op": "NOOP", "reason": "valid"},
            ]
        })
        assert len(ops) == 1
        assert ops[0].op == "NOOP"

    def test_parse_rejects_events_update(self):
        with pytest.raises(OpValidationError, match="events table only supports ADD"):
            _parse_one({"op": "UPDATE", "target": {"table": "events", "key": {"event_type": "correction"}},
                        "payload": {"description": "x"}, "reason": "y"})

    def test_parse_bare_list_fallback(self):
        ops = parse_ops_json([{"op": "NOOP", "reason": "x"}])
        assert len(ops) == 1

    def test_parse_malformed_root_returns_empty(self):
        assert parse_ops_json("not a dict or list") == []

    def test_idempotency_hash_is_stable(self):
        op1 = MemoryOp(op="ADD", target_table="preferences",
                        target_key={"person": "A", "key": "b"},
                        payload={}, reason="r")
        op2 = MemoryOp(op="ADD", target_table="preferences",
                        target_key={"key": "b", "person": "A"},   # different key order
                        payload={}, reason="r")
        assert op1.idempotency_hash("s1") == op2.idempotency_hash("s1")
        assert op1.idempotency_hash("s1") != op1.idempotency_hash("s2")


# -------------------------------------------------------------------------
# apply
# -------------------------------------------------------------------------


class TestApply:
    @pytest.mark.asyncio
    async def test_apply_add_preference_calls_save_and_logs(self, applier, structured_mock, pool_mock):
        ops = parse_ops_json({"ops": [{"op": "ADD",
            "target": {"table": "preferences", "key": {"person": "Yair", "key": "coffee"}},
            "payload": {"value": "black"}, "reason": "new", "confidence": 0.9}]})
        result = await applier.apply_all(ops, "Yair", "sess-1")
        assert result.added == 1
        structured_mock.save_preference.assert_awaited_once()
        # memory_ops INSERT recorded
        assert pool_mock._conn.execute.await_count >= 1

    @pytest.mark.asyncio
    async def test_apply_update_memory_entries(self, applier, backend_mock):
        ops = parse_ops_json({"ops": [{"op": "UPDATE",
            "target": {"table": "memory_entries", "key": {"category": "family"}},
            "payload": {"content": "two lines"}, "reason": "add member"}]})
        result = await applier.apply_all(ops, "Yair", "sess-1",
                                          memory_snapshot={"family": "one line"})
        assert result.updated == 1
        backend_mock.save.assert_awaited_once_with("family", "two lines", None)

    @pytest.mark.asyncio
    async def test_apply_delete_preference(self, applier, structured_mock):
        ops = parse_ops_json({"ops": [{"op": "DELETE",
            "target": {"table": "preferences", "key": {"person": "Yair", "key": "coffee"}},
            "reason": "forget"}]})
        result = await applier.apply_all(ops, "Yair", "sess-1")
        assert result.deleted == 1
        structured_mock.delete_preference.assert_awaited_once_with("Yair", "coffee")

    @pytest.mark.asyncio
    async def test_apply_noop_logs_row_only(self, applier, backend_mock, structured_mock, pool_mock):
        ops = parse_ops_json({"ops": [{"op": "NOOP", "reason": "nothing"}]})
        result = await applier.apply_all(ops, "Yair", "sess-1")
        assert result.nooped == 1
        backend_mock.save.assert_not_called()
        structured_mock.save_preference.assert_not_called()
        # memory_ops row still inserted
        assert pool_mock._conn.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_apply_person_add_stores_birth_date(self, applier, structured_mock):
        ops = parse_ops_json({"ops": [{"op": "ADD",
            "target": {"table": "persons", "key": {"name": "Yair"}},
            "payload": {"birth_date": "1991-04-19"}, "reason": "birthday"}]})
        await applier.apply_all(ops, "Yair", "sess-1")
        call = structured_mock.save_person.await_args
        import datetime as _dt
        assert call.kwargs["birth_date"] == _dt.date(1991, 4, 19)

    @pytest.mark.asyncio
    async def test_failing_op_does_not_abort_batch(self, applier, structured_mock):
        structured_mock.save_preference.side_effect = [RuntimeError("boom"), None]
        ops = parse_ops_json({"ops": [
            {"op": "ADD", "target": {"table": "preferences", "key": {"person": "A", "key": "k1"}},
             "payload": {"value": "v"}, "reason": "r"},
            {"op": "ADD", "target": {"table": "preferences", "key": {"person": "A", "key": "k2"}},
             "payload": {"value": "v"}, "reason": "r"},
        ]})
        result = await applier.apply_all(ops, "A", "sess-1")
        assert result.failed == 1
        assert result.added == 1

    @pytest.mark.asyncio
    async def test_records_session_id_and_user_name(self, applier, pool_mock):
        ops = parse_ops_json({"ops": [{"op": "NOOP", "reason": "x"}]})
        await applier.apply_all(ops, "Yair", "sess-abc")
        insert_call = pool_mock._conn.execute.await_args_list[0]
        args = insert_call.args
        # positional: sql, op, table, key, payload, before, reason, confidence, user, session, op_hash, raw
        assert args[-4] == "Yair"  # user_name
        assert args[-3] == "sess-abc"  # session_id
        assert len(args[-2]) == 32  # op_hash md5 hex


# -------------------------------------------------------------------------
# idempotency (§1.2 of review)
# -------------------------------------------------------------------------


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_replay_same_ops_does_not_duplicate(self, applier, structured_mock, pool_mock):
        """Second apply_all with same session_id + ops detects replay and skips."""
        ops = parse_ops_json({"ops": [{"op": "ADD",
            "target": {"table": "preferences", "key": {"person": "Yair", "key": "k"}},
            "payload": {"value": "v"}, "reason": "r"}]})

        await applier.apply_all(ops, "Yair", "sess-1")
        # After first apply, fetchrow should "find" the prior op_hash on replay.
        pool_mock._conn.fetchrow = AsyncMock(return_value={"id": 1})

        result2 = await applier.apply_all(ops, "Yair", "sess-1")
        assert result2.skipped == 1
        assert result2.added == 0

    @pytest.mark.asyncio
    async def test_partial_replay_after_crash(self, applier, pool_mock):
        """Ops 1-3 applied, crash, replay all 5 — 1-3 skipped, 4-5 applied."""
        applied_hashes: set[str] = set()

        async def _fetchrow(_sql, op_hash):
            return {"id": 1} if op_hash in applied_hashes else None

        pool_mock._conn.fetchrow = AsyncMock(side_effect=_fetchrow)

        def make_op(i):
            return {"op": "ADD",
                    "target": {"table": "preferences", "key": {"person": "P", "key": f"k{i}"}},
                    "payload": {"value": "v"}, "reason": "r"}

        all_ops = parse_ops_json({"ops": [make_op(i) for i in range(1, 6)]})
        # Pretend first 3 already applied.
        for op in all_ops[:3]:
            applied_hashes.add(op.idempotency_hash("sess-x"))

        result = await applier.apply_all(all_ops, "P", "sess-x")
        assert result.skipped == 3
        assert result.added == 2


# -------------------------------------------------------------------------
# before_state
# -------------------------------------------------------------------------


class TestBeforeState:
    @pytest.mark.asyncio
    async def test_before_state_captured_from_snapshot_for_memory_entries(self, applier, pool_mock):
        ops = parse_ops_json({"ops": [{"op": "UPDATE",
            "target": {"table": "memory_entries", "key": {"category": "family"}},
            "payload": {"content": "new"}, "reason": "r"}]})
        await applier.apply_all(ops, "Yair", "sess-1",
                                 memory_snapshot={"family": "OLD CONTENT"})
        # memory_ops INSERT — before_state is positional arg index 5 (0-indexed after sql).
        insert_args = pool_mock._conn.execute.await_args_list[0].args
        before_json = insert_args[5]  # before_state JSON string
        assert before_json is not None
        assert "OLD CONTENT" in before_json

    @pytest.mark.asyncio
    async def test_before_state_null_for_add(self, applier, pool_mock):
        ops = parse_ops_json({"ops": [{"op": "ADD",
            "target": {"table": "preferences", "key": {"person": "A", "key": "k"}},
            "payload": {"value": "v"}, "reason": "r"}]})
        await applier.apply_all(ops, "A", "sess-1")
        insert_args = pool_mock._conn.execute.await_args_list[0].args
        assert insert_args[5] is None


# -------------------------------------------------------------------------
# OpResult sanity
# -------------------------------------------------------------------------


class TestOpResult:
    def test_summary_format(self):
        r = OpResult(added=2, updated=1, nooped=3)
        s = r.summary()
        assert "2 ADD" in s and "1 UPDATE" in s and "3 NOOP" in s
