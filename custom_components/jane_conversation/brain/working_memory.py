"""Working Memory — real-time household awareness backed by Redis."""

import json
import logging
import textwrap
import time
from collections.abc import Callable

from homeassistant.core import Event, HomeAssistant

from ..const import (
    CONF_SKIP_KEYWORDS,
    CONF_TRACKED_DOMAINS,
    DEFAULT_SKIP_KEYWORDS,
    DEFAULT_TRACKED_DOMAINS,
    normalize_person_state,
    parse_csv,
)

_LOGGER = logging.getLogger(__name__)

CHANGES_TTL = 3600  # Keep changes for 1 hour
CONTEXT_CACHE_TTL = 30  # Cache rendered context for 30 seconds
DEBOUNCE_SECONDS = 60
OFF_STATES = {"off", "unavailable", "idle", "unknown", "standby"}


class WorkingMemory:
    """Real-time household awareness backed by Redis."""

    def __init__(self, redis_client, hass: HomeAssistant, episodic=None, config_entry=None):
        self._redis = redis_client
        self._hass = hass
        self._episodic = episodic
        raw = {**(config_entry.data or {}), **(config_entry.options or {})} if config_entry else {}
        self._tracked = parse_csv(raw.get(CONF_TRACKED_DOMAINS, DEFAULT_TRACKED_DOMAINS))
        self._tracked.add("person")  # always track presence
        self._skip = parse_csv(raw.get(CONF_SKIP_KEYWORDS, DEFAULT_SKIP_KEYWORDS))

    async def start_listening(self) -> Callable:
        """Start listening to HA state changes and populate initial snapshot."""
        await self._redis.delete("jane:active", "jane:context_cache")
        await self._snapshot_current_state()
        unsub = self._hass.bus.async_listen("state_changed", self._on_state_changed)
        _LOGGER.info("Working memory: listening to state changes")
        return unsub

    async def _snapshot_current_state(self) -> None:
        """Populate Redis with current home state on startup."""
        try:
            pipe = self._redis.pipeline()

            for state in self._hass.states.async_all("person"):
                name = state.attributes.get("friendly_name", state.entity_id)
                status = normalize_person_state(state.state)
                pipe.hset("jane:presence", name, status)
                pipe.hset("jane:presence:since", name, str(time.time()))

            for state in self._hass.states.async_all():
                if state.domain not in self._tracked or state.domain == "person":
                    continue
                if any(kw in state.entity_id.lower() for kw in self._skip):
                    continue
                if state.state not in OFF_STATES:
                    pipe.hset("jane:active", state.entity_id, describe_entity(state))

            await pipe.execute()
            _LOGGER.info("Working memory: initial snapshot loaded")
        except Exception:
            _LOGGER.warning("Working memory: failed to load initial snapshot", exc_info=True)

    async def _on_state_changed(self, event: Event) -> None:
        """Handle HA state_changed event — update Redis."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        domain = new_state.domain
        if domain not in self._tracked:
            return

        entity_id = new_state.entity_id
        if any(kw in entity_id.lower() for kw in self._skip):
            return

        try:
            if domain == "person":
                await self._update_presence(new_state)
            else:
                await self._update_active(new_state)

            await self._record_change(event)
            await self._redis.delete("jane:context_cache")
        except Exception:
            _LOGGER.debug("Working memory: Redis write failed for %s", entity_id, exc_info=True)

        await self._persist_to_pg(event)

    async def _update_presence(self, state) -> None:
        """Update person presence in Redis."""
        name = state.attributes.get("friendly_name", state.entity_id)
        status = normalize_person_state(state.state)
        pipe = self._redis.pipeline()
        pipe.hset("jane:presence", name, status)
        pipe.hset("jane:presence:since", name, str(time.time()))
        await pipe.execute()

    async def _update_active(self, state) -> None:
        """Update active device tracking in Redis with rich descriptions."""
        if state.state in OFF_STATES:
            await self._redis.hdel("jane:active", state.entity_id)
            return
        if state.domain == "cover":
            pos = state.attributes.get("current_position")
            if pos in (None, 0, 100):
                await self._redis.hdel("jane:active", state.entity_id)
                return
        await self._redis.hset("jane:active", state.entity_id, describe_entity(state))

    async def _record_change(self, event: Event) -> None:
        """Record state change with smart debounce (per entity+state)."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if old_state is None or new_state is None:
            return
        if old_state.state == new_state.state:
            return

        now = time.time()
        entity_id = new_state.entity_id

        # Smart debounce: suppress if entity reached this same state within window
        state_key = f"jane:change_ts:{entity_id}:{new_state.state}"
        last_ts = await self._redis.get(state_key)
        if last_ts and now - float(last_ts) < DEBOUNCE_SECONDS:
            return
        await self._redis.set(state_key, str(now), ex=DEBOUNCE_SECONDS)

        friendly = new_state.attributes.get("friendly_name", new_state.entity_id)
        entry = json.dumps(
            {"entity": friendly, "from": old_state.state, "to": new_state.state, "ts": now},
            ensure_ascii=False,
        )

        pipe = self._redis.pipeline()
        pipe.zadd("jane:changes", {entry: now})
        pipe.zremrangebyscore("jane:changes", "-inf", now - CHANGES_TTL)
        await pipe.execute()

    async def _persist_to_pg(self, event: Event) -> None:
        """Dual-write: persist state change to PG for long-term episodic memory."""
        if not self._episodic:
            return
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if not old_state or not new_state or old_state.state == new_state.state:
            return
        try:
            friendly = new_state.attributes.get("friendly_name", new_state.entity_id)
            await self._episodic.persist_state_change(
                entity_id=new_state.entity_id,
                friendly_name=friendly,
                old_state=old_state.state,
                new_state=new_state.state,
                timestamp=time.time(),
            )
        except Exception:
            _LOGGER.debug("Episodic persist failed for %s", new_state.entity_id, exc_info=True)

    async def get_context(self) -> str | None:
        """Build context string from Redis. Returns None if Redis empty/down."""
        cached = await self._redis.get("jane:context_cache")
        if cached:
            return cached

        parts = []

        presence = await self._redis.hgetall("jane:presence")
        since = await self._redis.hgetall("jane:presence:since")
        if presence:
            people = []
            for name, status in presence.items():
                ts = since.get(name)
                ago = _format_time_ago(float(ts)) if ts else ""
                suffix = f" ({ago})" if ago else ""
                people.append(f"{name}: {status}{suffix}")
            parts.append("People: " + ", ".join(people))

        weather = self._hass.states.get("weather.forecast_home")
        if weather:
            temp = weather.attributes.get("temperature", "?")
            parts.append(f"Weather: {weather.state}, {temp}°C")

        active = await self._redis.hgetall("jane:active")
        if active:
            descriptions = list(active.values())[:15]
            parts.append(f"Active: {', '.join(descriptions)}")

        now = time.time()
        changes_raw = await self._redis.zrangebyscore("jane:changes", now - 1800, "+inf")
        if changes_raw:
            change_lines = []
            for raw in changes_raw[-5:]:
                try:
                    c = json.loads(raw)
                    ago = _format_time_ago(c["ts"])
                    change_lines.append(f"{c['entity']}: {c['from']}→{c['to']} ({ago})")
                except (json.JSONDecodeError, KeyError):
                    continue
            if change_lines:
                parts.append("Recent: " + ", ".join(change_lines))

        if not parts:
            return None

        context = "\n".join(parts)
        await self._redis.set("jane:context_cache", context, ex=CONTEXT_CACHE_TTL)
        return context

    async def record_interaction(self, user_name: str, text: str, response: str) -> None:
        """Record last interaction metadata in Redis."""
        try:
            await self._redis.hset(
                "jane:last_interaction",
                mapping={
                    "user": user_name,
                    "text": text[:200],
                    "response": response[:200],
                    "timestamp": str(time.time()),
                },
            )
        except Exception:
            _LOGGER.debug("Working memory: failed to record interaction", exc_info=True)


def describe_entity(state) -> str:
    """Build a short human description with key attributes."""
    name = state.attributes.get("friendly_name", state.entity_id)
    domain = state.domain
    attrs = state.attributes

    if domain == "climate":
        mode = state.state
        temp = attrs.get("temperature", "")
        unit = attrs.get("temperature_unit", "°C")
        return f"{name} ({mode}, {temp}{unit})" if temp else f"{name} ({mode})"

    if domain == "media_player":
        title = attrs.get("media_title", "")
        if title:
            short = textwrap.shorten(title, width=30, placeholder="…")
            return f"{name} ({short})"
        source = attrs.get("app_name") or attrs.get("source", "")
        return f"{name} ({source})" if source else f"{name} ({state.state})"

    if domain == "cover":
        pos = attrs.get("current_position")
        return f"{name} ({pos}%)" if pos is not None else f"{name} ({state.state})"

    if domain in ("vacuum", "alarm_control_panel", "lock"):
        return f"{name} ({state.state})"

    if domain == "light" and state.state == "on":
        bright_pct = attrs.get("brightness_pct")
        if bright_pct is not None:
            return f"{name} ({bright_pct}%)"
        bright = attrs.get("brightness")
        if bright is not None:
            return f"{name} ({round(bright / 255 * 100)}%)"
        return name

    if domain == "fan":
        pct = attrs.get("percentage")
        return f"{name} ({pct}%)" if pct else f"{name} ({state.state})"

    if domain == "water_heater":
        current = attrs.get("current_temperature") or attrs.get("temperature")
        unit = attrs.get("temperature_unit", "°C")
        return f"{name} ({state.state}, {current}{unit})" if current is not None else f"{name} ({state.state})"

    if domain == "humidifier":
        current = attrs.get("current_humidity") or attrs.get("humidity")
        return f"{name} ({state.state}, {current}%)" if current is not None else f"{name} ({state.state})"

    if state.state != "on":
        return f"{name} ({state.state})"
    return name


def _format_time_ago(timestamp: float) -> str:
    """Format a timestamp as a human-readable relative time."""
    diff = time.time() - timestamp
    if diff < 60:
        return "just now"
    if diff < 3600:
        mins = int(diff / 60)
        return f"{mins} min ago"
    hours = int(diff / 3600)
    return f"{hours}h ago"
