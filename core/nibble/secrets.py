"""API keys live in the OS keyring, never in the DB or sent to the UI."""
from __future__ import annotations

from . import config

try:
    import keyring
    _OK = True
except Exception:  # pragma: no cover - environment without a backend
    _OK = False


def set_key(name: str, value: str) -> None:
    if not _OK:
        raise RuntimeError("keyring backend unavailable")
    if value:
        keyring.set_password(config.KEYRING_SERVICE, name, value)
    else:
        try:
            keyring.delete_password(config.KEYRING_SERVICE, name)
        except Exception:
            pass


def get_key(name: str) -> str | None:
    if not _OK:
        return None
    try:
        return keyring.get_password(config.KEYRING_SERVICE, name)
    except Exception:
        return None


def has_key(name: str) -> bool:
    return bool(get_key(name))
