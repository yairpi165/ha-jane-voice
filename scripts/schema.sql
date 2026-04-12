-- Jane Memory Schema v1.0
-- Run against the 'jane' database after add-on starts

-- Memory entries: replaces the 7 MD files
CREATE TABLE IF NOT EXISTS memory_entries (
    id SERIAL PRIMARY KEY,
    category VARCHAR(50) NOT NULL,
    user_name VARCHAR(100),
    content TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(category, user_name)
);

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

-- Anti-repetition tracking (replaces in-memory list)
CREATE TABLE IF NOT EXISTS response_tracking (
    id SERIAL PRIMARY KEY,
    opening TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Seed memory categories (so we know what exists)
INSERT INTO memory_entries (category, user_name, content)
VALUES
    ('family', NULL, ''),
    ('habits', NULL, ''),
    ('corrections', NULL, ''),
    ('routines', NULL, ''),
    ('home', NULL, '')
ON CONFLICT (category, user_name) DO NOTHING;
