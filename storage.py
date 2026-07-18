"""
ATLAS Dual Storage Layer — Part B (D1 primary + Supabase overflow)
Version: 1.0

WHAT THIS DOES
==============
A single storage abstraction so bot.py / exam_server.py can call ONE set of
functions and the data is transparently written to:

  1) Cloudflare D1  (PRIMARY)   — fast, generous free tier (~5 GB)
  2) Supabase       (OVERFLOW)  — used automatically when D1 is "full"

Both are kept in sync for critical tables. When EITHER store approaches its
size cap, the OLDEST rows are auto-deleted to free space (FIFO), so the bot
never hard-fails on a full database.

HOW IT TALKS TO D1
==================
HF Space cannot bind to D1 directly. It talks to D1 THROUGH your existing
Cloudflare Worker (atlas-mcq-worker) which already has the D1 binding.
This module sends SQL to the worker over HTTPS:

    POST {CF_D1_URL}/d1/query   {"sql": "...", "params": [...]}
    ->  {"ok": true, "results": [...], "meta": {...}}

You must add a small /d1/query route to the worker (see WORKER_SNIPPET at the
bottom of this file) that runs:  env.DB.prepare(sql).bind(...params).all()

CONFIG (env / HF secrets)
=========================
  CF_D1_URL          e.g. https://atlas-mcq-worker.hamza818483.workers.dev
  CF_D1_TOKEN        a shared secret; worker checks header X-D1-Token
  D1_MAX_ROWS        soft cap per big table (default 50000) -> FIFO delete
  SUPABASE_MAX_ROWS  soft cap per big table (default 8000)  -> FIFO delete
  STORAGE_MODE       "d1_primary" (default) | "supabase_primary" | "mirror"

SAFE BY DESIGN
==============
- Every function is best-effort and never raises to the caller.
- If D1 is unreachable, it falls back to Supabase automatically.
- Designed to be imported lazily; if you don't configure CF_D1_URL, the whole
  module no-ops on the D1 side and behaves like plain Supabase.

INTEGRATION (later, in a focused session)
=========================================
  from storage import dual_save_mcq, dual_get_mcq, enforce_quotas
  - replace save_mcq()/get_mcq() internals to call these
  - run enforce_quotas() inside the daily scheduler
"""

import os
import json
import httpx
from datetime import datetime, timedelta, timezone

try:
    BD_TZ = timezone(timedelta(hours=6))
except Exception:
    BD_TZ = timezone(timedelta(hours=6))

CF_D1_URL = os.getenv("CF_D1_URL", "").rstrip("/")
CF_D1_TOKEN = os.getenv("CF_D1_TOKEN", "")
D1_MAX_ROWS = int(os.getenv("D1_MAX_ROWS", "50000"))
SUPABASE_MAX_ROWS = int(os.getenv("SUPABASE_MAX_ROWS", "8000"))
STORAGE_MODE = os.getenv("STORAGE_MODE", "d1_primary")

# Big tables that should be quota-managed (FIFO oldest-delete when full)
MANAGED_TABLES = ["mcqs", "results", "bookmarks", "mistakes"]

# ------------------------------------------------------------
# D1 transport (via Cloudflare Worker)
# ------------------------------------------------------------

def d1_enabled() -> bool:
    return bool(CF_D1_URL and CF_D1_TOKEN)

async def d1_query(sql: str, params=None) -> dict:
    """Run SQL on D1 through the worker. Returns {} on any failure."""
    if not d1_enabled():
        return {}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{CF_D1_URL}/d1/query",
                json={"sql": sql, "params": params or []},
                headers={"X-D1-Token": CF_D1_TOKEN, "Content-Type": "application/json"},
            )
            if r.status_code == 200:
                return r.json()
            return {}
    except Exception as e:
        print(f"[storage] d1_query failed: {e}")
        return {}

async def d1_count(table: str) -> int:
    res = await d1_query(f"SELECT COUNT(*) AS n FROM {table}")
    try:
        return int(res.get("results", [{}])[0].get("n", 0))
    except Exception:
        return 0

# ------------------------------------------------------------
# Supabase transport (sync client passed in from caller)
# ------------------------------------------------------------
# To avoid duplicate client setup, caller injects its get_supabase() once.
_supabase_getter = None

def bind_supabase(getter_fn):
    """Call once at startup: bind_supabase(get_supabase)."""
    global _supabase_getter
    _supabase_getter = getter_fn

def _sb():
    return _supabase_getter() if _supabase_getter else None

def sb_count(table: str) -> int:
    try:
        c = _sb()
        if not c:
            return 0
        res = c.table(table).select("*", count="exact").limit(1).execute()
        return res.count or 0
    except Exception:
        return 0

# ------------------------------------------------------------
# Dual write / read
# ------------------------------------------------------------

async def dual_insert(table: str, row: dict) -> bool:
    """Insert a row into the primary store; mirror to the other.
    Order depends on STORAGE_MODE. Never raises."""
    primary_ok = False
    # Build a parameterized INSERT for D1
    cols = list(row.keys())
    placeholders = ",".join(["?"] * len(cols))
    col_sql = ",".join(cols)
    vals = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in row.values()]
    sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"

    async def _write_d1():
        res = await d1_query(sql, vals)
        return bool(res.get("ok", res != {}))

    def _write_sb():
        try:
            c = _sb()
            if c:
                c.table(table).insert(row).execute()
                return True
        except Exception as e:
            print(f"[storage] sb insert failed: {e}")
        return False

    if STORAGE_MODE == "supabase_primary":
        primary_ok = _write_sb()
        await _write_d1()
    else:  # d1_primary or mirror
        primary_ok = await _write_d1()
        if STORAGE_MODE == "mirror" or not primary_ok:
            _write_sb()
    return primary_ok

async def dual_get_mcq(quiz_id: str):
    """Read a quiz from D1 first, then Supabase. Returns row dict or None."""
    if d1_enabled() and STORAGE_MODE != "supabase_primary":
        res = await d1_query("SELECT * FROM mcqs WHERE quiz_id=? LIMIT 1", [quiz_id])
        rows = res.get("results", [])
        if rows:
            return rows[0]
    # fallback Supabase
    try:
        c = _sb()
        if c:
            r = c.table("mcqs").select("*").eq("quiz_id", quiz_id).execute()
            if r.data:
                return r.data[0]
    except Exception:
        pass
    return None

# ------------------------------------------------------------
# Quota enforcement — FIFO oldest-delete when a store is "full"
# ------------------------------------------------------------

async def enforce_quotas() -> dict:
    """For each managed table, if a store exceeds its cap, delete the OLDEST
    rows to bring it back under cap. Returns a small report dict.
    Call this from the daily scheduler (and optionally after big inserts)."""
    report = {}
    for table in MANAGED_TABLES:
        info = {"d1": None, "supabase": None}
        # ---- D1 ----
        if d1_enabled():
            n = await d1_count(table)
            info["d1"] = n
            if n > D1_MAX_ROWS:
                excess = n - D1_MAX_ROWS + 100  # delete a little extra to avoid thrashing
                # delete oldest by created_at (FIFO). Requires created_at column + rowid.
                await d1_query(
                    f"DELETE FROM {table} WHERE rowid IN "
                    f"(SELECT rowid FROM {table} ORDER BY created_at ASC LIMIT ?)",
                    [excess],
                )
                info["d1_deleted"] = excess
        # ---- Supabase ----
        n_sb = sb_count(table)
        info["supabase"] = n_sb
        if n_sb > SUPABASE_MAX_ROWS:
            try:
                c = _sb()
                if c:
                    excess = n_sb - SUPABASE_MAX_ROWS + 100
                    oldest = c.table(table).select("id,created_at").order(
                        "created_at", desc=False).limit(excess).execute().data or []
                    ids = [r["id"] for r in oldest if "id" in r]
                    if ids:
                        c.table(table).delete().in_("id", ids).execute()
                        info["supabase_deleted"] = len(ids)
            except Exception as e:
                print(f"[storage] sb quota delete failed ({table}): {e}")
        report[table] = info
    return report

# ------------------------------------------------------------
# D1 schema bootstrap (run once) — mirrors Supabase tables
# ------------------------------------------------------------
D1_SCHEMA = """
CREATE TABLE IF NOT EXISTS mcqs (
  quiz_id TEXT PRIMARY KEY, user_id INTEGER, mcqs TEXT, source_type TEXT,
  prompt_type TEXT, image_file_id TEXT, chat_id INTEGER, message_id INTEGER,
  source_hash TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mcqs_hash ON mcqs(source_hash);
CREATE INDEX IF NOT EXISTS idx_mcqs_user ON mcqs(user_id);
CREATE INDEX IF NOT EXISTS idx_mcqs_created ON mcqs(created_at);

CREATE TABLE IF NOT EXISTS results (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, quiz_id TEXT,
  quiz_name TEXT, total INTEGER, correct INTEGER, wrong INTEGER, skipped INTEGER,
  time_taken INTEGER, mark REAL, negative_mark REAL, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_results_user ON results(user_id);
CREATE INDEX IF NOT EXISTS idx_results_created ON results(created_at);

CREATE TABLE IF NOT EXISTS bookmarks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, cache_id TEXT,
  question_index INTEGER, question_data TEXT, topic TEXT, page INTEGER, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_bookmarks_user ON bookmarks(user_id);

CREATE TABLE IF NOT EXISTS mistakes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, quiz_id TEXT,
  question_data TEXT, status TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mistakes_user ON mistakes(user_id);

CREATE TABLE IF NOT EXISTS menu_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER NOT NULL DEFAULT 0,
  name TEXT NOT NULL, csv_data TEXT, created_by INTEGER, created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_menu_items_parent ON menu_items(parent_id);
"""

async def bootstrap_d1_schema():
    if not d1_enabled():
        return
    for stmt in [s.strip() for s in D1_SCHEMA.split(";") if s.strip()]:
        await d1_query(stmt)
    print("[storage] D1 schema bootstrapped")

# ============================================================
# WORKER_SNIPPET — add this route to atlas-mcq-worker (index.js)
# ============================================================
WORKER_SNIPPET = r'''
// --- Add inside your fetch() handler, before existing routes ---
if (url.pathname === "/d1/query" && request.method === "POST") {
  const token = request.headers.get("X-D1-Token") || "";
  if (token !== env.D1_TOKEN) {
    return new Response("Unauthorized", { status: 401 });
  }
  try {
    const { sql, params } = await request.json();
    const stmt = env.DB.prepare(sql);
    const bound = (params && params.length) ? stmt.bind(...params) : stmt;
    const result = await bound.all();
    return new Response(JSON.stringify({
      ok: true,
      results: result.results || [],
      meta: result.meta || {}
    }), { headers: { "Content-Type": "application/json" } });
  } catch (e) {
    return new Response(JSON.stringify({ ok: false, error: String(e) }), {
      status: 500, headers: { "Content-Type": "application/json" }
    });
  }
}
// Requires in wrangler.toml:
//   [[d1_databases]]
//   binding = "DB"
//   database_name = "atlas-db"
//   database_id = "9b866cf1-d4e8-440c-a2f8-33f2b826e383"
// And a secret:  wrangler secret put D1_TOKEN   (same value as CF_D1_TOKEN)
'''
