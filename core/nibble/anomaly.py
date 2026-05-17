"""Spend-anomaly detection.

Compares the cost of the just-ingested batch (and today's running total) to a
trailing daily baseline. Flags a spike when today's spend at this point in the
day is well above the recent norm. Intentionally simple and explainable —
this drives the creature's "shocked" reaction and the optional LLM comment.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Optional

from .store import Store

SPIKE_FACTOR = 3.0
MIN_ABS_USD = 1.0  # ignore tiny absolute amounts


@dataclass
class Anomaly:
    detected: bool
    factor: float
    spent_today: float
    baseline: float
    reason: str


def detect(store: Store, spent_today: float) -> Optional[Anomaly]:
    rows = store.daily_costs(days=15)
    # rows[0] is today (partial); use prior complete days as baseline.
    prior = [r["cost"] for r in rows[1:] if r["cost"] is not None]
    prior = [c for c in prior if c > 0]
    if len(prior) < 3 or spent_today < MIN_ABS_USD:
        return None
    baseline = mean(prior[:7]) if prior else 0.0
    if baseline <= 0:
        return None
    factor = spent_today / baseline
    if factor >= SPIKE_FACTOR:
        return Anomaly(
            detected=True,
            factor=round(factor, 1),
            spent_today=round(spent_today, 2),
            baseline=round(baseline, 2),
            reason=(
                f"Today's ${spent_today:,.2f} is {factor:.1f}x your recent "
                f"daily average of ${baseline:,.2f}."
            ),
        )
    return None
