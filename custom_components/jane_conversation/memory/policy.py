"""Policy Store — per-user access control and household rules (S1.5).

Stores policies (role, quiet hours, confirmation thresholds) per person.
S1.5 scope: store + inject into Gemini context. Hard enforcement deferred to Phase 3.
"""

import logging
from datetime import datetime

from ..const import SENSITIVE_ACTIONS

_LOGGER = logging.getLogger(__name__)


class PolicyStore:
    """Typed access to the policies table in PostgreSQL."""

    def __init__(self, pool):
        self._pool = pool

    async def save_policy(self, person_name: str, key: str, value: str) -> None:
        """Upsert a policy for a person."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO policies (person_name, key, value)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (person_name, key) DO UPDATE SET
                       value = EXCLUDED.value,
                       updated_at = NOW()""",
                person_name,
                key,
                value,
            )

    async def load_policies(self, person_name: str) -> dict[str, str]:
        """Load all policies for a person as a key→value dict."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT key, value FROM policies WHERE person_name = $1",
                person_name,
            )
            return {r["key"]: r["value"] for r in rows}

    async def check_permission(self, person_name: str, action: str) -> str | None:
        """Check if person is allowed to perform action.

        Returns None if allowed, reason string if denied.
        """
        policies = await self.load_policies(person_name)
        role = policies.get("role", "admin")

        # Quiet hours check (for TTS/announcements)
        quiet_start = policies.get("quiet_hours_start")
        quiet_end = policies.get("quiet_hours_end")
        if quiet_start and quiet_end and action == "tts":
            now = datetime.now().strftime("%H:%M")
            if quiet_start <= quiet_end:
                # Same-day range (e.g., 14:00–16:00)
                in_quiet = quiet_start <= now < quiet_end
            else:
                # Overnight range (e.g., 23:00–07:00)
                in_quiet = now >= quiet_start or now < quiet_end
            if in_quiet:
                return f"שעות שקט: {quiet_start}–{quiet_end}"

        # Role-based check for sensitive actions
        if role == "child" and action in SENSITIVE_ACTIONS:
            threshold = policies.get("confirmation_threshold", "sensitive")
            if threshold != "none":
                return "פעולה זו דורשת אישור מהורה"

        return None  # allowed

    async def build_policy_context(self, person_name: str) -> str:
        """Format policies as concise text for Gemini system_instruction."""
        policies = await self.load_policies(person_name)
        if not policies:
            return ""

        lines = []
        role = policies.get("role")
        if role:
            lines.append(f"Role: {role}")

        quiet_start = policies.get("quiet_hours_start")
        quiet_end = policies.get("quiet_hours_end")
        if quiet_start and quiet_end:
            lines.append(f"Quiet hours: {quiet_start}–{quiet_end}")

        threshold = policies.get("confirmation_threshold")
        if threshold:
            lines.append(f"Confirmation: {threshold}")

        tts = policies.get("tts_enabled")
        if tts == "false":
            lines.append("TTS disabled")

        return "\n".join(lines)

    async def seed_defaults(self, persons: list[dict]) -> int:
        """Seed default admin policy for persons that don't have one yet."""
        count = 0
        for person in persons:
            name = person.get("name", "")
            if not name:
                continue
            existing = await self.load_policies(name)
            if "role" not in existing:
                role = "child" if person.get("role") == "child" else "admin"
                await self.save_policy(name, "role", role)
                count += 1
                _LOGGER.debug("Seeded default policy for %s: role=%s", name, role)
        return count
