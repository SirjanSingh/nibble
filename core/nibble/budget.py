"""Budget + rate-of-spend logic and creature-state derivation.

"Today" is the user's local calendar day; records are stored in UTC so we
compute the local-midnight boundary and convert to a UTC ISO string for the
query.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from .store import Store

DEFAULT_DAILY_BUDGET = 10.0


@dataclass
class BudgetState:
    spent_today: float
    daily_budget: float
    pct_used: float
    day_fraction: float        # 0..1 how far through the local day
    projected_eod: float       # naive linear projection of end-of-day spend
    on_track: bool
    creature_state: str
    headline: str
    per_tool: list


def _local_midnight_utc_iso() -> str:
    now_local = datetime.now().astimezone()
    midnight_local = now_local.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return midnight_local.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _day_fraction() -> float:
    now = datetime.now().astimezone()
    secs = now.hour * 3600 + now.minute * 60 + now.second
    return max(0.0001, min(1.0, secs / 86400.0))


def compute(store: Store) -> BudgetState:
    try:
        budget = float(
            store.get_setting("daily_budget", DEFAULT_DAILY_BUDGET)
        )
    except (TypeError, ValueError):
        budget = DEFAULT_DAILY_BUDGET
    if budget <= 0:
        budget = DEFAULT_DAILY_BUDGET

    since = _local_midnight_utc_iso()
    spent = store.total_cost_since(since)
    per_tool = store.totals_since(since)
    frac = _day_fraction()
    pct = (spent / budget) * 100 if budget else 0.0
    projected = spent / frac if frac else spent
    # "on track" if projected end-of-day stays within budget
    on_track = projected <= budget

    state, headline = _derive_state(spent, budget, pct, projected, frac)
    return BudgetState(
        spent_today=round(spent, 4),
        daily_budget=round(budget, 2),
        pct_used=round(pct, 1),
        day_fraction=round(frac, 3),
        projected_eod=round(projected, 2),
        on_track=on_track,
        creature_state=state,
        headline=headline,
        per_tool=per_tool,
    )


def _derive_state(spent, budget, pct, projected, frac) -> tuple[str, str]:
    money = f"${spent:,.2f}"
    if pct >= 100:
        return "shocked", f"Over budget — {money} spent today."
    if pct >= 70:
        return "alert", f"{money} today — {pct:.0f}% of your budget."
    if projected > budget and frac < 0.6:
        return (
            "alert",
            f"{money} by now — on pace for ${projected:,.2f} by midnight.",
        )
    if frac > 0.8 and pct < 60:
        return "happy", f"Only {money} today — nicely under budget."
    if spent <= 0:
        return "sleeping", "No AI spend yet today."
    return "idle", f"{money} spent on AI today."


def as_dict(b: BudgetState) -> dict:
    return asdict(b)
