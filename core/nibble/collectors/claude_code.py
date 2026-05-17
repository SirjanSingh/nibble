"""Claude Code collector — incrementally tails ~/.claude/projects/**/*.jsonl.

Reads only the newly-appended bytes of each file since last poll (offset
tracked in meta). Dedupe key = message.id:requestId (ccusage convention),
enforced again by the UNIQUE raw_hash column.
"""
from __future__ import annotations

import json
from typing import Iterable

from .. import config
from ..pricing import PricingTable
from ..store import Record, Store

OFFSETS_META = "cc_offsets"


class ClaudeCodeCollector:
    name = "claude_code"
    kind = "local"

    def available(self) -> tuple[bool, str]:
        d = config.claude_projects_dir()
        if d.exists():
            return True, "ok"
        return False, f"no Claude Code logs at {d}"

    def collect(self, store: Store, pricing: PricingTable) -> Iterable[Record]:
        root = config.claude_projects_dir()
        if not root.exists():
            return []
        try:
            offsets = json.loads(store.get_meta(OFFSETS_META, "{}"))
        except Exception:
            offsets = {}

        out: list[Record] = []
        for path in root.rglob("*.jsonl"):
            key = str(path)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            start = int(offsets.get(key, 0))
            if start > size:  # file truncated/rotated
                start = 0
            if start == size:
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(start)
                    for line in fh:
                        rec = self._parse_line(line, pricing)
                        if rec:
                            out.append(rec)
                offsets[key] = size
            except OSError:
                continue

        store.set_meta(OFFSETS_META, json.dumps(offsets))
        return out

    @staticmethod
    def _parse_line(line: str, pricing: PricingTable) -> Record | None:
        line = line.strip()
        if not line:
            return None
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            return None
        if o.get("type") != "assistant":
            return None
        msg = o.get("message") or {}
        usage = msg.get("usage") or {}
        model = msg.get("model") or ""
        if not usage or model in ("<synthetic>", ""):
            return None

        msg_id = msg.get("id") or ""
        req_id = o.get("requestId") or ""
        if not msg_id and not req_id:
            return None
        raw_hash = f"claude:{msg_id}:{req_id}"

        it = int(usage.get("input_tokens", 0) or 0)
        ot = int(usage.get("output_tokens", 0) or 0)
        cw = int(usage.get("cache_creation_input_tokens", 0) or 0)
        cr = int(usage.get("cache_read_input_tokens", 0) or 0)

        embedded = o.get("costUSD")
        if isinstance(embedded, (int, float)):
            cost, priced = float(embedded), True
        else:
            c = pricing.cost(model, it, ot, cw, cr)
            cost, priced = c.usd, c.priced

        ts = o.get("timestamp") or ""
        # normalize ...Z / ...+00:00 to compact Z form for lexical compare
        if ts.endswith("Z") and "." in ts:
            ts = ts.split(".")[0] + "Z"

        return Record(
            ts_utc=ts,
            tool="claude_code",
            model=model,
            input_tokens=it,
            output_tokens=ot,
            cache_write_tokens=cw,
            cache_read_tokens=cr,
            cost_usd=cost,
            priced=priced,
            source_key="local",
            raw_hash=raw_hash,
        )
