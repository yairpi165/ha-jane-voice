-- Jane Memory Schema v1.1 (reference copy)
-- Source of truth: https://github.com/yairpi165/ha-jane-db/blob/main/jane_db/schema.sql
-- For self-managed PostgreSQL (without ha-jane-db add-on), run this DDL manually.

-- Memory entries: replaces the 7 MD files
CREATE TABLE IF NOT EXISTS memory_entries (
    id SERIAL PRIMARY KEY,
    category VARCHAR(50) NOT NULL,
    user_name VARCHAR(100),
    content TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- v1.1: Fix unique constraint — NULL != NULL caused duplicates
-- Clean any existing duplicates (keep highest id = latest content)
DELETE FROM memory_entries a USING memory_entries b
WHERE a.id < b.id
  AND a.category = b.category
  AND a.user_name IS NOT DISTINCT FROM b.user_name;

ALTER TABLE memory_entries DROP CONSTRAINT IF EXISTS memory_entries_category_user_name_key;
DROP INDEX IF EXISTS uq_memory_category_user;
CREATE UNIQUE INDEX uq_memory_category_user
    ON memory_entries (category, user_name) NULLS NOT DISTINCT;

-- Events: replaces actions.md + history.log (append-only audit trail)
CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    event_type VARCHAR(50) NOT NULL,
    user_name VARCHAR(100),
    description TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_name);

-- S1.3: Semantic Memory — Household Graph
CREATE TABLE IF NOT EXISTS persons (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    role VARCHAR(50),
    birth_date DATE,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS relationships (
    id SERIAL PRIMARY KEY,
    person_a_id INT REFERENCES persons(id) ON DELETE CASCADE,
    person_b_id INT REFERENCES persons(id) ON DELETE CASCADE,
    relation VARCHAR(50) NOT NULL,
    UNIQUE(person_a_id, person_b_id, relation)
);

-- S1.3: Preference Memory
CREATE TABLE IF NOT EXISTS preferences (
    id SERIAL PRIMARY KEY,
    person_name VARCHAR(100) NOT NULL,
    key VARCHAR(200) NOT NULL,
    value TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    inferred BOOLEAN DEFAULT FALSE,
    source VARCHAR(50) DEFAULT 'extraction',
    last_reinforced TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(person_name, key)
);

CREATE INDEX IF NOT EXISTS idx_preferences_person ON preferences(person_name);
CREATE INDEX IF NOT EXISTS idx_preferences_confidence ON preferences(confidence) WHERE confidence > 0.3;

-- S1.4: Episodic Memory — event entity links, episodes, daily summaries

CREATE TABLE IF NOT EXISTS event_entities (
    id SERIAL PRIMARY KEY,
    event_id INT REFERENCES events(id) ON DELETE CASCADE,
    entity_id VARCHAR(200) NOT NULL,
    friendly_name VARCHAR(200)
);
CREATE INDEX IF NOT EXISTS idx_event_entities_event ON event_entities(event_id);
CREATE INDEX IF NOT EXISTS idx_event_entities_entity ON event_entities(entity_id);

CREATE TABLE IF NOT EXISTS episodes (
    id SERIAL PRIMARY KEY,
    title VARCHAR(300) NOT NULL,
    summary TEXT NOT NULL,
    start_ts TIMESTAMPTZ NOT NULL,
    end_ts TIMESTAMPTZ NOT NULL,
    episode_type VARCHAR(50) NOT NULL DEFAULT 'activity',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_episodes_start ON episodes(start_ts DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_type ON episodes(episode_type);

CREATE TABLE IF NOT EXISTS daily_summaries (
    id SERIAL PRIMARY KEY,
    summary_date DATE NOT NULL UNIQUE,
    summary TEXT NOT NULL,
    event_count INT DEFAULT 0,
    episode_count INT DEFAULT 0,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_daily_summaries_date ON daily_summaries(summary_date DESC);

-- S1.5: Routine Memory
CREATE TABLE IF NOT EXISTS routines (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL UNIQUE,
    trigger_phrase VARCHAR(300) NOT NULL,
    steps JSONB NOT NULL DEFAULT '[]',
    script_id VARCHAR(200),
    confidence REAL DEFAULT 1.0,
    occurrence_count INT DEFAULT 1,
    last_used TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- S1.5: Policy Memory
CREATE TABLE IF NOT EXISTS policies (
    id SERIAL PRIMARY KEY,
    person_name VARCHAR(100) NOT NULL,
    key VARCHAR(100) NOT NULL,
    value TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(person_name, key)
);

-- Anti-repetition tracking (replaces in-memory list)
CREATE TABLE IF NOT EXISTS response_tracking (
    id SERIAL PRIMARY KEY,
    opening TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- A3: Operations-based extraction audit log
-- Every memory write emitted by the extractor lands here with before-state + reason.
-- op VARCHAR(20) leaves room for future source tags (CONSOLIDATION/TOOL_WRITE/DECAY).
-- op_hash is the B-tree-indexed idempotency key — same session_id + op + target ⇒ same hash.
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
);
CREATE INDEX IF NOT EXISTS idx_memory_ops_created ON memory_ops(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_ops_user ON memory_ops(user_name);
CREATE INDEX IF NOT EXISTS idx_memory_ops_session ON memory_ops(session_id);
CREATE INDEX IF NOT EXISTS idx_memory_ops_op_hash ON memory_ops(op_hash);

-- A4: Soft-delete primitive
-- Deleted_at tombstone column on the two tables the op-extractor DELETEs from.
-- Readers filter `WHERE deleted_at IS NULL`; save paths clear `deleted_at` on revive,
-- which preserves the existing unique constraints (at most one row per key, live or tombstoned).
ALTER TABLE memory_entries ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE preferences    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_memory_entries_live ON memory_entries(category, user_name) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_preferences_live    ON preferences(person_name, key)       WHERE deleted_at IS NULL;

-- B1: Semantic preference dedup
-- Stage 2 embedding sweep stores one 768-dim vector per pref row, compares
-- pairwise via cosine, merges >=0.95 auto + 0.85-0.95 via Gemini arbitration.
-- Audit row per merge preserves both sides for manual revert.
ALTER TABLE preferences ADD COLUMN IF NOT EXISTS embedding VECTOR(768);
CREATE INDEX IF NOT EXISTS idx_preferences_embedding
    ON preferences USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

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
);
CREATE INDEX IF NOT EXISTS idx_preference_merges_merged_at ON preference_merges(merged_at DESC);
CREATE INDEX IF NOT EXISTS idx_preference_merges_winner ON preference_merges(winner_id);

-- B5: Weekly memory health snapshots (JANE-82).
-- No unique index — every run inserts a row; restart-induced double-rows
-- are information about the scheduler, not noise to dedup.
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
);
CREATE INDEX IF NOT EXISTS idx_memory_health_period ON memory_health_samples(period_end DESC);
-- Helper for B5 metric (3): consolidations PRODUCED in the window
-- (not whose content is from the window — start_ts is event-time).
CREATE INDEX IF NOT EXISTS idx_episodes_created_at ON episodes(created_at DESC);
