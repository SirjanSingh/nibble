"""Anthropic Organization usage collector.

Uses GET /v1/organizations/usage_report/messages which requires an Admin API
key (sk-ant-admin...). Degrades silently when no key. Dedup key =
anthropic:<bucket_start>:<model>:<service_tier>.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

import httpx

from .. import config, secrets
from ..pricing import PricingTable
from ..store import Record, Store

URL = "https://api.anthropic.com/v1/organizations/usage_report/messages"
CURSOR_META = "anthropic_cursor"


class AnthropicCollector:
    name = "anthropic"
    kind = "remote"

    def available(self) -> tuple[bool, str]:
        if secrets.has_key(config.KEY_ANTHROPIC):
            return True, "ok"
        return False, "no Anthropic admin key configured"

    def collect(self, store: Store, pricing: PricingTable) -> Iterable[Record]:
        key = secrets.get_key(config.KEY_ANTHROPIC)
        if not key:
            return []

        last = store.get_meta(CURSOR_META)
        if last:
            starting_at = last
        else:
            starting_at = (
                datetime.now(timezone.utc) - timedelta(days=1)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
        out: list[Record] = []
        page = None
        latest_ts = starting_at
        for _ in range(20):
            params = {
                "starting_at": starting_at,
                "bucket_width": "1d",
                "group_by[]": ["model", "service_tier"],
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
                b_start = bucket.get("starting_at", starting_at)
                if b_start > latest_ts:
                    latest_ts = b_start
                ts = b_start
                for res in bucket.get("results", []):
                    model = res.get("model") or "anthropic-unknown"
                    tier = res.get("service_tier") or "standard"
                    it = int(res.get("uncached_input_tokens", 0) or 0)
                    ot = int(res.get("output_tokens", 0) or 0)
                    cw = int(res.get("cache_creation_input_tokens", 0) or 0)
                    cr = int(res.get("cache_read_input_tokens", 0) or 0)
                    c = pricing.cost(model, it, ot, cw, cr)
                    out.append(Record(
                        ts_utc=ts,
                        tool="anthropic",
                        model=model,
                        input_tokens=it,
                        output_tokens=ot,
                        cache_write_tokens=cw,
                        cache_read_tokens=cr,
                        cost_usd=c.usd,
                        priced=c.priced,
                        source_key="api",
                        raw_hash=f"anthropic:{b_start}:{model}:{tier}",
                    ))
            if body.get("has_more") and body.get("next_page"):
                page = body["next_page"]
            else:
                break

        # back off one day to re-capture the partial current day
        try:
            dt = datetime.strptime(latest_ts, "%Y-%m-%dT%H:%M:%SZ")
            store.set_meta(
                CURSOR_META,
                (dt - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        except ValueError:
            store.set_meta(CURSOR_META, starting_at)
        return out
