"""Firebase Firestore backup for Jane's memory files."""

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import aiohttp
from google.oauth2 import service_account

_LOGGER = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/datastore"]
COLLECTION = "jane-memory"

_credentials = None
_project_id = None


def init_firebase(key_path: str) -> bool:
    """Initialize Firebase credentials from service account JSON."""
    global _credentials, _project_id

    path = Path(key_path)
    if not path.exists():
        _LOGGER.error("Firebase key file not found: %s", key_path)
        return False

    try:
        creds = service_account.Credentials.from_service_account_file(
            str(path), scopes=SCOPES
        )
        with open(path) as f:
            data = json.load(f)

        _credentials = creds
        _project_id = data.get("project_id")
        _LOGGER.info("Firebase initialized for project: %s", _project_id)
        return True
    except Exception as e:
        _LOGGER.error("Firebase init failed: %s", e)
        return False


def _get_base_url() -> str:
    """Get Firestore REST API base URL."""
    return (
        f"https://firestore.googleapis.com/v1/"
        f"projects/{_project_id}/databases/(default)/documents"
    )


def _refresh_token() -> str | None:
    """Refresh credentials and return token. Blocking — run in executor."""
    try:
        if not _credentials.valid:
            import google.auth.transport.requests
            request = google.auth.transport.requests.Request()
            _credentials.refresh(request)
        return _credentials.token
    except Exception as e:
        _LOGGER.error("Firebase token refresh failed: %s", e)
        return None


async def _get_token() -> str | None:
    """Get a valid access token, refreshing in executor if needed."""
    if _credentials is None:
        return None
    return await asyncio.get_event_loop().run_in_executor(None, _refresh_token)


async def backup_memory(doc_name: str, content: str) -> bool:
    """Backup a memory file to Firestore."""
    if _credentials is None or _project_id is None:
        return False

    token = await _get_token()
    if not token:
        return False

    url = f"{_get_base_url()}/{COLLECTION}/{doc_name}"
    body = {
        "fields": {
            "content": {"stringValue": content},
            "updated": {"stringValue": datetime.now(UTC).isoformat()},
        }
    }

    try:
        async with aiohttp.ClientSession() as session, session.patch(
            url,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            if resp.status in (200, 201):
                _LOGGER.debug("Backed up %s to Firestore", doc_name)
                return True
            else:
                text = await resp.text()
                _LOGGER.warning(
                    "Firestore backup failed for %s: %s %s",
                    doc_name, resp.status, text,
                )
                return False
    except Exception as e:
        _LOGGER.warning("Firestore backup error for %s: %s", doc_name, e)
        return False


async def restore_memory(doc_name: str) -> str | None:
    """Restore a memory file from Firestore. Returns content or None."""
    if _credentials is None or _project_id is None:
        return None

    token = await _get_token()
    if not token:
        return None

    url = f"{_get_base_url()}/{COLLECTION}/{doc_name}"

    try:
        async with aiohttp.ClientSession() as session, session.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                content = (
                    data.get("fields", {})
                    .get("content", {})
                    .get("stringValue")
                )
                if content:
                    _LOGGER.info("Restored %s from Firestore", doc_name)
                    return content
            elif resp.status == 404:
                return None
            else:
                _LOGGER.warning(
                    "Firestore restore failed for %s: %s", doc_name, resp.status
                )
    except Exception as e:
        _LOGGER.warning("Firestore restore error for %s: %s", doc_name, e)

    return None


async def restore_all_memory(memory_dir: Path) -> None:
    """Restore missing memory files from Firestore."""
    if _credentials is None:
        return

    # Files to check and their Firestore doc names
    files = {
        "family.md": "family",
        "habits.md": "habits",
        "corrections.md": "corrections",
        "routines.md": "routines",
    }

    for filename, doc_name in files.items():
        filepath = memory_dir / filename
        if filepath.exists() and filepath.stat().st_size > 0:
            continue

        content = await restore_memory(doc_name)
        if content:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(content, encoding="utf-8")
            _LOGGER.info("Restored %s from Firestore backup", filename)

    # Restore user files
    users_dir = memory_dir / "users"
    if not users_dir.exists():
        users_dir.mkdir(parents=True, exist_ok=True)

    # We can't enumerate Firestore docs easily via REST,
    # so user files are restored on first access if missing.


async def sync_existing_memory(memory_dir: Path) -> None:
    """Push all existing local memory files to Firestore (initial sync)."""
    if _credentials is None:
        return

    files = {
        "family.md": "family",
        "habits.md": "habits",
        "corrections.md": "corrections",
        "routines.md": "routines",
    }

    for filename, doc_name in files.items():
        filepath = memory_dir / filename
        if filepath.exists() and filepath.stat().st_size > 0:
            content = filepath.read_text(encoding="utf-8").strip()
            if content:
                await backup_memory(doc_name, content)

    # Sync user files
    users_dir = memory_dir / "users"
    if users_dir.exists():
        for user_file in users_dir.glob("*.md"):
            content = user_file.read_text(encoding="utf-8").strip()
            if content:
                doc_name = f"users_{user_file.stem}"
                await backup_memory(doc_name, content)
