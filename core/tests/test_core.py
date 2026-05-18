import json
import os
import tempfile

import pytest

from nibble.pricing import FALLBACK, PricingTable
from nibble.store import Store, Record
from nibble.collectors.claude_code import ClaudeCodeCollector


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def test_pricing_cost_basic():
    pt = PricingTable(FALLBACK, "fallback")
    c = pt.cost("claude-opus-4", 1_000_000, 1_000_000, 0, 0)
    assert c.priced
    assert round(c.usd, 2) == round(15.0 + 75.0, 2)


def test_pricing_prefix_match():
    pt = PricingTable(FALLBACK, "fallback")
    c = pt.cost("claude-opus-4-7", 1_000_000, 0, 0, 0)
    assert c.priced and round(c.usd, 2) == 15.0


def test_pricing_unknown_model():
    pt = PricingTable(FALLBACK, "fallback")
    c = pt.cost("totally-unknown-model", 1000, 1000, 0, 0)
    assert not c.priced and c.usd == 0.0


def test_store_dedupe(store):
    r = Record(
        ts_utc="2026-05-18T00:00:00Z", tool="claude_code", model="x",
        cost_usd=1.0, raw_hash="dup",
    )
    assert store.upsert_many([r]) == 1
    assert store.upsert_many([r]) == 0  # UNIQUE raw_hash
    assert store.total_cost_since("2026-05-01T00:00:00Z") == 1.0


def test_claude_parser_reads_usage():
    pt = PricingTable(FALLBACK, "fallback")
    line = json.dumps({
        "type": "assistant",
        "timestamp": "2026-05-18T10:00:00.123Z",
        "requestId": "req_1",
        "message": {
            "id": "msg_1",
            "model": "claude-opus-4-7",
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 200,
            },
        },
    })
    rec = ClaudeCodeCollector._parse_line(line, pt)
    assert rec is not None
    assert rec.tool == "claude_code"
    assert rec.raw_hash == "claude:msg_1:req_1"
    assert rec.input_tokens == 1000 and rec.output_tokens == 500
    assert rec.cost_usd > 0 and rec.priced


def test_claude_parser_skips_non_assistant():
    pt = PricingTable(FALLBACK, "fallback")
    assert ClaudeCodeCollector._parse_line('{"type":"user"}', pt) is None
    assert ClaudeCodeCollector._parse_line("not json", pt) is None
    assert ClaudeCodeCollector._parse_line("", pt) is None


def test_budget_state(store, monkeypatch):
    from nibble import budget as b

    store.set_setting("daily_budget", "10")
    store.upsert_many([
        Record(ts_utc=b._local_midnight_utc_iso(), tool="claude_code",
               model="claude-opus-4", cost_usd=8.0, raw_hash="h1"),
    ])
    st = b.compute(store)
    assert st.spent_today == 8.0
    assert st.daily_budget == 10.0
    assert st.pct_used == 80.0
    assert st.creature_state in ("alert", "shocked")


def test_session_window(store):
    from datetime import datetime, timedelta, timezone
    from nibble import budget as b

    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = (now - timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.upsert_many([
        Record(ts_utc=recent, tool="claude_code", model="claude-opus-4",
               input_tokens=100, output_tokens=50, cost_usd=2.0,
               raw_hash="s_recent"),
        Record(ts_utc=stale, tool="claude_code", model="claude-opus-4",
               cost_usd=9.0, raw_hash="s_stale"),
    ])
    st = b.compute(store)
    assert st.session_active is True
    assert st.session_spent == 2.0           # stale (9h ago) excluded
    assert st.session_tokens == 150
    assert 0 < st.session_resets_in_min <= 5 * 60


def test_local_daily_buckets_today(store):
    from nibble import budget as b

    store.upsert_many([
        Record(ts_utc=b._local_midnight_utc_iso(), tool="claude_code",
               model="claude-opus-4", cost_usd=3.5, raw_hash="ld1"),
    ])
    rows = b.local_daily(store, days=14)
    assert len(rows) == 14
    from datetime import datetime
    today = datetime.now().astimezone().date().isoformat()
    assert rows[0]["d"] == today
    assert rows[0]["cost"] == 3.5


def test_session_is_claude_only(store):
    from datetime import datetime, timedelta, timezone
    from nibble import budget as b

    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    store.upsert_many([
        Record(ts_utc=recent, tool="claude_code", model="claude-opus-4",
               input_tokens=10, output_tokens=10, cost_usd=4.0,
               raw_hash="cs1"),
        Record(ts_utc=recent, tool="openai", model="gpt-4o",
               cost_usd=99.0, raw_hash="cs2"),
    ])
    st = b.compute(store)
    assert st.session_active is True
    assert st.session_spent == 4.0          # openai excluded from 5h session


def test_budget_default_when_unset(store):
    from nibble import budget as b
    st = b.compute(store)
    assert st.daily_budget == b.DEFAULT_DAILY_BUDGET
    assert st.creature_state == "sleeping"
