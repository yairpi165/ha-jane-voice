"""Tests for ExtractionDebouncer (A1 — Memory Optimization)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

fakeredis = pytest.importorskip("fakeredis")
import fakeredis.aioredis  # noqa: E402, F401, F811

from jane_conversation.memory import debouncer as debouncer_mod  # noqa: E402
from jane_conversation.memory.debouncer import ExtractionDebouncer  # noqa: E402

ENTRY_ID = "test_entry"
USER = "Yair"
CONV = "conv-1"


@pytest.fixture
def redis_mock():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def process_memory_mock(monkeypatch):
    """Replace process_memory with an AsyncMock we can inspect."""
    mock = AsyncMock()
    monkeypatch.setattr(debouncer_mod, "process_memory", mock)
    return mock


@pytest.fixture
def fast_timers(monkeypatch):
    """Shrink debounce windows so tests finish quickly."""
    monkeypatch.setattr(debouncer_mod, "EXTRACTION_BURST_CAP_SECONDS", 0.3)
    monkeypatch.setattr(debouncer_mod, "EXTRACTION_DEFAULT_DELAY_SECONDS", 0.1)
    monkeypatch.setattr(debouncer_mod, "EXTRACTION_MIN_DELAY_SECONDS", 0.02)


@pytest.fixture
def client_mock():
    return MagicMock(name="gemini_client")


@pytest.fixture
def debouncer(hass_mock, redis_mock, client_mock, fast_timers, process_memory_mock):
    return ExtractionDebouncer(hass_mock, redis_mock, lambda: client_mock, ENTRY_ID)


def _key(user=USER, conv=CONV):
    return f"{ENTRY_ID}:{user}:{conv}"


# --- Basic ---


class TestSchedule:
    @pytest.mark.asyncio
    async def test_schedule_starts_timer(self, debouncer):
        await debouncer.schedule(USER, CONV, "היי", "שלום")
        assert len(debouncer._pending[_key()]) == 1
        assert _key() in debouncer._timers
        # Clean up
        debouncer._timers[_key()].cancel()

    @pytest.mark.asyncio
    async def test_second_turn_extends_burst_within_cap(self, debouncer):
        await debouncer.schedule(USER, CONV, "turn 1", "r1")
        first_deadline = debouncer._burst_deadline[_key()]
        await debouncer.schedule(USER, CONV, "turn 2", "r2")
        # Deadline is set once at burst start and never moves — that's the whole point.
        assert debouncer._burst_deadline[_key()] == first_deadline
        assert len(debouncer._pending[_key()]) == 2
        debouncer._timers[_key()].cancel()

    @pytest.mark.asyncio
    async def test_timer_fires_flushes_all_exchanges(self, debouncer, process_memory_mock):
        await debouncer.schedule(USER, CONV, "turn 1", "r1")
        await debouncer.schedule(USER, CONV, "turn 2", "r2")
        await debouncer.schedule(USER, CONV, "turn 3", "r3")
        # Wait past the burst cap
        await asyncio.sleep(0.5)
        # A2: single call with a list of all 3 exchanges (not one call per turn).
        assert process_memory_mock.await_count == 1
        exchanges_arg = process_memory_mock.await_args.args[2]
        assert len(exchanges_arg) == 3
        assert [ex["text"] for ex in exchanges_arg] == ["turn 1", "turn 2", "turn 3"]
        assert _key() not in debouncer._pending

    @pytest.mark.asyncio
    async def test_explicit_intent_immediate_flush(self, debouncer, process_memory_mock):
        await debouncer.schedule(USER, CONV, "תזכרי ש אני אוהב שקט", "בסדר", explicit_intent=True)
        # No sleep — explicit intent bypasses timer
        assert process_memory_mock.await_count == 1
        assert len(process_memory_mock.await_args.args[2]) == 1
        assert _key() not in debouncer._pending

    @pytest.mark.asyncio
    async def test_silent_does_not_queue(self, debouncer, process_memory_mock):
        await debouncer.schedule(USER, CONV, "אל תזכרי את זה", "אוקיי", is_silent=True)
        assert _key() not in debouncer._pending
        assert process_memory_mock.await_count == 0

    @pytest.mark.asyncio
    async def test_silent_mid_burst_does_not_drop_queue(self, debouncer, process_memory_mock):
        await debouncer.schedule(USER, CONV, "turn 1", "r1")
        await debouncer.schedule(USER, CONV, "silent", "r2", is_silent=True)
        # Silent did not clear pending; queue still has 1
        assert len(debouncer._pending[_key()]) == 1
        # Waiting should flush turn 1 only
        await asyncio.sleep(0.5)
        assert process_memory_mock.await_count == 1
        exchanges_arg = process_memory_mock.await_args_list[0].args[2]
        assert len(exchanges_arg) == 1
        assert exchanges_arg[0]["text"] == "turn 1"


# --- Concurrency ---


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_schedule_and_flush_no_loss(self, debouncer, process_memory_mock):
        """A schedule() arriving during flush must not be lost."""
        await debouncer.schedule(USER, CONV, "turn 1", "r1")
        # Kick off flush and a new schedule in parallel
        flush_task = asyncio.create_task(debouncer.flush(USER, CONV))
        await asyncio.sleep(0)  # let flush grab the lock first
        await debouncer.schedule(USER, CONV, "turn 2", "r2")
        await flush_task
        # Let the newly scheduled timer fire
        await asyncio.sleep(0.5)
        # Both turns must have been extracted (possibly across two flush calls).
        extracted_texts = [
            ex["text"]
            for c in process_memory_mock.await_args_list
            for ex in c.args[2]
        ]
        assert "turn 1" in extracted_texts
        assert "turn 2" in extracted_texts

    @pytest.mark.asyncio
    async def test_cancellation_race_no_double_flush(self, debouncer, process_memory_mock):
        """A superseded in-flight timer must not double-flush after a direct flush."""
        await debouncer.schedule(USER, CONV, "turn 1", "r1")
        # Flush directly (explicit), which bumps the generation
        await debouncer.flush(USER, CONV)
        # Wait for any stale timer to potentially wake up
        await asyncio.sleep(0.5)
        # process_memory called exactly once
        assert process_memory_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_explicit_intent_during_flush_does_not_race(self, debouncer, process_memory_mock):
        """Explicit intent arriving while a flush is running should queue the new exchange
        into a fresh burst and flush it."""
        await debouncer.schedule(USER, CONV, "turn 1", "r1")
        # Start a flush
        t1 = asyncio.create_task(debouncer.flush(USER, CONV))
        await t1
        # Now explicit intent after flush cleared state
        await debouncer.schedule(USER, CONV, "תזכרי ש X", "r2", explicit_intent=True)
        # Both should have been processed
        assert process_memory_mock.await_count == 2


# --- Redis lifecycle ---


class TestRedisLifecycle:
    @pytest.mark.asyncio
    async def test_redis_persistence_write_and_read(self, debouncer, redis_mock):
        await debouncer.schedule(USER, CONV, "turn 1", "r1")
        raw = await redis_mock.get(f"jane:pending_extraction:{_key()}")
        assert raw is not None
        data = json.loads(raw)
        assert len(data) == 1
        assert data[0]["text"] == "turn 1"
        # A3: conv_id propagated into exchange dict for OpApplier session_id.
        assert data[0]["conv_id"] == CONV
        debouncer._timers[_key()].cancel()

    @pytest.mark.asyncio
    async def test_restore_from_redis_flushes_immediately(
        self, hass_mock, redis_mock, client_mock, fast_timers, process_memory_mock
    ):
        # Pre-seed Redis with a pending queue
        raw = json.dumps(
            [{"user": USER, "text": "orphan turn", "response": "r", "ts": 0}],
            ensure_ascii=False,
        )
        await redis_mock.set(f"jane:pending_extraction:{_key()}", raw)

        deb = ExtractionDebouncer(hass_mock, redis_mock, lambda: client_mock, ENTRY_ID)
        count = await deb.restore_from_redis()

        assert count == 1
        assert process_memory_mock.await_count == 1
        assert process_memory_mock.await_args.args[2][0]["text"] == "orphan turn"
        # Redis key should be cleared
        assert await redis_mock.get(f"jane:pending_extraction:{_key()}") is None

    @pytest.mark.asyncio
    async def test_restore_ignores_malformed_json(
        self, hass_mock, redis_mock, client_mock, fast_timers, process_memory_mock
    ):
        await redis_mock.set(f"jane:pending_extraction:{_key()}", "{not json}")
        deb = ExtractionDebouncer(hass_mock, redis_mock, lambda: client_mock, ENTRY_ID)
        count = await deb.restore_from_redis()
        assert count == 0
        assert process_memory_mock.await_count == 0
        # Malformed key cleaned up
        assert await redis_mock.get(f"jane:pending_extraction:{_key()}") is None

    @pytest.mark.asyncio
    async def test_different_entry_ids_isolated(
        self, hass_mock, redis_mock, client_mock, fast_timers, process_memory_mock
    ):
        raw = json.dumps([{"user": USER, "text": "other entry", "response": "r", "ts": 0}])
        await redis_mock.set(f"jane:pending_extraction:OTHER_ENTRY:{USER}:{CONV}", raw)

        deb = ExtractionDebouncer(hass_mock, redis_mock, lambda: client_mock, ENTRY_ID)
        count = await deb.restore_from_redis()
        # Our debouncer only scans its own namespace
        assert count == 0
        assert process_memory_mock.await_count == 0
        # Other entry's key untouched
        other = await redis_mock.get(f"jane:pending_extraction:OTHER_ENTRY:{USER}:{CONV}")
        assert other is not None


# --- Lifecycle ---


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_flush_all_drains_every_queue(self, debouncer, process_memory_mock):
        await debouncer.schedule(USER, "conv-1", "t1", "r1")
        await debouncer.schedule(USER, "conv-2", "t2", "r2")
        await debouncer.schedule("Efrat", "conv-3", "t3", "r3")
        await debouncer.flush_all()
        assert process_memory_mock.await_count == 3
        assert not debouncer._pending

    @pytest.mark.asyncio
    async def test_process_memory_error_requeues_in_memory(self, debouncer, process_memory_mock):
        """On flush failure, exchanges are restored to _pending so the next flush sees them."""
        process_memory_mock.side_effect = RuntimeError("boom")
        await debouncer.schedule(USER, CONV, "t1", "r1")
        await debouncer.flush(USER, CONV)
        # Attempted process_memory despite error
        assert process_memory_mock.await_count == 1
        # Re-queued in-memory (not just Redis) so a subsequent flush retries them.
        assert _key() in debouncer._pending
        assert debouncer._pending[_key()][0]["text"] == "t1"

    @pytest.mark.asyncio
    async def test_flush_requeues_to_redis_on_process_memory_error(
        self, debouncer, process_memory_mock, redis_mock
    ):
        """A2 §2.1: failed extraction re-persists to Redis for retry on next burst/startup."""
        process_memory_mock.side_effect = RuntimeError("Gemini 503")
        await debouncer.schedule(USER, CONV, "important fact", "ok")
        await debouncer.flush(USER, CONV)
        # Redis still has the queue — next restore_from_redis will retry.
        raw = await redis_mock.get(f"jane:pending_extraction:{_key()}")
        assert raw is not None
        data = json.loads(raw)
        assert len(data) == 1
        assert data[0]["text"] == "important fact"

    @pytest.mark.asyncio
    async def test_requeue_merges_with_concurrent_schedule(
        self, debouncer, process_memory_mock, redis_mock
    ):
        """PR #43 review: re-queue must merge with new exchanges scheduled during the failed flush."""

        async def _slow_failing_extraction(*args, **kwargs):
            # Simulate Gemini taking time, during which a new turn arrives.
            await asyncio.sleep(0.05)
            raise RuntimeError("Gemini 503")

        process_memory_mock.side_effect = _slow_failing_extraction

        await debouncer.schedule(USER, CONV, "old fact", "r1")
        flush_task = asyncio.create_task(debouncer.flush(USER, CONV))
        # Let flush enter and pop the queue, then start the slow process_memory.
        await asyncio.sleep(0.01)
        # Concurrent schedule arrives while process_memory is in flight.
        await debouncer.schedule(USER, CONV, "new fact", "r2")
        await flush_task

        # After re-queue: both old (re-persisted) and new (concurrently scheduled) survive.
        raw = await redis_mock.get(f"jane:pending_extraction:{_key()}")
        assert raw is not None
        texts = [ex["text"] for ex in json.loads(raw)]
        assert "old fact" in texts
        assert "new fact" in texts
