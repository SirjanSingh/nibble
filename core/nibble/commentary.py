"""Creature speech-bubble lines.

Default path is offline rule-based templates (free, private). For a flagged
anomaly, and only if the user opted in and supplied a commentary key, make a
single tiny Anthropic Haiku call to produce one sharper line. Failure falls
back to the template silently.
"""
from __future__ import annotations

import json
import random

import httpx

from . import config, secrets
from .anomaly import Anomaly
from .budget import BudgetState

_IDLE = [
    "Tracking every token so you don't have to.",
    "All quiet on the spend front.",
    "I nibble your usage logs, not your wallet.",
]
_HAPPY = [
    "Frugal day — your wallet says thanks.",
    "Well under budget. Treat yourself (responsibly).",
]
_ALERT = [
    "Heads up — spend is climbing faster than usual.",
    "You're burning through today's budget early.",
]
_SHOCKED = [
    "Whoa. That escalated quickly.",
    "Budget? We knew her.",
]


def template_line(b: BudgetState) -> str:
    if b.creature_state == "happy":
        return random.choice(_HAPPY)
    if b.creature_state == "alert":
        return (
            f"{b.pct_used:.0f}% of budget used, on pace for "
            f"${b.projected_eod:,.2f}."
        )
    if b.creature_state == "shocked":
        return random.choice(_SHOCKED)
    if b.creature_state == "sleeping":
        return "Zzz… no AI spend yet today."
    if b.creature_state == "reconnecting":
        return "Reconnecting to the core…"
    return random.choice(_IDLE)


def anomaly_line(b: BudgetState, a: Anomaly) -> str:
    """Template fallback for an anomaly (no network)."""
    return a.reason + " Worth a look at what changed."


def llm_anomaly_line(b: BudgetState, a: Anomaly, per_tool: list) -> str | None:
    """Optional single Haiku call. Returns None on any problem."""
    if str(store_opt("commentary_enabled")) not in ("1", "true", "True"):
        return None
    key = secrets.get_key(config.KEY_ANTHROPIC_COMMENTARY)
    if not key:
        return None
    breakdown = ", ".join(
        f"{t['tool']} ${t['cost']:.2f}" for t in per_tool if t.get("cost")
    )
    prompt = (
        "You are a terse, witty desktop creature that comments on a "
        "developer's AI spending. One sentence, under 22 words, no emoji, "
        "concrete and useful. Data: "
        f"spent today ${b.spent_today:.2f}, budget ${b.daily_budget:.2f}, "
        f"recent daily avg ${a.baseline:.2f} ({a.factor}x spike). "
        f"By tool: {breakdown or 'n/a'}. Say what likely drove the spike "
        "and what to check."
    )
    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            content=json.dumps({
                "model": "claude-haiku-4-5",
                "max_tokens": 60,
                "messages": [{"role": "user", "content": prompt}],
            }),
            timeout=15.0,
        )
        r.raise_for_status()
        blocks = r.json().get("content", [])
        text = "".join(
            b.get("text", "") for b in blocks if b.get("type") == "text"
        ).strip()
        return text or None
    except Exception:
        return None


# late-bound store accessor so this module stays import-light for tests
_STORE = None


def bind_store(store):
    global _STORE
    _STORE = store


def store_opt(key, default=""):
    if _STORE is None:
        return default
    return _STORE.get_setting(key, default)
