"""One-time migration from MD files to structured tables (S1.3).

Parses family.md and users/*.md to populate persons, relationships,
and preferences tables. Uses PREFERENCE_KEY_TAXONOMY for key mapping.
"""

import logging
import re

_LOGGER = logging.getLogger(__name__)

# Map common preference patterns to taxonomy keys
_PREFERENCE_MAP = {
    "tv": "default_tv",
    "good morning": "morning_greeting_style",
    "good night": "goodnight_style",
    "laila tov": "goodnight_style",
    "emoji": "emoji_preference",
    "act directly": "action_style",
    "tool": "tool_usage_policy",
    "explanation": "explanation_preference",
    "beitar": "football_teams",
    "real madrid": "football_teams",
    "football": "entertainment_interests",
    "pizza": "food_preferences",
    "sushi": "food_preferences",
    "food": "food_preferences",
    "coffee": "food_preferences",
    "tea": "food_preferences",
    "music": "music_taste",
    "guitar": "hobbies",
    "chess": "hobbies",
    "lego": "hobbies",
    "run": "morning_routine",
    "screen time": "screen_time_rules",
    "tami4": "tami4_reminder_preference",
}


async def migrate_to_structured(store, file_data: dict) -> int:
    """Migrate pre-read MD content to structured tables. Returns count of items migrated.

    file_data: {"family": str, "users": {"name": str, ...}} — read in executor by caller.
    Primary user inferred from first user in file_data["users"].
    """
    count = 0

    # Check if already migrated — skip only if BOTH persons and preferences exist
    existing_persons = await store.load_persons()
    existing_prefs = await store.load_all_preferences(min_confidence=0.0)
    if existing_persons and existing_prefs:
        _LOGGER.debug("Structured tables already populated, skipping migration")
        return 0

    # Infer primary user from users directory
    users = file_data.get("users", {})
    primary_user = next(iter(users), "").capitalize() if users else ""

    # Migrate family content → persons + relationships
    if file_data.get("family") and primary_user:
        count += await _migrate_family(store, file_data["family"], primary_user)

    # Migrate user content → preferences
    for user_name, content in users.items():
        count += await _migrate_user_preferences(store, user_name, content)

    if count:
        _LOGGER.info("Structured migration: %d items migrated from MD files", count)
    return count


async def _migrate_family(store, content: str, primary_user: str) -> int:
    """Parse family.md and create persons + relationships."""
    count = 0
    lines = content.splitlines()
    primary_lower = primary_user.lower()

    for line in lines:
        line = line.strip()
        if not line.startswith("- "):
            continue
        line = line[2:]

        # Pattern: "Name: description"
        match = re.match(r"^(\w+):\s*(.+)", line)
        if not match:
            continue
        name = match.group(1)
        desc = match.group(2).lower()

        # Determine role
        role = None
        if "wife" in desc or "husband" in desc:
            role = "parent"
        elif "son" in desc or "daughter" in desc:
            role = "child"
        elif "cat" in desc or "dog" in desc:
            role = "pet"

        # Extract birth date
        birth_date = None
        date_match = re.search(r"born.*?(\d{4}-\d{2}-\d{2})", desc)
        if date_match:
            from datetime import date

            try:
                birth_date = date.fromisoformat(date_match.group(1))
            except ValueError:
                pass

        # Extract metadata from description
        metadata = {}
        if "lego" in desc:
            metadata["hobbies"] = "Lego"
        if "youtube" in desc.lower():
            metadata["media"] = "YouTube Kids"
        if "school" in desc or "grade" in desc:
            metadata["education"] = "first grade" if "first" in desc else "school"
        if "math" in desc:
            metadata["subjects"] = "math"
        if "kindergarten" in desc:
            metadata["education"] = "kindergarten"

        await store.save_person(name, role=role, birth_date=birth_date, metadata=metadata or None)
        count += 1

        # Create relationship to primary user if mentioned
        if primary_lower in desc:
            if "wife" in desc or "husband" in desc:
                await store.save_relationship(primary_user, name, "spouse")
            elif "son" in desc or "daughter" in desc:
                await store.save_relationship(primary_user, name, "parent_of")

    return count


async def _migrate_user_preferences(store, user_name: str, content: str) -> int:
    """Parse user MD file and create preferences."""
    count = 0
    lines = content.splitlines()
    section = None

    for line in lines:
        stripped = line.strip()

        # Track sections
        if stripped.startswith("Preferences:") or stripped.startswith("## Preferences"):
            section = "preferences"
            continue
        elif stripped.startswith("Interests:") or stripped.startswith("## Interests"):
            section = "interests"
            continue
        elif stripped.startswith("##") or stripped.startswith("Name:") or stripped.startswith("Location:"):
            section = None
            continue

        if not stripped.startswith("- "):
            continue
        text = stripped[2:].strip()
        if not text:
            continue

        # Map to taxonomy key
        key = _map_to_key(text, section)
        if not key:
            continue

        await store.save_preference(user_name, key, text, inferred=False, source="migration")
        count += 1

    return count


def _map_to_key(text: str, section: str | None) -> str | None:
    """Map a preference/interest line to a taxonomy key."""
    text_lower = text.lower()

    # Check known patterns
    for pattern, key in _PREFERENCE_MAP.items():
        if pattern in text_lower:
            return key

    # Section-based fallback
    if section == "interests":
        if "series" in text_lower or "movie" in text_lower or "football" in text_lower:
            return "entertainment_interests"
        return f"note_{_slug(text)}"

    if section == "preferences":
        return f"note_{_slug(text)}"

    return None


def _slug(text: str) -> str:
    """Create a short slug from text."""
    words = re.sub(r"[^a-zA-Z0-9\s]", "", text.lower()).split()
    return "_".join(words[:3])
