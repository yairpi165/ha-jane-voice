"""OpApplier — dispatches MemoryOp writes to the right store and logs each to memory_ops.

Split from ops.py to keep both files under the 300-line project cap. See ops.py for the
MemoryOp dataclass, parse_ops_json, and validation.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .ops import MemoryOp, OpResult

_LOGGER = logging.getLogger(__name__)


@dataclass
class OpApplier:
    """Applies a batch of MemoryOps: writes target row(s) + writes audit row(s).

    Not thread-safe within a single instance; construct per batch.

    The two optional callables (default ``None`` = no-op) allow the caller to plug
    in B2 (JANE-81) behavior without making OpApplier import the Redis client:

    - ``recently_removed_check(person, normalized_key) -> bool``: when True for
      an ADD on ``preferences``, the op is downgraded to NOOP with reason
      ``recently_removed_guard``.
    - ``on_pref_add()``: invoked after every successful ADD on ``preferences``
      (used for the threshold counter).
    """

    backend: Any
    structured: Any
    pg_pool: Any
    recently_removed_check: Callable[[str, str], Awaitable[bool]] | None = None
    on_pref_add: Callable[[], Awaitable[None]] | None = None
    _raw_logged_for_session: set[str] = field(default_factory=set)
    _person_cache: list[dict] = field(default_factory=list)

    async def _canonical(self, name: str, fallback: str) -> str:
        """Resolve a name to its canonical persons-table form, batch-cached.

        Thin wrapper that delegates to ``StructuredMemoryStore.canonical_person``
        (the single source of truth for person canonicalization) while keeping
        ``self._person_cache`` populated lazily once per OpApplier instance to
        avoid N round-trips on a multi-op batch.
        """
        if not name:
            return fallback
        if not self._person_cache:
            try:
                self._person_cache = await self.structured.load_persons()
            except Exception:
                self._person_cache = []
        return await self.structured.canonical_person(
            name, fallback, persons_cache=self._person_cache
        )

    async def apply_all(
        self,
        ops: list[MemoryOp],
        user_name: str,
        session_id: str,
        memory_snapshot: dict | None = None,
        raw_response: str | None = None,
    ) -> OpResult:
        result = OpResult()
        snap = memory_snapshot or {}
        for op in ops:
            try:
                applied = await self._apply_one(op, user_name, session_id, snap, raw_response)
                if not applied:
                    result.skipped += 1
                elif op.op == "ADD":
                    result.added += 1
                elif op.op == "UPDATE":
                    result.updated += 1
                elif op.op == "DELETE":
                    result.deleted += 1
                elif op.op == "NOOP":
                    result.nooped += 1
            except Exception as e:
                _LOGGER.warning(
                    "OpApplier failed op=%s table=%s key=%s: %s",
                    op.op,
                    op.target_table,
                    op.target_key,
                    e,
                )
                result.failed += 1
        return result

    async def _apply_one(
        self,
        op: MemoryOp,
        user_name: str,
        session_id: str,
        snapshot: dict,
        raw_response: str | None,
    ) -> bool:
        """Returns True if the op was applied/logged, False if skipped as duplicate."""
        op_hash = op.idempotency_hash(session_id)
        if await self._already_applied(op_hash):
            _LOGGER.debug("OpApplier: skip replay op=%s key=%s", op.op, op.target_key)
            return False

        # B2 (JANE-81): if the user just asked to forget Alice:coffee and the
        # extractor wants to re-learn it, downgrade to NOOP. The check callable
        # encapsulates the Redis ZSET lookup so OpApplier stays PG-only.
        if (
            op.op == "ADD"
            and op.target_table == "preferences"
            and self.recently_removed_check is not None
        ):
            from .structured import _normalize_pref_key

            person = await self._canonical(op.target_key.get("person", ""), user_name)
            norm_key = _normalize_pref_key(op.target_key.get("key", ""))
            try:
                if await self.recently_removed_check(person, norm_key):
                    _LOGGER.info(
                        "Recently-removed guard: skipping ADD %s:%s (downgrading to NOOP)",
                        person,
                        norm_key,
                    )
                    op = MemoryOp(
                        op="NOOP",
                        target_table=op.target_table,
                        target_key=op.target_key,
                        payload=op.payload,
                        reason="recently_removed_guard",
                        confidence=op.confidence,
                    )
            except Exception as e:
                _LOGGER.debug("recently_removed_check failed (non-fatal): %s", e)

        before_state: dict | None = None
        if op.op in ("UPDATE", "DELETE"):
            before_state = await self._capture_before_state(op, user_name, snapshot)

        if op.op != "NOOP":
            await self._dispatch_write(op, user_name)

        raw_to_store = raw_response if session_id not in self._raw_logged_for_session else None
        self._raw_logged_for_session.add(session_id)

        await self._log_op(op, user_name, session_id, before_state, op_hash, raw_to_store)
        return True

    async def _already_applied(self, op_hash: str) -> bool:
        async with self.pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM memory_ops WHERE op_hash = $1 LIMIT 1",
                op_hash,
            )
            return row is not None

    async def _capture_before_state(
        self, op: MemoryOp, user_name: str, snapshot: dict
    ) -> dict | None:
        table = op.target_table
        key = op.target_key
        if table == "memory_entries":
            cat = key.get("category")
            if cat and cat in snapshot:
                return {"content": snapshot[cat]}
            if cat:
                content = await self.backend.load(cat, key.get("user_name") or user_name)
                return {"content": content} if content else None
            return None
        if table == "preferences":
            person = await self._canonical(key.get("person", ""), user_name)
            return await self.structured.load_preference(person, key.get("key"))
        if table == "persons":
            person = await self._canonical(key.get("name", ""), user_name)
            row = await self.structured.load_person(person)
            if row and isinstance(row.get("birth_date"), _dt.date):
                row = {**row, "birth_date": row["birth_date"].isoformat()}
            return row
        return None

    async def _dispatch_write(self, op: MemoryOp, user_name: str) -> None:
        table = op.target_table
        key = op.target_key
        payload = op.payload

        if table == "memory_entries":
            cat = key.get("category")
            user_key = key.get("user_name") or (user_name if cat == "user" else None)
            if op.op == "DELETE":
                await self.backend.delete_category(cat, user_key)
            else:
                await self.backend.save(cat, payload.get("content", ""), user_key)

        elif table == "preferences":
            person = await self._canonical(key.get("person", ""), user_name)
            if op.op == "DELETE":
                await self.structured.delete_preference(person, key.get("key"))
            else:
                await self.structured.save_preference(
                    person_name=person,
                    key=key.get("key"),
                    value=payload.get("value", ""),
                    inferred=bool(payload.get("inferred", False)),
                    confidence=payload.get("confidence"),
                    source="extraction_ops",
                )
                # B2 (JANE-81): increment threshold counter on actual ADDs.
                # UPDATEs reuse the same upsert path but shouldn't bump the
                # "new prefs since consolidation" counter.
                if op.op == "ADD" and self.on_pref_add is not None:
                    try:
                        await self.on_pref_add()
                    except Exception as e:
                        _LOGGER.debug("on_pref_add callback failed (non-fatal): %s", e)

        elif table == "persons":
            person = await self._canonical(key.get("name", ""), user_name)
            bd = payload.get("birth_date")
            bd_parsed = _parse_date(bd) if isinstance(bd, str) else bd
            await self.structured.save_person(
                name=person,
                role=payload.get("role"),
                birth_date=bd_parsed,
                metadata=payload.get("metadata"),
            )

        elif table == "events":
            await self.backend.append_event(
                event_type=key.get("event_type", "correction"),
                user_name=user_name,
                description=payload.get("description", ""),
                metadata=payload.get("metadata"),
            )

    async def _log_op(
        self,
        op: MemoryOp,
        user_name: str,
        session_id: str,
        before_state: dict | None,
        op_hash: str,
        raw_response: str | None,
    ) -> None:
        try:
            async with self.pg_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO memory_ops
                       (op, target_table, target_key, payload, before_state, reason,
                        confidence, user_name, session_id, op_hash, raw_response)
                       VALUES ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6, $7, $8, $9, $10, $11)""",
                    op.op,
                    op.target_table,
                    json.dumps(op.target_key, ensure_ascii=False, default=_json_default),
                    json.dumps(op.payload, ensure_ascii=False, default=_json_default),
                    json.dumps(before_state, ensure_ascii=False, default=_json_default)
                    if before_state
                    else None,
                    op.reason,
                    op.confidence,
                    user_name,
                    session_id,
                    op_hash,
                    raw_response,
                )
        except Exception as e:
            _LOGGER.warning("OpApplier: failed to log memory_ops row (op=%s): %s", op.op, e)


def _parse_date(s: str):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _json_default(obj):
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    raise TypeError(f"Unserializable: {type(obj).__name__}")
