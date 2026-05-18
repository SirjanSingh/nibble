"""SQLite storage with WAL mode. Single-writer, simple upserts."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    tool TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    priced INTEGER DEFAULT 1,
    source_key TEXT,
    raw_hash TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts_utc);
CREATE INDEX IF NOT EXISTS idx_usage_tool ON usage(tool);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sessions (
    sid TEXT PRIMARY KEY,
    source TEXT, cwd TEXT,
    started_at TEXT, last_seen TEXT,
    mode TEXT DEFAULT 'autopilot',
    status TEXT DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sid TEXT, ts TEXT, event TEXT, tool TEXT,
    summary TEXT, decision TEXT, reason TEXT, decided_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idx INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    label TEXT,
    match_json TEXT,
    action TEXT,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS caps (
    scope TEXT PRIMARY KEY,
    limit_usd REAL, limit_tokens INTEGER
);
"""


@dataclass
class Record:
    ts_utc: str
    tool: str
    model: Optional[str]
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    priced: bool = True
    source_key: Optional[str] = None
    raw_hash: str = ""


class Store:
    def __init__(self, path=None):
        self.path = str(path or config.db_path())
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        with self._lock:
            self._conn.executescript(SCHEMA)

    @contextmanager
    def _cur(self):
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    # ---- writes --------------------------------------------------------
    def upsert_many(self, records: Iterable[Record]) -> int:
        inserted = 0
        with self._cur() as cur:
            for r in records:
                cur.execute(
                    """INSERT OR IGNORE INTO usage
                    (ts_utc,tool,model,input_tokens,output_tokens,
                     cache_write_tokens,cache_read_tokens,cost_usd,priced,
                     source_key,raw_hash)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        r.ts_utc, r.tool, r.model, r.input_tokens,
                        r.output_tokens, r.cache_write_tokens,
                        r.cache_read_tokens, r.cost_usd,
                        1 if r.priced else 0, r.source_key, r.raw_hash,
                    ),
                )
                inserted += cur.rowcount
        return inserted

    # ---- meta / settings ----------------------------------------------
    def get_meta(self, key: str, default=None):
        with self._cur() as cur:
            row = cur.execute(
                "SELECT value FROM meta WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str):
        with self._cur() as cur:
            cur.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_setting(self, key: str, default=None):
        with self._cur() as cur:
            row = cur.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self._cur() as cur:
            cur.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    # ---- reads ---------------------------------------------------------
    def totals_since(self, since_utc_iso: str):
        with self._cur() as cur:
            rows = cur.execute(
                """SELECT tool, COUNT(*) n, SUM(cost_usd) cost,
                          SUM(input_tokens+output_tokens) tokens
                   FROM usage WHERE ts_utc >= ?
                   GROUP BY tool ORDER BY cost DESC""",
                (since_utc_iso,),
            ).fetchall()
        return [dict(r) for r in rows]

    def total_cost_since(self, since_utc_iso: str) -> float:
        with self._cur() as cur:
            row = cur.execute(
                "SELECT COALESCE(SUM(cost_usd),0) c FROM usage WHERE ts_utc>=?",
                (since_utc_iso,),
            ).fetchone()
        return float(row["c"])

    def first_ts_since(self, since_utc_iso: str, tool: str | None = None):
        q = "SELECT MIN(ts_utc) m FROM usage WHERE ts_utc >= ?"
        args = [since_utc_iso]
        if tool:
            q += " AND tool = ?"
            args.append(tool)
        with self._cur() as cur:
            row = cur.execute(q, args).fetchone()
        return row["m"] if row and row["m"] else None

    def window_summary(self, since_utc_iso: str,
                       tool: str | None = None) -> dict:
        q = ("""SELECT COALESCE(SUM(cost_usd),0) cost,
                       COALESCE(SUM(input_tokens+output_tokens),0) tokens,
                       COUNT(*) n
                FROM usage WHERE ts_utc >= ?""")
        args = [since_utc_iso]
        if tool:
            q += " AND tool = ?"
            args.append(tool)
        with self._cur() as cur:
            row = cur.execute(q, args).fetchone()
        return {"cost": float(row["cost"]), "tokens": int(row["tokens"]),
                "requests": int(row["n"])}

    def costs_rows_since(self, since_utc_iso: str):
        """Raw (ts_utc, cost) since a bound, for local-day bucketing."""
        with self._cur() as cur:
            rows = cur.execute(
                "SELECT ts_utc, cost_usd FROM usage WHERE ts_utc >= ?",
                (since_utc_iso,),
            ).fetchall()
        return [(r["ts_utc"], float(r["cost_usd"])) for r in rows]

    def tool_models_since(self, tool: str, since_utc_iso: str):
        with self._cur() as cur:
            rows = cur.execute(
                """SELECT model, COUNT(*) n,
                          SUM(cost_usd) cost,
                          SUM(input_tokens+output_tokens) tokens
                   FROM usage
                   WHERE tool=? AND ts_utc >= ?
                   GROUP BY model ORDER BY cost DESC""",
                (tool, since_utc_iso),
            ).fetchall()
        return [dict(r) for r in rows]

    def daily_costs(self, days: int = 14):
        with self._cur() as cur:
            rows = cur.execute(
                """SELECT substr(ts_utc,1,10) d, SUM(cost_usd) cost
                   FROM usage GROUP BY d ORDER BY d DESC LIMIT ?""",
                (days,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- governor: sessions / events / policies / caps -----------------
    def upsert_session(self, sid, source, cwd, ts):
        with self._cur() as cur:
            cur.execute(
                """INSERT INTO sessions(sid,source,cwd,started_at,last_seen)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(sid) DO UPDATE SET last_seen=excluded.last_seen,
                     status='active'""",
                (sid, source, cwd, ts, ts),
            )

    def set_session_mode(self, sid, mode):
        with self._cur() as cur:
            cur.execute("UPDATE sessions SET mode=? WHERE sid=?", (mode, sid))

    def set_session_status(self, sid, status, ts):
        with self._cur() as cur:
            cur.execute(
                "UPDATE sessions SET status=?, last_seen=? WHERE sid=?",
                (status, ts, sid),
            )

    def get_session(self, sid):
        with self._cur() as cur:
            r = cur.execute(
                "SELECT * FROM sessions WHERE sid=?", (sid,)
            ).fetchone()
        return dict(r) if r else None

    def list_sessions(self, limit=50):
        with self._cur() as cur:
            rows = cur.execute(
                "SELECT * FROM sessions ORDER BY last_seen DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_event(self, sid, ts, event, tool, summary, decision, reason, by):
        with self._cur() as cur:
            cur.execute(
                """INSERT INTO events
                   (sid,ts,event,tool,summary,decision,reason,decided_by)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (sid, ts, event, tool, summary, decision, reason, by),
            )

    def recent_events(self, limit=40):
        with self._cur() as cur:
            rows = cur.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def list_policies(self):
        with self._cur() as cur:
            rows = cur.execute(
                "SELECT * FROM policies ORDER BY idx ASC, id ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def add_policy(self, label, match_json, action, reason, idx=0):
        with self._cur() as cur:
            cur.execute(
                """INSERT INTO policies(idx,enabled,label,match_json,
                   action,reason) VALUES(?,?,?,?,?,?)""",
                (idx, 1, label, match_json, action, reason),
            )

    def update_policy(self, pid, **f):
        if not f:
            return
        cols = ", ".join(f"{k}=?" for k in f)
        with self._cur() as cur:
            cur.execute(
                f"UPDATE policies SET {cols} WHERE id=?",
                (*f.values(), pid),
            )

    def delete_policy(self, pid):
        with self._cur() as cur:
            cur.execute("DELETE FROM policies WHERE id=?", (pid,))

    def set_cap(self, scope, limit_usd, limit_tokens):
        with self._cur() as cur:
            cur.execute(
                """INSERT INTO caps(scope,limit_usd,limit_tokens)
                   VALUES(?,?,?)
                   ON CONFLICT(scope) DO UPDATE SET
                     limit_usd=excluded.limit_usd,
                     limit_tokens=excluded.limit_tokens""",
                (scope, limit_usd, limit_tokens),
            )

    def list_caps(self):
        with self._cur() as cur:
            rows = cur.execute("SELECT * FROM caps").fetchall()
        return [dict(r) for r in rows]

    def close(self):
        with self._lock:
            self._conn.close()


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
