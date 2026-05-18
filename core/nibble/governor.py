"""Conductor governor: decide allow/deny/ask for Claude Code hook events.

Pure decision logic + a small async gate registry used by the server for
supervise-mode blocking. Pipeline (first decisive wins):
  panic -> budget caps -> policy rules -> session mode -> (ask|default)
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from . import budget as budget_mod
from .store import Store, utcnow_iso

PROTOCOL = "1"

# Risk tiers drive the fail-safe when the core can't answer in time.
DANGEROUS_TOOLS = {
    "Bash", "Write", "Edit", "MultiEdit", "NotebookEdit",
    "WebFetch", "WebSearch",
}
DANGEROUS_PREFIXES = ("computer-use", "mcp__")  # mcp__* often mutate
SAFE_TOOLS = {"Read", "Grep", "Glob", "TodoWrite", "Task"}

GATED_EVENTS = {"PreToolUse"}
TERMINAL_EVENTS = {"SessionEnd", "Stop"}


def tool_is_dangerous(tool: str) -> bool:
    if not tool:
        return False
    if tool in SAFE_TOOLS:
        return False
    if tool in DANGEROUS_TOOLS:
        return True
    return any(tool.startswith(p) for p in DANGEROUS_PREFIXES)


@dataclass
class Decision:
    action: str          # "allow" | "deny" | "ask"
    reason: str = ""
    by: str = "policy"   # panic|budget|policy|mode|failsafe|user


@dataclass
class Gate:
    gid: str
    sid: str
    tool: str
    event: str
    summary: str
    created: float
    fut: "asyncio.Future" = field(repr=False, default=None)


class Governor:
    def __init__(self, store: Store):
        self.store = store
        self.gates: dict[str, Gate] = {}
        self._seq = 0

    # ---- settings helpers ---------------------------------------------
    def _setting(self, key, default):
        v = self.store.get_setting(key, None)
        return default if v is None else v

    @property
    def panic(self) -> bool:
        return str(self.store.get_meta("panic", "0")) == "1"

    def set_panic(self, on: bool):
        self.store.set_meta("panic", "1" if on else "0")

    def supervise_timeout(self) -> float:
        try:
            return float(self._setting("supervise_timeout_s", 90))
        except (TypeError, ValueError):
            return 90.0

    # ---- payload normalisation ----------------------------------------
    @staticmethod
    def normalize(payload: dict) -> dict:
        ev = payload.get("hook_event_name") or payload.get("event") or ""
        tool = payload.get("tool_name") or payload.get("tool") or ""
        ti = payload.get("tool_input") or {}
        sid = payload.get("session_id") or payload.get("sid") or "unknown"
        cwd = payload.get("cwd") or ""
        cmd = ""
        path = ""
        url = ""
        if isinstance(ti, dict):
            cmd = str(ti.get("command", "") or "")
            path = str(ti.get("file_path", ti.get("path", "")) or "")
            url = str(ti.get("url", "") or "")
        summary = (cmd or path or url or tool or ev)[:200]
        return {"event": ev, "tool": tool, "sid": sid, "cwd": cwd,
                "cmd": cmd, "path": path, "url": url,
                "summary": summary, "raw": payload}

    # ---- policy matching ----------------------------------------------
    def _match(self, rule: dict, n: dict) -> bool:
        m = rule
        if "event" in m and m["event"] and m["event"] != n["event"]:
            return False
        if "tool" in m and m["tool"]:
            tools = m["tool"] if isinstance(m["tool"], list) else [m["tool"]]
            if n["tool"] not in tools:
                return False
        for key, field_ in (("command_regex", "cmd"),
                            ("path_regex", "path"),
                            ("url_regex", "url")):
            pat = m.get(key)
            if pat:
                try:
                    if not re.search(pat, n[field_] or ""):
                        return False
                except re.error:
                    return False
        # require at least one positive constraint to avoid match-all rules
        return any(k in m and m[k] for k in
                   ("event", "tool", "command_regex", "path_regex",
                    "url_regex"))

    def evaluate_policies(self, n: dict) -> Optional[Decision]:
        for p in self.store.list_policies():
            if not p.get("enabled"):
                continue
            try:
                rule = json.loads(p.get("match_json") or "{}")
            except json.JSONDecodeError:
                continue
            if self._match(rule, n):
                act = (p.get("action") or "ask").lower()
                if act in ("allow", "deny", "ask"):
                    return Decision(act, p.get("reason") or
                                    f"policy: {p.get('label') or p['id']}",
                                    "policy")
        return None

    # ---- budget caps ---------------------------------------------------
    def check_caps(self, n: dict) -> Optional[Decision]:
        caps = {c["scope"]: c for c in self.store.list_caps()}
        if not caps:
            return None
        b = budget_mod.compute(self.store)
        if "today" in caps:
            lim = caps["today"].get("limit_usd")
            if lim and b.spent_today >= float(lim):
                return Decision(
                    "deny",
                    f"daily cap ${float(lim):.2f} reached "
                    f"(spent ${b.spent_today:.2f})", "budget")
        if "session5h" in caps:
            lim = caps["session5h"].get("limit_usd")
            if lim and b.session_active and b.session_spent >= float(lim):
                return Decision(
                    "deny",
                    f"5h-session cap ${float(lim):.2f} reached "
                    f"(spent ${b.session_spent:.2f})", "budget")
        return None

    # ---- static decision (everything except the ask wait) -------------
    def decide_static(self, n: dict) -> Decision:
        if n["event"] not in GATED_EVENTS:
            return Decision("allow", "", "ungated")
        if self.panic:
            return Decision("deny", "Panic: all agents halted.", "panic")
        cap = self.check_caps(n)
        if cap:
            return cap
        pol = self.evaluate_policies(n)
        if pol:
            return pol
        sess = self.store.get_session(n["sid"]) or {}
        mode = sess.get("mode", "autopilot")
        if mode == "supervise":
            return Decision("ask", "awaiting your verdict", "mode")
        # autopilot: configurable default, default allow
        default = str(self._setting("autopilot_default", "allow"))
        if default == "deny":
            return Decision("deny", "autopilot default-deny", "mode")
        return Decision("allow", "", "mode")

    def failsafe(self, n: dict) -> Decision:
        """When supervise times out / core path unavailable."""
        ov = self.store.get_setting("failsafe_overrides", "") or ""
        forced = dict(
            kv.split(":") for kv in ov.split(",") if ":" in kv
        ) if ov else {}
        t = n["tool"]
        if t in forced:
            act = forced[t]
            return Decision(act if act in ("allow", "deny") else "deny",
                            "fail-safe override", "failsafe")
        if tool_is_dangerous(t):
            return Decision("deny",
                            "supervisor unavailable (fail-closed)",
                            "failsafe")
        return Decision("allow", "supervisor unavailable (fail-open)",
                        "failsafe")

    # ---- gate registry (async, used by server) ------------------------
    def new_gate(self, n: dict) -> Gate:
        self._seq += 1
        gid = f"g{int(time.time())}_{self._seq}"
        g = Gate(gid=gid, sid=n["sid"], tool=n["tool"], event=n["event"],
                 summary=n["summary"], created=time.time())
        self.gates[gid] = g
        return g

    def resolve_gate(self, gid: str, action: str, reason: str) -> bool:
        g = self.gates.get(gid)
        if not g or not g.fut or g.fut.done():
            return False
        g.fut.set_result(Decision(action, reason or "user verdict", "user"))
        return True

    def pending(self) -> list:
        return [
            {"gid": g.gid, "sid": g.sid, "tool": g.tool,
             "event": g.event, "summary": g.summary,
             "age": round(time.time() - g.created, 1)}
            for g in self.gates.values()
            if g.fut and not g.fut.done()
        ]

    # ---- bookkeeping ---------------------------------------------------
    def record(self, n: dict, d: Decision):
        ts = utcnow_iso()
        if n["event"] in ("SessionStart",):
            self.store.upsert_session(n["sid"], "claude_code", n["cwd"], ts)
        elif n["event"] in TERMINAL_EVENTS:
            self.store.set_session_status(n["sid"], "ended", ts)
        else:
            # touch session so it shows up even without an explicit start
            if not self.store.get_session(n["sid"]):
                self.store.upsert_session(
                    n["sid"], "claude_code", n["cwd"], ts)
            else:
                self.store.upsert_session(
                    n["sid"], "claude_code", n["cwd"], ts)
        if n["event"] in GATED_EVENTS or d.by in ("panic", "budget"):
            self.store.add_event(n["sid"], ts, n["event"], n["tool"],
                                 n["summary"], d.action, d.reason, d.by)
