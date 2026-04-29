"""Jane Voice Assistant — Custom conversation agent for Home Assistant."""

import importlib
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_FIREBASE_KEY_PATH,
    CONF_GEMINI_API_KEY,
    CONF_PG_HOST,
    CONF_REDIS_PASSWORD,
    CONF_REDIS_PORT,
    DEFAULT_REDIS_PORT,
    DOMAIN,
    JaneData,
)
from .memory import init_memory, rebuild_home_map

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CONVERSATION]


def _get_jane(hass: HomeAssistant) -> JaneData:
    """Get or create JaneData from hass.data."""
    hass.data.setdefault(DOMAIN, JaneData())
    return hass.data[DOMAIN]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Jane from a config entry."""
    jane = _get_jane(hass)
    jane.entry = entry

    # Determine storage backend
    backend = None
    pg_host = entry.options.get(CONF_PG_HOST) or entry.data.get(CONF_PG_HOST)

    if pg_host:
        backend = await _create_pg_backend(hass, entry)

    # Initialize Redis + Working Memory
    working_memory = None
    if pg_host:
        working_memory = await _create_working_memory(hass, entry, pg_host)

    # Initialize memory backend (PG required — Jane cannot function without it)
    if not backend:
        _LOGGER.error("PostgreSQL backend unavailable — Jane requires PG for memory. Check pg_host config.")
        return False
    init_memory(backend, hass)

    # Initialize Firebase backup if configured
    firebase_key = entry.data.get(CONF_FIREBASE_KEY_PATH)
    if firebase_key:
        from .memory.firebase import init_firebase

        ok = await hass.async_add_executor_job(init_firebase, firebase_key)
        if ok:
            _LOGGER.info("Firebase memory backup enabled")

    # Build home map on first setup
    from google import genai

    client = await hass.async_add_executor_job(lambda: genai.Client(api_key=entry.data[CONF_GEMINI_API_KEY]))
    _get_jane(hass).gemini_client = client  # Store for backfill + consolidation
    await rebuild_home_map(client, hass)

    # S3.1 (JANE-42) — auto-create the input_select.jane_household_mode helper
    # on first setup so the mode gate has a state to read. Idempotent.
    await _ensure_household_mode_helper(hass)

    # Extraction debouncer (A1) — coalesce per-turn memory extractions into bursts.
    from .memory.debouncer import ExtractionDebouncer

    jane.extraction_debouncer = ExtractionDebouncer(hass, jane.redis, lambda: jane.gemini_client, entry.entry_id)
    await jane.extraction_debouncer.restore_from_redis()

    async def _flush_debouncer_on_unload():
        await jane.extraction_debouncer.flush_all()

    entry.async_on_unload(_flush_debouncer_on_unload)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Register periodic tasks
    _register_periodic_tasks(hass, jane)

    redis_status = ", Redis working memory" if working_memory else ""
    _LOGGER.info("Jane Voice Assistant loaded (storage: %s%s)", "PostgreSQL" if pg_host else "files", redis_status)
    return True


def _register_periodic_tasks(hass: HomeAssistant, jane: JaneData) -> None:
    """Register all periodic background tasks."""
    from datetime import timedelta

    from homeassistant.helpers.event import async_track_time_interval

    # S1.3: Daily preference decay
    if jane.structured:

        async def _decay_task(_now):
            try:
                count = await jane.structured.decay_preferences()
                if count:
                    _LOGGER.info("Preference decay: %d preferences updated", count)
            except Exception as e:
                _LOGGER.debug("Preference decay failed: %s", e)

        jane.add_unsub(async_track_time_interval(hass, _decay_task, timedelta(hours=24)))

        # B1: Daily semantic preference dedup sweep + one-shot on startup
        async def _preference_dedup_task(_now):
            try:
                from .memory import preference_optimizer

                client = jane.gemini_client
                if not client or not jane.pg_pool:
                    return
                results = await preference_optimizer.sweep_all(
                    jane.pg_pool,
                    client,
                    hass,
                    jane.structured,
                )
                for person, r in results.items():
                    if r.before_count != r.after_count:
                        _LOGGER.info(
                            "Preference dedup for %s: %d → %d (%d auto, %d arbitrated)",
                            person,
                            r.before_count,
                            r.after_count,
                            r.auto_merges,
                            r.arbitrated_merges,
                        )
            except Exception as e:
                _LOGGER.debug("Preference dedup failed: %s", e)

        jane.add_unsub(async_track_time_interval(hass, _preference_dedup_task, timedelta(hours=24)))
        hass.async_create_task(_preference_dedup_task(None))

    # S1.4: Consolidation + daily summary + cleanup
    if jane.episodic:
        from .memory.consolidation import ConsolidationWorker

        worker = ConsolidationWorker(jane.episodic, hass)
        jane.consolidation = worker  # Set here (not in _create_pg_backend) — needs episodic + hass

        async def _consolidation_task(_now):
            try:
                count = await worker.consolidate_events()
                if count:
                    _LOGGER.info("Consolidation: %d episodes created", count)
            except Exception as e:
                _LOGGER.debug("Consolidation failed: %s", e)

        async def _daily_summary_task(_now):
            try:
                if await worker.generate_daily_summary():
                    _LOGGER.info("Daily summary created")
            except Exception as e:
                _LOGGER.debug("Daily summary failed: %s", e)

        async def _cleanup_task(_now):
            try:
                counts = await jane.episodic.cleanup_old_data()
                if any(counts.values()):
                    _LOGGER.info("Episodic cleanup: %s", counts)
            except Exception as e:
                _LOGGER.debug("Episodic cleanup failed: %s", e)

        jane.add_unsub(async_track_time_interval(hass, _consolidation_task, timedelta(hours=6)))
        jane.add_unsub(async_track_time_interval(hass, _daily_summary_task, timedelta(hours=24)))
        jane.add_unsub(async_track_time_interval(hass, _cleanup_task, timedelta(hours=24)))

        # S1.6: Backfill embeddings for existing episodes/summaries (background, non-blocking)
        async def _backfill_embeddings():
            try:
                from .memory.embeddings import backfill_embeddings

                client = jane.gemini_client
                if client and jane.pg_pool:
                    count = await backfill_embeddings(hass, jane.pg_pool, client)
                    if count:
                        _LOGGER.info("Embedding backfill: %d vectors generated", count)
            except Exception as e:
                _LOGGER.debug("Embedding backfill failed: %s", e)

        hass.async_create_task(_backfill_embeddings())

    # B4: daily corrections lifecycle sweep + manual trigger service (JANE-83).
    # Same daily cadence as the decay task; each row advances at most one state
    # per pass.
    if jane.pg_pool:
        from .memory.correction_lifecycle import sweep_corrections

        async def _corrections_sweep_task(_now=None):
            try:
                summary = await sweep_corrections(jane.pg_pool)
                if summary.any():
                    _LOGGER.info(
                        "Corrections lifecycle: applied=%d resolved=%d force_closed=%d deleted=%d",
                        summary.transitioned_to_applied,
                        summary.transitioned_to_resolved,
                        summary.force_closed,
                        summary.deleted,
                    )
            except Exception as e:
                _LOGGER.debug("Corrections sweep failed: %s", e)

        jane.add_unsub(async_track_time_interval(hass, _corrections_sweep_task, timedelta(hours=24)))

        async def _corrections_sweep_service(_call):
            await _corrections_sweep_task()

        hass.services.async_register(DOMAIN, "corrections_sweep_now", _corrections_sweep_service)

    # B5: weekly memory health report + manual trigger service (JANE-82).
    if jane.pg_pool:

        async def _health_report_task(_now=None):
            try:
                from .memory.health import collect_health_report, format_for_log, persist_health_report

                report = await collect_health_report(jane.pg_pool, days=7)
                await persist_health_report(jane.pg_pool, report)
                _LOGGER.info("Memory health: %s", format_for_log(report))
            except Exception as e:
                _LOGGER.debug("Memory health report failed: %s", e)

        jane.add_unsub(async_track_time_interval(hass, _health_report_task, timedelta(days=7)))

        async def _health_report_service(_call):
            await _health_report_task()

        hass.services.async_register(DOMAIN, "health_report_now", _health_report_service)

    # B2: weekly memory consolidation pass + threshold-trigger + manual services (JANE-81).
    if jane.pg_pool and jane.redis and jane.structured:
        from .memory.consolidation_pass import (
            RECENTLY_REMOVED_KEY,
            backfill_last_consolidation_ts,
            run_consolidation_pass,
            should_trigger_threshold,
        )
        from .memory.structured import _normalize_pref_key

        # Recovery from Redis flush: rehydrate LAST_CONSOLIDATION_KEY from PG if missing.
        hass.async_create_task(backfill_last_consolidation_ts(jane.pg_pool, jane.redis))

        async def _consolidation_pass_task(_now=None, *, trigger="weekly"):
            try:
                diff = await run_consolidation_pass(
                    jane.pg_pool,
                    jane.redis,
                    jane.structured,
                    hass,
                    jane.gemini_client,
                    trigger=trigger,
                )
                _LOGGER.info("Consolidation pass (%s): %s", trigger, diff.summary())
            except Exception as e:
                _LOGGER.debug("Consolidation pass failed: %s", e)

        async def _threshold_check_task(_now):
            try:
                if await should_trigger_threshold(jane.redis):
                    await _consolidation_pass_task(trigger="threshold")
            except Exception as e:
                _LOGGER.debug("Threshold check failed: %s", e)

        jane.add_unsub(async_track_time_interval(hass, _consolidation_pass_task, timedelta(days=7)))
        jane.add_unsub(async_track_time_interval(hass, _threshold_check_task, timedelta(hours=1)))

        async def _consolidate_service(_call):
            await _consolidation_pass_task(trigger="manual")

        hass.services.async_register(DOMAIN, "consolidate_memory_now", _consolidate_service)

        async def _clear_recently_removed_service(call):
            person = (call.data.get("person") or "").strip()
            key = (call.data.get("key") or "").strip()
            if not person or not key:
                _LOGGER.warning(
                    "clear_recently_removed: missing person or key (got person=%r, key=%r)",
                    person,
                    key,
                )
                return
            try:
                removed = await jane.redis.zrem(RECENTLY_REMOVED_KEY, f"{person}:{_normalize_pref_key(key)}")
                if removed:
                    _LOGGER.info("Cleared recently_removed guard for %s:%s", person, key)
                else:
                    _LOGGER.info("clear_recently_removed: no entry found for %s:%s", person, key)
            except Exception as e:
                _LOGGER.warning("clear_recently_removed failed: %s", e)

        hass.services.async_register(DOMAIN, "clear_recently_removed", _clear_recently_removed_service)


async def _create_working_memory(hass: HomeAssistant, entry: ConfigEntry, pg_host: str):
    """Create Redis client and start Working Memory listener."""
    try:
        aioredis = await hass.async_add_executor_job(importlib.import_module, "redis.asyncio")
        jane = _get_jane(hass)

        data = {**entry.data, **entry.options}
        redis_port = int(data.get(CONF_REDIS_PORT, DEFAULT_REDIS_PORT))
        redis_password = data.get(CONF_REDIS_PASSWORD, "") or None

        client = aioredis.Redis(
            host=pg_host,
            port=redis_port,
            password=redis_password,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        await client.ping()

        from .brain.working_memory import WorkingMemory

        wm = WorkingMemory(client, hass, episodic=jane.episodic, config_entry=entry)
        unsub = await wm.start_listening()

        jane.redis = client
        jane.working_memory = wm
        jane.add_unsub(unsub)

        _LOGGER.info("Redis connected: %s:%s", pg_host, redis_port)
        return wm

    except Exception as e:
        _LOGGER.warning("Redis unavailable, working memory disabled: %s", e)
        return None


async def _create_pg_backend(hass: HomeAssistant, entry: ConfigEntry):
    """Create PostgreSQL storage backend with connection pool."""
    from .const import CONF_PG_DATABASE, CONF_PG_PASSWORD, CONF_PG_PORT, CONF_PG_USER

    try:
        import asyncpg

        data = {**entry.data, **entry.options}
        pool = await asyncpg.create_pool(
            host=data.get(CONF_PG_HOST, "localhost"),
            port=int(data.get(CONF_PG_PORT, 5432)),
            database=data.get(CONF_PG_DATABASE, "jane"),
            user=data.get(CONF_PG_USER, "postgres"),
            password=data.get(CONF_PG_PASSWORD, ""),
            ssl="disable",
            min_size=2,
            max_size=5,
            timeout=5,
        )

        jane = _get_jane(hass)
        jane.pg_pool = pool

        from .memory.storage import PostgresBackend

        backend = PostgresBackend(pool)

        _LOGGER.info(
            "PostgreSQL connected: %s:%s/%s", data.get(CONF_PG_HOST), data.get(CONF_PG_PORT), data.get(CONF_PG_DATABASE)
        )

        # A3: bootstrap memory_ops audit table (idempotent).
        # Follow-up: move to memory/migrations.py with versioned migrations.
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_ops (
                    id SERIAL PRIMARY KEY,
                    op VARCHAR(20) NOT NULL,
                    target_table VARCHAR(50),
                    target_key JSONB NOT NULL DEFAULT '{}'::jsonb,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    before_state JSONB,
                    reason TEXT,
                    confidence REAL,
                    user_name VARCHAR(100),
                    session_id VARCHAR(100),
                    op_hash VARCHAR(32),
                    raw_response TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    reverted_at TIMESTAMPTZ
                )
            """)
            # If an older memory_ops exists without op_hash, add it — PR #44 review.
            await conn.execute("ALTER TABLE memory_ops ADD COLUMN IF NOT EXISTS op_hash VARCHAR(32)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_ops_created ON memory_ops(created_at DESC)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_ops_user ON memory_ops(user_name)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_ops_session ON memory_ops(session_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_ops_op_hash ON memory_ops(op_hash)")

            # A4: soft-delete primitive — add deleted_at column + partial-live indexes.
            await conn.execute("ALTER TABLE memory_entries ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
            await conn.execute("ALTER TABLE preferences ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_entries_live "
                "ON memory_entries(category, user_name) WHERE deleted_at IS NULL"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_preferences_live "
                "ON preferences(person_name, key) WHERE deleted_at IS NULL"
            )

            # B1: semantic preference dedup — embedding column + ivfflat index + audit table.
            await conn.execute("ALTER TABLE preferences ADD COLUMN IF NOT EXISTS embedding VECTOR(768)")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_preferences_embedding "
                "ON preferences USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10)"
            )
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS preference_merges (
                    id SERIAL PRIMARY KEY,
                    loser_id INT NOT NULL,
                    winner_id INT NOT NULL,
                    loser_key VARCHAR(200),
                    loser_value TEXT,
                    winner_key VARCHAR(200),
                    winner_value_before TEXT,
                    winner_value_after TEXT,
                    similarity REAL,
                    reason TEXT,
                    merged_at TIMESTAMPTZ DEFAULT NOW(),
                    reverted_at TIMESTAMPTZ
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_preference_merges_merged_at ON preference_merges(merged_at DESC)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_preference_merges_winner ON preference_merges(winner_id)"
            )

            # B5: weekly memory health snapshots (JANE-82). No unique index —
            # every run inserts a row; restart-induced double-rows are information.
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_health_samples (
                    id SERIAL PRIMARY KEY,
                    period_start TIMESTAMPTZ NOT NULL,
                    period_end TIMESTAMPTZ NOT NULL,
                    prefs_per_person JSONB NOT NULL DEFAULT '{}'::jsonb,
                    prefs_total INT NOT NULL DEFAULT 0,
                    extraction_calls INT NOT NULL DEFAULT 0,
                    consolidation_ops INT NOT NULL DEFAULT 0,
                    corrections INT NOT NULL DEFAULT 0,
                    forget_invocations INT NOT NULL DEFAULT 0,
                    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
                    schema_version INT NOT NULL DEFAULT 1,
                    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_health_period ON memory_health_samples(period_end DESC)"
            )
            # Helper for metric (3): consolidations PRODUCED in the window
            # (not whose content is from the window — start_ts is event-time).
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_episodes_created_at ON episodes(created_at DESC)")

            # B4: corrections lifecycle (JANE-83). `status` lives on every event row but
            # only carries semantics for event_type='correction'. Daily sweep transitions
            # open → applied → resolved → DELETE.
            await conn.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'open'")
            await conn.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_status_type "
                "ON events(event_type, status, timestamp DESC) "
                "WHERE event_type = 'correction'"
            )
            # Idempotent backfill: catches genuinely-historical correction rows on first
            # migration. 30d cut-off (not 7d) so the daily sweep retains first-crack
            # semantics on the 7-30d band even after an HA-offline window of >7d.
            await conn.execute(
                "UPDATE events SET status = 'resolved', resolved_at = COALESCE(timestamp, NOW()) "
                "WHERE event_type = 'correction' AND status = 'open' "
                "AND timestamp < NOW() - INTERVAL '30 days'"
            )

        # Auto-migrate MD files on first PG connect
        await _auto_migrate(pool, hass)

        # Initialize stores
        from .memory.episodic import EpisodicStore
        from .memory.structured import StructuredMemoryStore

        jane.structured = StructuredMemoryStore(pool)
        jane.episodic = EpisodicStore(pool)

        # Initialize routine store (S1.5)
        from .memory.routine_store import RoutineStore

        jane.routines = RoutineStore(pool)

        # Initialize policy store (S1.5)
        from .memory.policy import PolicyStore

        jane.policies = PolicyStore(pool)

        # Seed default policies for existing persons
        if jane.structured:
            try:
                persons = await jane.structured.load_persons()
                if persons:
                    seeded = await jane.policies.seed_defaults(persons)
                    if seeded:
                        _LOGGER.info("Seeded default policies for %d persons", seeded)
            except Exception as e:
                _LOGGER.debug("Policy seeding skipped: %s", e)

        # Auto-migrate MD → structured tables on first connect
        from pathlib import Path as _Path

        from .memory.migrate_structured import migrate_to_structured

        memory_dir = _Path(hass.config.config_dir) / "jane_memory"
        file_data = await hass.async_add_executor_job(_read_migration_files, memory_dir)
        if file_data:
            await migrate_to_structured(jane.structured, file_data)

        return backend

    except Exception as e:
        _LOGGER.error("Failed to connect to PostgreSQL: %s", e)
        return None


async def _auto_migrate(pool, hass: HomeAssistant) -> None:
    """Auto-migrate permanent MD memory files to PostgreSQL on first connect."""
    from pathlib import Path

    try:
        async with pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM memory_entries WHERE content != ''")
            if count > 0:
                _LOGGER.info("PG already has %d memory entries, skipping migration", count)
                return

        memory_dir = Path(hass.config.config_dir) / "jane_memory"
        if not memory_dir.exists():
            _LOGGER.info("No jane_memory directory found, skipping migration")
            return

        migrated = 0
        categories = {
            "family": memory_dir / "family.md",
            "habits": memory_dir / "habits.md",
            "corrections": memory_dir / "corrections.md",
            "routines": memory_dir / "routines.md",
            "home": memory_dir / "home.md",
        }

        async with pool.acquire() as conn:
            for category, path in categories.items():
                content = await hass.async_add_executor_job(
                    lambda p: p.read_text(encoding="utf-8").strip() if p.exists() else "", path
                )
                if content:
                    await conn.execute(
                        """INSERT INTO memory_entries (category, user_name, content, updated_at)
                           VALUES ($1, NULL, $2, NOW())
                           ON CONFLICT (category, user_name)
                           DO UPDATE SET content = $2, updated_at = NOW()""",
                        category,
                        content,
                    )
                    migrated += 1

            users_dir = memory_dir / "users"
            user_files = await hass.async_add_executor_job(
                lambda: list(users_dir.glob("*.md")) if users_dir.exists() else []
            )
            for user_file in user_files:
                content = await hass.async_add_executor_job(lambda p: p.read_text(encoding="utf-8").strip(), user_file)
                if content:
                    await conn.execute(
                        """INSERT INTO memory_entries (category, user_name, content, updated_at)
                           VALUES ('user', $1, $2, NOW())
                           ON CONFLICT (category, user_name)
                           DO UPDATE SET content = $2, updated_at = NOW()""",
                        user_file.stem,
                        content,
                    )
                    migrated += 1

        _LOGGER.info("Auto-migrated %d memory entries from MD files to PostgreSQL", migrated)

    except Exception as e:
        _LOGGER.warning("Auto-migration failed (non-fatal, files still work): %s", e)


def _read_migration_files(memory_dir) -> dict | None:
    """Read MD files for structured migration (sync, runs in executor)."""
    result = {}
    family = memory_dir / "family.md"
    if family.exists():
        result["family"] = family.read_text(encoding="utf-8")
    users = memory_dir / "users"
    if users.exists():
        result["users"] = {f.stem: f.read_text(encoding="utf-8") for f in users.glob("*.md")}
    return result or None


async def _ensure_household_mode_helper(hass: HomeAssistant) -> None:
    """Auto-create the input_select.jane_household_mode helper if missing.

    The helper is the user-visible source of truth for the active household
    mode (S3.1 / JANE-42). Idempotent — if the entity already exists we
    no-op so a fresh HA restart doesn't duplicate or override a user-edited
    options list. Failures are swallowed: a missing helper degrades to
    MODE_NORMAL via `get_active_mode`'s fallback path, never crashes.
    """
    from .modes import HELPER_ENTITY_ID, HOUSEHOLD_MODES, MODE_NORMAL

    if hass.states.get(HELPER_ENTITY_ID) is not None:
        return
    try:
        await hass.services.async_call(
            "input_select",
            "create",
            {
                "name": "Jane Household Mode",
                "options": list(HOUSEHOLD_MODES),
                "initial": MODE_NORMAL,
                "icon": "mdi:home-account",
            },
            blocking=True,
        )
        _LOGGER.info("Created household mode helper: %s", HELPER_ENTITY_ID)
    except Exception as e:  # noqa: BLE001
        _LOGGER.warning(
            "Could not auto-create %s (%s) — create it manually in Settings → Helpers",
            HELPER_ENTITY_ID,
            e,
        )


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config entry updates (options flow)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Jane config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        jane = hass.data.pop(DOMAIN, None)
        if jane and isinstance(jane, JaneData):
            jane.cancel_all()
            if jane.redis:
                await jane.redis.aclose()
            if jane.pg_pool:
                await jane.pg_pool.close()
    return unload_ok
