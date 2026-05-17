"""Runtime configuration and paths."""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """Per-user data directory for the Nibble database and cache."""
    base = os.environ.get("NIBBLE_DATA_DIR")
    if base:
        p = Path(base)
    elif os.name == "nt":
        p = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Nibble"
    else:
        p = Path.home() / ".local" / "share" / "nibble"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "nibble.db"


def pricing_cache_path() -> Path:
    return data_dir() / "pricing_cache.json"


def claude_projects_dir() -> Path:
    override = os.environ.get("NIBBLE_CLAUDE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "projects"


KEYRING_SERVICE = "nibble"
# Keyring usernames for stored secrets
KEY_OPENAI = "openai_admin_key"
KEY_ANTHROPIC = "anthropic_admin_key"
KEY_ANTHROPIC_COMMENTARY = "anthropic_commentary_key"

POLL_LOCAL_SECONDS = int(os.environ.get("NIBBLE_POLL_LOCAL", "30"))
POLL_REMOTE_SECONDS = int(os.environ.get("NIBBLE_POLL_REMOTE", "1200"))
