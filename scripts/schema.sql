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
