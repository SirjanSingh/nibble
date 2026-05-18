"""Install / remove Nibble's hook in the user's global Claude Code settings.

Nibble's command is appended LAST in each event list so user hooks run
first; Claude Code already blocks a tool if *any* hook denies, so
"most-restrictive wins" holds without us merging. A one-time backup of
settings.json is written before the first edit; uninstall restores cleanly.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SENTINEL = "nibble:conductor-hook"  # marks groups we own
EVENTS = ["PreToolUse", "SessionStart", "SessionEnd", "Stop",
          "SubagentStop"]


def settings_path() -> Path:
    override = os.environ.get("NIBBLE_CLAUDE_SETTINGS")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "settings.json"


def _hook_command() -> str:
    exe = sys.executable.replace("\\", "/")
    if getattr(sys, "frozen", False):
        return f'"{exe}" --hook'
    return f'"{exe}" -m nibble --hook'


def _load(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


def _group():
    return {
        "_nibble": SENTINEL,
        "hooks": [{"type": "command", "command": _hook_command(),
                   "timeout": 125}],
    }


def status() -> dict:
    p = settings_path()
    data = _load(p)
    hooks = data.get("hooks", {})
    installed = any(
        any(g.get("_nibble") == SENTINEL for g in hooks.get(ev, []))
        for ev in EVENTS
    )
    return {
        "installed": installed,
        "settings_path": str(p),
        "backup_exists": (p.with_suffix(".json.nibble-bak")).exists(),
        "command": _hook_command(),
    }


def install() -> dict:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = _load(p)

    backup = p.with_suffix(".json.nibble-bak")
    if p.exists() and not backup.exists():
        backup.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")

    hooks = data.setdefault("hooks", {})
    for ev in EVENTS:
        lst = hooks.setdefault(ev, [])
        lst[:] = [g for g in lst if g.get("_nibble") != SENTINEL]
        lst.append(_group())          # appended last => runs after user's

    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"ok": True, **status()}


def uninstall() -> dict:
    p = settings_path()
    data = _load(p)
    hooks = data.get("hooks", {})
    for ev in list(hooks.keys()):
        hooks[ev] = [g for g in hooks[ev]
                     if g.get("_nibble") != SENTINEL]
        if not hooks[ev]:
            del hooks[ev]
    if not hooks:
        data.pop("hooks", None)
    if p.exists():
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"ok": True, **status()}
