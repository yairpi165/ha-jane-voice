#!/usr/bin/env python3
"""Migrate Jane memory from MD files to PostgreSQL.

Usage:
    python3 scripts/migrate_md_to_pg.py --memory-dir /path/to/jane_memory --pg-host localhost --pg-port 5432 --pg-db jane --pg-user postgres --pg-pass PASSWORD

Does NOT delete MD files — they stay as backup.
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("migrate")


async def migrate(memory_dir: Path, pg_host: str, pg_port: int, pg_db: str, pg_user: str, pg_pass: str):
    import asyncpg

    log.info("Connecting to PostgreSQL %s:%s/%s...", pg_host, pg_port, pg_db)
    conn = await asyncpg.connect(
        host=pg_host, port=pg_port, database=pg_db, user=pg_user, password=pg_pass
    )

    migrated = 0
    errors = 0

    # --- Migrate memory categories ---
    categories = {
        "family": memory_dir / "family.md",
        "habits": memory_dir / "habits.md",
        "corrections": memory_dir / "corrections.md",
        "routines": memory_dir / "routines.md",
        "home": memory_dir / "home.md",
    }

    for category, path in categories.items():
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                await conn.execute(
                    """INSERT INTO memory_entries (category, user_name, content, updated_at)
                       VALUES ($1, NULL, $2, NOW())
                       ON CONFLICT (category, user_name) DO UPDATE SET content = $2, updated_at = NOW()""",
                    category, content,
                )
                log.info("  Migrated %s (%d chars)", category, len(content))
                migrated += 1
            else:
                log.info("  Skipped %s (empty)", category)
        else:
            log.info("  Skipped %s (not found)", category)

    # --- Migrate user files ---
    users_dir = memory_dir / "users"
    if users_dir.exists():
        for user_file in users_dir.glob("*.md"):
            content = user_file.read_text(encoding="utf-8").strip()
            if content:
                user_name = user_file.stem
                await conn.execute(
                    """INSERT INTO memory_entries (category, user_name, content, updated_at)
                       VALUES ('user', $1, $2, NOW())
                       ON CONFLICT (category, user_name) DO UPDATE SET content = $2, updated_at = NOW()""",
                    user_name, content,
                )
                log.info("  Migrated user/%s (%d chars)", user_name, len(content))
                migrated += 1

    # --- Migrate actions.md as events ---
    actions_path = memory_dir / "actions.md"
    if actions_path.exists():
        action_count = 0
        for line in actions_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("- "):
                try:
                    parts = line[2:].split(" — ", 1)
                    ts_str = parts[0].strip()
                    rest = parts[1] if len(parts) > 1 else ""
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M")

                    user_name = ""
                    description = rest
                    if rest.endswith(")") and "(" in rest:
                        idx = rest.rfind("(")
                        user_name = rest[idx + 1 : -1]
                        description = rest[:idx].strip()

                    await conn.execute(
                        """INSERT INTO events (timestamp, event_type, user_name, description)
                           VALUES ($1, 'action', $2, $3)""",
                        ts, user_name, description,
                    )
                    action_count += 1
                except (ValueError, IndexError) as e:
                    log.warning("  Skipped action line: %s (%s)", line[:50], e)
                    errors += 1
        log.info("  Migrated %d actions from actions.md", action_count)

    # --- Migrate history.log as events ---
    history_path = memory_dir / "history.log"
    if history_path.exists():
        history_count = 0
        lines = history_path.read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("[") and "] " in line:
                try:
                    ts_str = line[1 : line.index("]")]
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M")
                    rest = line[line.index("] ") + 2 :]

                    if ": " in rest:
                        user_name, text = rest.split(": ", 1)
                    else:
                        user_name, text = "", rest

                    # Look for Jane's response on next line
                    response = ""
                    if i + 1 < len(lines) and lines[i + 1].strip().startswith("["):
                        next_line = lines[i + 1].strip()
                        if "Jane: " in next_line:
                            response = next_line[next_line.index("Jane: ") + 6 :]
                            i += 1

                    await conn.execute(
                        """INSERT INTO events (timestamp, event_type, user_name, description, metadata)
                           VALUES ($1, 'conversation', $2, $3, $4::jsonb)""",
                        ts, user_name, text,
                        f'{{"response": "{response[:500]}"}}'
                    )
                    history_count += 1
                except (ValueError, IndexError) as e:
                    log.warning("  Skipped history line: %s (%s)", line[:50], e)
                    errors += 1
            i += 1
        log.info("  Migrated %d conversations from history.log", history_count)

    # --- Verify ---
    mem_count = await conn.fetchval("SELECT COUNT(*) FROM memory_entries WHERE content != ''")
    event_count = await conn.fetchval("SELECT COUNT(*) FROM events")

    log.info("")
    log.info("=== Migration Complete ===")
    log.info("  Memory entries: %d", mem_count)
    log.info("  Events: %d", event_count)
    log.info("  Errors: %d", errors)
    log.info("  MD files NOT deleted (kept as backup)")

    await conn.close()


def main():
    parser = argparse.ArgumentParser(description="Migrate Jane memory MD files to PostgreSQL")
    parser.add_argument("--memory-dir", required=True, help="Path to jane_memory directory")
    parser.add_argument("--pg-host", default="localhost")
    parser.add_argument("--pg-port", type=int, default=5432)
    parser.add_argument("--pg-db", default="jane")
    parser.add_argument("--pg-user", default="postgres")
    parser.add_argument("--pg-pass", default="")
    args = parser.parse_args()

    memory_dir = Path(args.memory_dir)
    if not memory_dir.exists():
        log.error("Memory directory not found: %s", memory_dir)
        sys.exit(1)

    asyncio.run(migrate(memory_dir, args.pg_host, args.pg_port, args.pg_db, args.pg_user, args.pg_pass))


if __name__ == "__main__":
    main()
