"""OpenAI Organization usage collector.

Uses GET /v1/organization/usage/completions which requires an ORG ADMIN key
(normal sk- project keys are rejected). Degrades silently when no key.
Dedup key = openai:<bucket_start>:<model>:<api_key_id>.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Iterable

import httpx

from .. import config, secrets
from ..pricing import PricingTable
from ..store import Record, Store

URL = "https://api.openai.com/v1/organization/usage/completions"
CURSOR_META = "openai_cursor"


class OpenAICollector:
    name = "openai"
    kind = "remote"

    def available(self) -> tuple[bool, str]:
        if secrets.has_key(config.KEY_OPENAI):
            return True, "ok"
        return False, "no OpenAI admin key configured"

    def collect(self, store: Store, pricing: PricingTable) -> Iterable[Record]:
        key = secrets.get_key(config.KEY_OPENAI)
        if not key:
            return []
        # Resume from last seen bucket, else last 24h.
        last = store.get_meta(CURSOR_META)
        start_time = int(last) if last else int(time.time()) - 86400

        out: list[Record] = []
        page = None
        headers = {"Authorization": f"Bearer {key}"}
        max_bucket = start_time
        for _ in range(20):  # page cap
            params = {
                "start_time": start_time,
                "bucket_width": "1d",
                "group_by": "model",
                "limit": 31,
            }
            if page:
                params["page"] = page
            try:
                r = httpx.get(URL, headers=headers, params=params, timeout=20.0)
                r.raise_for_status()
            except httpx.HTTPError:
                break
            body = r.json()
            for bucket in body.get("data", []):
                b_start = bucket.get("start_time", start_time)
                max_bucket = max(max_bucket, int(b_start))
                ts = datetime.fromtimestamp(
                    int(b_start), tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
                for res in bucket.get("results", []):
                    model = res.get("model") or "openai-unknown"
                    it = int(res.get("input_tokens", 0) or 0)
                    ot = int(res.get("output_tokens", 0) or 0)
                    cr = int(res.get("input_cached_tokens", 0) or 0)
                    akid = res.get("api_key_id") or "org"
                    c = pricing.cost(model, it, ot, 0, cr)
                    out.append(Record(
                        ts_utc=ts,
                        tool="openai",
                        model=model,
                        input_tokens=it,
                        output_tokens=ot,
                        cache_read_tokens=cr,
                        cost_usd=c.usd,
                        priced=c.priced,
                        source_key="api",
                        raw_hash=f"openai:{b_start}:{model}:{akid}",
                    ))
            if body.get("has_more") and body.get("next_page"):
                page = body["next_page"]
            else:
                break

        # Re-poll the most recent (possibly partial) day next time.
        store.set_meta(CURSOR_META, str(max(start_time, max_bucket - 86400)))
        return out
