"""Extraction Debouncer — coalesce rapid turns into a single memory-extraction burst.

Design (Memory Optimization A1):
- Each conversation is keyed by (entry_id, user_name, conv_id).
- Calls to schedule() append an exchange to an in-memory queue and (re)arm a
  timer. The timer is deadline-based: once a burst starts, the hard cap is
  EXTRACTION_BURST_CAP_SECONDS, so extraction always fires within that window
  no matter how chatty the user is.
- Concurrency is protected by a per-key asyncio.Lock plus a generation counter
  that invalidates in-flight timers superseded by newer schedules.
- Pending queues are persisted to Redis every turn, so HA restart mid-burst
  does not lose data — restore_from_redis() flushes them immediately on
  startup (the user has already finished speaking by definition).
- Silent turns are PROSPECTIVE: they do not queue this exchange, but do not
  drop already-queued exchanges from the same burst.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from ..const import (
    EXTRACTION_BURST_CAP_SECONDS,
    EXTRACTION_DEFAULT_DELAY_SECONDS,
    EXTRACTION_MIN_DELAY_SECONDS,
    EXTRACTION_PENDING_REDIS_PREFIX,
    EXTRACTION_PENDING_TTL_SECONDS,
)
from .extraction import process_memory

_LOGGER = logging.getLogger(__name__)


class ExtractionDebouncer:
    """Coalesce per-turn extractions into grouped bursts."""

    def __init__(self, hass, redis, client_getter, entry_id: str):
        self._hass = hass
        self._redis = redis
        self._client_getter = client_getter
        self._entry_id = entry_id

        self._pending: dict[str, list[dict]] = {}
        self._timers: dict[str, asyncio.Task] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._generation: dict[str, int] = {}
        self._burst_deadline: dict[str, float] = {}

    def _key(self, user_name: str, conv_id: str) -> str:
        return f"{self._entry_id}:{user_name}:{conv_id}"

    def _parse_key(self, key: str) -> tuple[str, str] | None:
        """Reverse _key() defensively via rsplit — safe if user_name contains colons."""
        try:
            prefix_user, conv_id = key.rsplit(":", 1)
            _, user_name = prefix_user.rsplit(":", 1)
        except ValueError:
            return None
        return user_name, conv_id

    def _redis_key(self, key: str) -> str:
        return f"{EXTRACTION_PENDING_REDIS_PREFIX}:{key}"

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def schedule(
        self,
        user_name: str,
        conv_id: str,
        user_text: str,
        jane_response: str,
        is_silent: bool = False,
        explicit_intent: bool = False,
    ) -> None:
        """Queue an exchange and arm a debounce timer.

        silent semantics: PROSPECTIVE. A silent turn does not queue this
        exchange, but does not clear already-pending exchanges in the burst.
        explicit_intent: flush immediately after appending (bypass timer).
        """
        if is_silent:
            return

        key = self._key(user_name, conv_id)
        now = time.time()
        exchange = {"user": user_name, "text": user_text, "response": jane_response, "ts": now}

        async with self._lock(key):
            self._pending.setdefault(key, []).append(exchange)
            deadline = self._burst_deadline.setdefault(key, now + EXTRACTION_BURST_CAP_SECONDS)
            pending_count = len(self._pending[key])
            await self._persist(key, self._pending[key])

            if explicit_intent:
                # Bypass timer: invalidate any in-flight, then flush below.
                self._generation[key] = self._generation.get(key, 0) + 1
                timer = self._timers.pop(key, None)
                if timer:
                    timer.cancel()
                _LOGGER.info("Explicit intent — immediate flush for %s", key)
                should_flush_now = True
            else:
                should_flush_now = False
                self._generation[key] = self._generation.get(key, 0) + 1
                generation = self._generation[key]
                old_timer = self._timers.pop(key, None)
                if old_timer:
                    old_timer.cancel()
                delay = max(
                    EXTRACTION_MIN_DELAY_SECONDS,
                    min(EXTRACTION_DEFAULT_DELAY_SECONDS, deadline - now),
                )
                self._timers[key] = asyncio.create_task(self._timer_fire(user_name, conv_id, delay, generation))
                burst_age = int(now - (deadline - EXTRACTION_BURST_CAP_SECONDS))
                _LOGGER.info(
                    "Scheduled extraction for %s in %ds (pending=%d, burst_age=%ds)",
                    key,
                    int(delay),
                    pending_count,
                    burst_age,
                )

        if should_flush_now:
            await self.flush(user_name, conv_id)

    async def _timer_fire(self, user_name: str, conv_id: str, delay: float, generation: int) -> None:
        """Timer coroutine — sleeps then flushes if still the active generation."""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        key = self._key(user_name, conv_id)
        if self._generation.get(key, 0) != generation:
            return  # superseded
        await self.flush(user_name, conv_id)

    async def flush(self, user_name: str, conv_id: str) -> None:
        """Snapshot pending exchanges, clear state, then run extraction outside the lock."""
        key = self._key(user_name, conv_id)
        async with self._lock(key):
            exchanges = self._pending.pop(key, [])
            timer = self._timers.pop(key, None)
            # Bump generation so any in-flight timer returns early.
            self._generation[key] = self._generation.get(key, 0) + 1
            self._burst_deadline.pop(key, None)
            await self._delete_persisted(key)

        if timer and not timer.done():
            timer.cancel()

        if not exchanges:
            return

        _LOGGER.info("Flushing %d exchanges for %s", len(exchanges), key)
        client = self._client_getter()
        if client is None:
            _LOGGER.warning(
                "Extraction flush skipped for %s: no Gemini client. %d exchanges lost.",
                key,
                len(exchanges),
            )
            return
        # TODO(A2): retry failed exchanges instead of dropping them.
        for i, ex in enumerate(exchanges):
            try:
                await process_memory(client, ex["user"], ex["text"], ex["response"], "tool", self._hass)
            except Exception as e:
                _LOGGER.warning(
                    "Extraction flush failed for %s [%d/%d] (text=%s…): %s",
                    key,
                    i + 1,
                    len(exchanges),
                    ex["text"][:40],
                    e,
                )

    async def flush_all(self) -> None:
        """Drain every queue — for HA unload / shutdown."""
        keys = list(self._pending.keys())
        for key in keys:
            parsed = self._parse_key(key)
            if parsed is None:
                continue
            user_name, conv_id = parsed
            try:
                await self.flush(user_name, conv_id)
            except Exception as e:
                _LOGGER.warning("flush_all: failed for %s: %s", key, e)

    async def restore_from_redis(self) -> int:
        """On startup, flush any pending queues left over from a prior run."""
        if self._redis is None:
            return 0
        pattern = f"{EXTRACTION_PENDING_REDIS_PREFIX}:{self._entry_id}:*"
        count = 0
        try:
            async with asyncio.timeout(10):
                async for raw_key in self._redis.scan_iter(match=pattern, count=100):
                    key_full = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
                    data = await self._redis.get(key_full)
                    if not data:
                        continue
                    if isinstance(data, bytes):
                        data = data.decode()
                    try:
                        exchanges = json.loads(data)
                    except (json.JSONDecodeError, TypeError):
                        await self._redis.delete(key_full)
                        continue
                    inner_key = key_full[len(EXTRACTION_PENDING_REDIS_PREFIX) + 1 :]
                    parsed = self._parse_key(inner_key)
                    if parsed is None:
                        await self._redis.delete(key_full)
                        continue
                    user_name, conv_id = parsed
                    self._pending[inner_key] = exchanges
                    self._burst_deadline[inner_key] = time.time()
                    await self.flush(user_name, conv_id)
                    count += 1
        except TimeoutError:
            _LOGGER.warning("restore_from_redis timed out after 10s (restored %d so far)", count)
            return count
        except Exception as e:
            _LOGGER.warning("restore_from_redis failed: %s", e)
            return count
        if count:
            _LOGGER.info("Restored + flushed %d pending extractions on startup", count)
        return count

    async def _persist(self, key: str, exchanges: list[dict]) -> None:
        """Write pending queue to Redis with TTL."""
        if self._redis is None:
            return
        try:
            await self._redis.set(
                self._redis_key(key),
                json.dumps(exchanges, ensure_ascii=False),
                ex=EXTRACTION_PENDING_TTL_SECONDS,
            )
        except Exception as e:
            _LOGGER.debug("Redis persist failed for %s: %s", key, e)

    async def _delete_persisted(self, key: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.delete(self._redis_key(key))
        except Exception as e:
            _LOGGER.debug("Redis delete failed for %s: %s", key, e)
