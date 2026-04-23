"""Memory operation models + JSON parsing.

A3 of Memory Optimization. Gemini emits discrete ops (ADD/UPDATE/DELETE/NOOP) that
are validated here, then applied via OpApplier (in ops_applier.py).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

OP_TYPES = ("ADD", "UPDATE", "DELETE", "NOOP")
TARGET_TABLES = ("memory_entries", "preferences", "persons", "events")


@dataclass
class MemoryOp:
    """A single memory operation emitted by the extractor."""

    op: str                              # ADD | UPDATE | DELETE | NOOP
    target_table: str | None             # None for NOOP
    target_key: dict                     # e.g. {"person": "Yair", "key": "beverage_preference"}
    payload: dict                        # ADD/UPDATE only
    reason: str                          # required for non-NOOP
    confidence: float = 1.0              # 0.0-1.0 self-reported

    def idempotency_hash(self, session_id: str) -> str:
        """Stable hash of (session_id, op, target_table, target_key) for replay detection."""
        blob = json.dumps(
            {"sid": session_id, "op": self.op, "tbl": self.target_table, "key": self.target_key},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.md5(blob.encode("utf-8")).hexdigest()


@dataclass
class OpResult:
    """Aggregated result of OpApplier.apply_all."""

    added: int = 0
    updated: int = 0
    deleted: int = 0
    nooped: int = 0
    skipped: int = 0          # duplicate / invalid after parse
    failed: int = 0

    def summary(self) -> str:
        return (
            f"{self.added} ADD, {self.updated} UPDATE, {self.deleted} DELETE, "
            f"{self.nooped} NOOP, {self.skipped} skipped, {self.failed} failed"
        )


class OpValidationError(ValueError):
    """Raised when an op fails schema validation. Caller logs + drops."""


def parse_ops_json(raw: Any) -> list[MemoryOp]:
    """Parse Gemini's JSON response into a list of MemoryOp.

    Accepts `{"ops": [...]}` shape, or a bare list as a fallback. Invalid ops are
    dropped with a warning — the rest are returned so a malformed single op does
    not abort the batch.
    """
    if isinstance(raw, dict) and "ops" in raw:
        items = raw["ops"] or []
    elif isinstance(raw, list):
        items = raw
    else:
        _LOGGER.warning("parse_ops_json: unexpected root type %s", type(raw).__name__)
        return []

    ops: list[MemoryOp] = []
    for i, item in enumerate(items):
        try:
            ops.append(_parse_one(item))
        except OpValidationError as e:
            _LOGGER.warning("parse_ops_json: dropped op %d — %s", i, e)
    return ops


def _parse_one(item: Any) -> MemoryOp:
    if not isinstance(item, dict):
        raise OpValidationError(f"op must be an object, got {type(item).__name__}")

    op = item.get("op")
    if op not in OP_TYPES:
        raise OpValidationError(f"op must be one of {OP_TYPES}, got {op!r}")

    reason = item.get("reason", "")
    if op != "NOOP" and not reason:
        raise OpValidationError(f"reason required for {op}")

    confidence = float(item.get("confidence", 1.0)) if op != "NOOP" else 1.0
    if not (0.0 <= confidence <= 1.0):
        raise OpValidationError(f"confidence out of range: {confidence}")

    if op == "NOOP":
        return MemoryOp(op=op, target_table=None, target_key={}, payload={}, reason=reason, confidence=1.0)

    target = item.get("target") or {}
    table = target.get("table")
    if table not in TARGET_TABLES:
        raise OpValidationError(f"unsupported target.table={table!r}")

    key = target.get("key") or {}
    if not isinstance(key, dict):
        raise OpValidationError(f"target.key must be a dict, got {type(key).__name__}")

    payload = item.get("payload") or {}
    if op in ("ADD", "UPDATE") and not isinstance(payload, dict):
        raise OpValidationError(f"payload must be a dict for {op}")

    if table == "events" and op != "ADD":
        raise OpValidationError(f"events table only supports ADD, got {op}")
    if table == "persons" and op == "DELETE":
        raise OpValidationError("DELETE on persons not supported in A3")

    return MemoryOp(
        op=op,
        target_table=table,
        target_key=key,
        payload=payload if op != "DELETE" else {},
        reason=reason,
        confidence=confidence,
    )
