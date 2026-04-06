"""
Jane Memory System — LLM-managed markdown memory.
Memory content is stored in English. Conversations remain in Hebrew.
"""

import os
import json
from pathlib import Path
from datetime import datetime, timedelta

from openai import OpenAI
from config import OPENAI_API_KEY, MEMORY_DIR

client = OpenAI(api_key=OPENAI_API_KEY)


# ---------------------------------------------------------------------------
# Directory setup
# ---------------------------------------------------------------------------

def get_memory_dir() -> Path:
    """Returns the memory directory path, creating it if needed."""
    p = Path(MEMORY_DIR)
    (p / "users").mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Load functions
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    """Read a file, return empty string if missing."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def load_user_memory(user_name: str) -> str:
    return _read(get_memory_dir() / "users" / f"{user_name.lower().strip()}.md")


def load_family_memory() -> str:
    return _read(get_memory_dir() / "family.md")


def load_habits_memory() -> str:
    return _read(get_memory_dir() / "habits.md")


def load_actions() -> str:
    return _read(get_memory_dir() / "actions.md")


def load_home() -> str:
    return _read(get_memory_dir() / "home.md")


def load_corrections() -> str:
    return _read(get_memory_dir() / "corrections.md")


def load_routines() -> str:
    return _read(get_memory_dir() / "routines.md")


def load_all_memory(user_name: str) -> str:
    """Combine all memory files into a single context block for GPT."""
    sections = {
        "Personal Memory": load_user_memory(user_name),
        "Family Memory": load_family_memory(),
        "Behavioral Patterns": load_habits_memory(),
        "Recent Actions (24h)": load_actions(),
        "Home Layout": load_home(),
        "Corrections & Learnings": load_corrections(),
        "Routines": load_routines(),
    }

    parts = []
    for title, content in sections.items():
        parts.append(f"## {title}")
        parts.append(content if content else "No data yet.")
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Save functions
# ---------------------------------------------------------------------------

def _write(path: Path, content: str):
    """Atomic write — write to tmp then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def save_user_memory(user_name: str, content: str):
    _write(get_memory_dir() / "users" / f"{user_name.lower().strip()}.md", content)


def save_family_memory(content: str):
    _write(get_memory_dir() / "family.md", content)


def save_habits_memory(content: str):
    _write(get_memory_dir() / "habits.md", content)


def save_corrections(content: str):
    _write(get_memory_dir() / "corrections.md", content)


def save_routines(content: str):
    _write(get_memory_dir() / "routines.md", content)


# ---------------------------------------------------------------------------
# Action log (code-managed, not GPT)
# ---------------------------------------------------------------------------

def append_action(user_name: str, description: str):
    """Append a timestamped action and prune entries older than 24h."""
    path = get_memory_dir() / "actions.md"
    now = datetime.now()
    new_line = f"- {now.strftime('%Y-%m-%d %H:%M')} — {description} ({user_name})"

    # Read existing lines
    lines = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("- "):
                try:
                    ts_str = line.split(" — ")[0].replace("- ", "")
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M")
                    if now - ts < timedelta(hours=24):
                        lines.append(line)
                except (ValueError, IndexError):
                    lines.append(line)
            elif line.startswith("#"):
                continue  # skip header, we'll re-add it

    lines.append(new_line)
    content = "# Recent Actions (rolling 24h)\n\n" + "\n".join(lines) + "\n"
    _write(path, content)


# ---------------------------------------------------------------------------
# Home map (code-managed, generated from HA)
# ---------------------------------------------------------------------------

def rebuild_home_map():
    """Generate home.md from HA entity list."""
    from ha_client import get_exposed_entities

    entities = get_exposed_entities()
    if not entities:
        return

    # Group by domain
    by_domain = {}
    for e in entities:
        domain = e["domain"]
        if domain not in by_domain:
            by_domain[domain] = []
        by_domain[domain].append(e)

    domain_labels = {
        "light": "Lights",
        "switch": "Switches",
        "climate": "Climate",
        "cover": "Covers",
        "media_player": "Media",
        "fan": "Fans",
    }

    lines = ["# Home Layout", ""]
    for domain, ents in sorted(by_domain.items()):
        label = domain_labels.get(domain, domain.title())
        lines.append(f"## {label}")
        for e in ents:
            lines.append(f"- {e['name']} ({e['entity_id']})")
        lines.append("")

    _write(get_memory_dir() / "home.md", "\n".join(lines))


# ---------------------------------------------------------------------------
# Memory extraction (GPT-managed)
# ---------------------------------------------------------------------------

MEMORY_EXTRACTION_PROMPT = """You are the memory manager for Jane, a Hebrew smart home assistant.
Analyze the conversation below and decide what should be remembered or updated.

Current memory state:

{memory_context}

---

Latest conversation:
User ({user_name}): {user_text}
Jane: {jane_response}

---

Instructions:
1. Decide if any memory files need updating with new information.
2. If yes — rewrite the ENTIRE content of each file that needs updating, merging new info with existing.
3. Resolve contradictions: new information wins over old.
4. Remove stale or irrelevant information.
5. Keep each file concise (max ~50 lines).
6. Write all memory content in English, even though conversations are in Hebrew.
7. If nothing worth remembering — return null for that file.

Respond in JSON only:
{
  "user": "Full updated content of the user's personal memory file, or null if no update needed",
  "family": "Full updated content of the family memory file, or null",
  "habits": "Full updated content of the habits file, or null",
  "corrections": "Full updated content of the corrections file, or null",
  "routines": "Full updated content of the routines file, or null"
}

What IS worth remembering:
- Personal preferences: "I don't like bright lights" → user preference
- Household rules: "Kids go to bed at 21:00" → family
- Recurring patterns: "Every morning I ask for heating" → habit
- Corrections: "No, I meant the living room, not kitchen" → correction
- Routine definitions: "Goodnight means: turn off lights, lock door, close shutters" → routine
- Personal facts: "I work from home" → user fact

What is NOT worth remembering:
- One-time device commands: "Turn on the light" → skip
- General questions: "What time is it?" → skip
- Pleasantries: "Thank you" → skip"""


def process_memory(user_name: str, user_text: str, jane_response: str, action: str):
    """Background: analyze conversation and update memory if needed."""
    # Skip simple device commands (unless it might contain a correction)
    if action == "ha_service" and len(jane_response) < 30:
        return

    memory_context = load_all_memory(user_name)

    prompt = MEMORY_EXTRACTION_PROMPT.format(
        memory_context=memory_context,
        user_name=user_name,
        user_text=user_text,
        jane_response=jane_response,
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            max_tokens=2000,
            temperature=0.3,
        )

        raw = response.choices[0].message.content.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        if raw.endswith("```"):
            raw = raw[:-3]

        result = json.loads(raw.strip())

        if result.get("user"):
            save_user_memory(user_name, result["user"])
        if result.get("family"):
            save_family_memory(result["family"])
        if result.get("habits"):
            save_habits_memory(result["habits"])
        if result.get("corrections"):
            save_corrections(result["corrections"])
        if result.get("routines"):
            save_routines(result["routines"])

        print(f"🧠 Memory updated for {user_name}")

    except Exception as e:
        print(f"⚠️ Memory extraction failed: {e}")
