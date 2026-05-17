"""Background poller: runs collectors, prices, stores, derives state, pushes.

Local collectors run every POLL_LOCAL_SECONDS; remote (API) collectors every
POLL_REMOTE_SECONDS. After each cycle it recomputes the budget state and an
anomaly check, then broadcasts a `state` message over the hub.
"""
from __future__ import annotations

import threading
import time
import traceback

from . import budget as budget_mod
from . import commentary
from .anomaly import detect
from .collectors import (
    AnthropicCollector,
    ClaudeCodeCollector,
    OpenAICollector,
)
from .config import POLL_LOCAL_SECONDS, POLL_REMOTE_SECONDS
from .pricing import PricingTable
from .server import Hub
from .store import Store


class Service:
    def __init__(self, store: Store, hub: Hub):
        self.store = store
        self.hub = hub
        self.pricing = PricingTable.load()
        commentary.bind_store(store)
        self.collectors = [
            ClaudeCodeCollector(),
            OpenAICollector(),
            AnthropicCollector(),
        ]
        self._stop = threading.Event()
        self._last_remote = 0.0
        self._last_anomaly_key = None

    def stop(self):
        self._stop.set()

    def run_forever(self):
        # prime quickly on startup
        self._cycle(include_remote=True)
        while not self._stop.wait(POLL_LOCAL_SECONDS):
            now = time.time()
            include_remote = (now - self._last_remote) >= POLL_REMOTE_SECONDS
            self._cycle(include_remote=include_remote)

    # ------------------------------------------------------------------
    def _cycle(self, include_remote: bool):
        statuses = {}
        total_new = 0
        for c in self.collectors:
            if c.kind == "remote" and not include_remote:
                continue
            ok, msg = c.available()
            statuses[c.name] = {"available": ok, "detail": msg}
            if not ok:
                continue
            try:
                records = list(c.collect(self.store, self.pricing))
                if records:
                    total_new += self.store.upsert_many(records)
            except Exception:
                statuses[c.name] = {
                    "available": False,
                    "detail": "collector error",
                }
                traceback.print_exc()
        if include_remote:
            self._last_remote = time.time()

        self._broadcast_state(statuses, total_new)

    def _broadcast_state(self, statuses, total_new):
        b = budget_mod.compute(self.store)
        line = commentary.template_line(b)

        anom = detect(self.store, b.spent_today)
        if anom and anom.detected:
            b.creature_state = "shocked"
            key = f"{anom.factor}:{round(b.spent_today,1)}"
            if key != self._last_anomaly_key:
                self._last_anomaly_key = key
                llm = commentary.llm_anomaly_line(b, anom, b.per_tool)
                line = llm or commentary.anomaly_line(b, anom)
            else:
                line = commentary.anomaly_line(b, anom)

        msg = {
            "type": "state",
            "creature_state": b.creature_state,
            "headline": b.headline,
            "speech": line,
            "spent_today": b.spent_today,
            "daily_budget": b.daily_budget,
            "pct_used": b.pct_used,
            "projected_eod": b.projected_eod,
            "on_track": b.on_track,
            "per_tool": b.per_tool,
            "sources": statuses,
            "pricing_source": self.pricing.source,
            "new_records": total_new,
            "ts": time.time(),
        }
        self.hub.broadcast_threadsafe(msg)


def start_in_thread(store: Store, hub: Hub) -> Service:
    svc = Service(store, hub)
    t = threading.Thread(target=svc.run_forever, daemon=True, name="nibble-svc")
    t.start()
    return svc
