"""Claude Code hook entrypoint — tiny, stdlib-only, fail-safe.

Invoked by Claude Code for configured events. Reads the event JSON on
stdin, asks the local Nibble core for a decision, and emits the verdict
in Claude Code's expected PreToolUse format. It must be fast and must
NEVER crash Claude Code: on any unexpected error it exits 0 (allow).
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

# Same risk tiers as the governor — used only for the offline fail-safe
# when the core is unreachable.
_DANGEROUS = {"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit",
              "WebFetch", "WebSearch"}
_SAFE = {"Read", "Grep", "Glob", "TodoWrite", "Task"}
_PREFIX = ("computer-use", "mcp__")
_GATED = {"PreToolUse"}


def _conn():
    from . import config  # light, stdlib-only
    p = config.data_dir() / "conductor.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _dangerous(tool: str) -> bool:
    if not tool or tool in _SAFE:
        return False
    if tool in _DANGEROUS:
        return True
    return any(tool.startswith(x) for x in _PREFIX)


def _emit_pretooluse(action: str, reason: str):
    # allow|deny -> Claude Code PreToolUse permission decision
    decision = "deny" if action == "deny" else "allow"
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason or "Nibble Conductor",
        }
    }
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()


def _failsafe(event: str, tool: str, reason: str):
    if event in _GATED and _dangerous(tool):
        _emit_pretooluse("deny", reason or "supervisor unavailable")
        sys.exit(0)
    # safe tool or non-gated event -> allow
    if event in _GATED:
        _emit_pretooluse("allow", reason or "supervisor unavailable")
    sys.exit(0)


def main(argv=None) -> int:
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        sys.exit(0)  # never wedge Claude Code
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        sys.exit(0)

    event = payload.get("hook_event_name") or ""
    tool = payload.get("tool_name") or ""

    try:
        c = _conn()
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{c['port']}/api/hook",
            data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {c['token']}"},
        )
        # Non-gated events must never block; gated may wait for a
        # supervise verdict (core enforces its own timeout ~90s).
        timeout = 120 if event in _GATED else 4
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError,
            FileNotFoundError):
        _failsafe(event, tool, "Nibble core unreachable")
        return 0
    except Exception:
        sys.exit(0)  # absolute last resort: allow

    if event in _GATED:
        _emit_pretooluse(data.get("action", "allow"),
                         data.get("reason", ""))
    # non-gated: registry-only, no output needed
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
