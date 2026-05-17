"""Model pricing: LiteLLM JSON (cached) with a hardcoded fallback table.

Prices are stored as USD per single token (LiteLLM convention) so cost is a
plain multiply. Public helpers take per-million numbers where convenient.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from . import config

LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
CACHE_TTL_SECONDS = 24 * 3600


def _pm(input_, output, cache_write, cache_read):
    """Per-million USD -> per-token dict."""
    return {
        "input_cost_per_token": input_ / 1e6,
        "output_cost_per_token": output / 1e6,
        "cache_creation_input_token_cost": (cache_write / 1e6) if cache_write is not None else 0.0,
        "cache_read_input_token_cost": (cache_read / 1e6) if cache_read is not None else 0.0,
    }


# Fallback table (USD / 1M tokens). Source of truth is the LiteLLM JSON; this
# only applies when offline on first run.
FALLBACK: dict[str, dict] = {
    "claude-opus-4": _pm(15.0, 75.0, 18.75, 1.50),
    "claude-sonnet-4.5": _pm(3.0, 15.0, 3.75, 0.30),
    "claude-sonnet-4": _pm(3.0, 15.0, 3.75, 0.30),
    "claude-haiku-4.5": _pm(1.0, 5.0, 1.25, 0.10),
    "claude-3-5-haiku": _pm(0.80, 4.0, 1.0, 0.08),
    "claude-3-5-sonnet": _pm(3.0, 15.0, 3.75, 0.30),
    "gpt-4o": _pm(2.50, 10.0, None, 1.25),
    "gpt-4o-mini": _pm(0.15, 0.60, None, 0.075),
    "gpt-4.1": _pm(2.0, 8.0, None, 0.50),
    "o1": _pm(15.0, 60.0, None, 7.50),
}


@dataclass
class Cost:
    usd: float
    matched_model: Optional[str]
    priced: bool  # False => no price found, usd is 0


class PricingTable:
    def __init__(self, table: dict[str, dict], source: str):
        self._table = table
        self.source = source  # "litellm" | "cache" | "fallback"

    # ---- loading -------------------------------------------------------
    @classmethod
    def load(cls) -> "PricingTable":
        cache = config.pricing_cache_path()
        # Try fresh remote first; fall back to cache, then hardcoded.
        try:
            r = httpx.get(LITELLM_URL, timeout=10.0)
            r.raise_for_status()
            data = r.json()
            cache.write_text(
                json.dumps({"fetched_at": time.time(), "data": data}),
                encoding="utf-8",
            )
            return cls(data, "litellm")
        except Exception:
            pass
        try:
            blob = json.loads(cache.read_text(encoding="utf-8"))
            return cls(blob["data"], "cache")
        except Exception:
            return cls(FALLBACK, "fallback")

    # ---- lookup --------------------------------------------------------
    def _find(self, model: str) -> Optional[dict]:
        if not model:
            return None
        if model in self._table:
            return self._table[model]
        # Strip common provider prefixes / date suffixes, try best substring.
        cand = model.split("/")[-1]
        if cand in self._table:
            return self._table[cand]
        # longest key that is a prefix of the model name
        best_key = None
        for key in self._table:
            if cand.startswith(key) or model.startswith(key):
                if best_key is None or len(key) > len(best_key):
                    best_key = key
        if best_key:
            return self._table[best_key]
        # loose: any key contained in the model id
        for key in self._table:
            if key in model:
                return self._table[key]
        return None

    def cost(
        self,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_write_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> Cost:
        entry = self._find(model)
        if not entry:
            return Cost(0.0, None, False)
        usd = (
            input_tokens * entry.get("input_cost_per_token", 0.0)
            + output_tokens * entry.get("output_cost_per_token", 0.0)
            + cache_write_tokens
            * entry.get("cache_creation_input_token_cost", 0.0)
            + cache_read_tokens * entry.get("cache_read_input_token_cost", 0.0)
        )
        return Cost(round(usd, 8), model, True)
