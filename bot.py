# ============================================================
# ATLAS MCQ BOT - Complete Telegram Bot
# Version: 4.0
# (v3.0 features + Multi-AI Fallback + Cache + /gpa + /bmexam +
#  /error + Share&Challenge + 6h Check-in + Creative PDF buttons +
#  Owner error forwarding + Keep-alive + Backup DB mirror +
#  Option prefix cleanup + /all time+delete + Back-to-Source image)
# ============================================================

# ============================================================
# SECTION 1: IMPORTS
# ============================================================
import asyncio
import json
import time
import traceback
import random
import uuid
import os
import base64
import hashlib
import re
import difflib
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
import csv
from typing import Optional, Dict, List, Tuple, Any

key = os.getenv("GEMINI_KEY", "")
print(f"[STARTUP] GEMINI_KEY loaded: len={len(key)}, prefix={key[:10]}, suffix={key[-4:]}")

# Telegram Bot
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    Poll, BotCommand, BotCommandScopeDefault, MenuButtonDefault
)
from telegram.ext import (
    Application, ApplicationBuilder,
    CommandHandler, MessageHandler, CallbackQueryHandler,
    PollAnswerHandler, filters, ContextTypes, ApplicationHandlerStop
)
from telegram.request import HTTPXRequest
from telegram.constants import ParseMode
from telegram.error import TelegramError, RetryAfter, Forbidden
from telegram.helpers import escape_markdown

# Supabase
from supabase import create_client, Client

# ATLAS Dual Storage (D1 primary + Supabase overflow)
from storage import (
    dual_insert, dual_get_mcq, enforce_quotas,
    bind_supabase, bootstrap_d1_schema, d1_enabled
)

# Google Gemini (New SDK)
from google import genai
from google.genai import types

# Image Processing
from PIL import Image, ImageDraw, ImageFont

# HTTP
import httpx
import aiohttp

# ============================================================
# SECTION 2: CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
GENAI_API_KEY = (os.getenv("GEMINI_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
print(f"[CONFIG] GEMINI_KEY: len={len(GENAI_API_KEY)}, prefix={GENAI_API_KEY[:8] if GENAI_API_KEY else 'EMPTY'}, suffix={GENAI_API_KEY[-4:] if GENAI_API_KEY else 'EMPTY'}")

# ── v4.0: Multi-AI provider keys (all optional, comma-separated, silent skip) ──
NVIDIA_KEYS = [k.strip() for k in os.getenv("NVIDIA_KEY", "").split(",") if k.strip()]
OPENROUTER_KEYS = [k.strip() for k in os.getenv("OPENROUTER_KEY", "").split(",") if k.strip()]
NEMOTRON_KEYS = [k.strip() for k in os.getenv("NEMOTRON_KEY", "").split(",") if k.strip()]
GEMMA_KEYS = [k.strip() for k in os.getenv("GEMMA_KEY", "").split(",") if k.strip()]
GROQ_KEYS = [k.strip() for k in os.getenv("GROQ_KEY", "").split(",") if k.strip()]

# v4.1: Cloudflare Workers AI — final free fallback for image→MCQ generation.
# Uses the existing CF account (no separate paid key needed beyond a scoped
# CF API token with "Workers AI: Edit" permission). Free daily neuron quota.
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "").strip()
CF_AI_TOKEN = os.getenv("CF_AI_TOKEN", "").strip()
CF_WORKERS_AI_MODEL = os.getenv("CF_WORKERS_AI_MODEL", "@cf/meta/llama-3.2-11b-vision-instruct")
CF_WORKERS_AI_BASE = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/v1" if CF_ACCOUNT_ID else ""

GROQ_MODEL = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
# v4.2: Groq is now PRIMARY. Multiple vision-capable Groq models rotated
# alongside keys — comma-separated env override supported, sane defaults otherwise.
GROQ_MODELS = [m.strip() for m in os.getenv(
    "GROQ_MODELS",
    "meta-llama/llama-4-scout-17b-16e-instruct,meta-llama/llama-4-maverick-17b-128e-instruct"
).split(",") if m.strip()]
if GROQ_MODEL not in GROQ_MODELS:
    GROQ_MODELS.insert(0, GROQ_MODEL)

NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct")
OPENROUTER_QWEN_MODEL = os.getenv("OPENROUTER_QWEN_MODEL", "qwen/qwen2.5-vl-72b-instruct:free")
NEMOTRON_MODEL = os.getenv("NEMOTRON_MODEL", "nvidia/nemotron-nano-12b-v2-vl:free")
GEMMA_MODEL = os.getenv("GEMMA_MODEL", "google/gemma-3-27b-it:free")

SUPABASE_URL = "https://wbdyjpjbczfunyhhmtry.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndiZHlqcGpiY3pmdW55aGhtdHJ5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA2OTI5ODAsImV4cCI6MjA5NjI2ODk4MH0.0WR1sgVsl_1XWZfSd0Pwoe6Uxp-2GMTksfseMn5aWjg"
SUPABASE_BACKUP_URL = os.getenv("SUPABASE_BACKUP_URL", "").rstrip("/")
SUPABASE_BACKUP_KEY = os.getenv("SUPABASE_BACKUP_KEY", "")

HF_SPACE_URL = os.getenv("PUBLIC_BASE_URL", os.getenv("HF_SPACE_URL", "https://atlasbot-pvp7.onrender.com"))
CF_WORKER_URL = "https://atlas-bot-proxy.hamza818483.workers.dev"
# v4.3: GitHub Pages exam link — CF/Render duitai fail korleo page static
# host theke load hoy, bhitorer JS nijei Render->CF->Supabase try kore.
GH_PAGES_EXAM_URL = os.environ.get("GH_PAGES_EXAM_URL", "https://hamza818483-dotcom.github.io/AtlasBot/exam.html")
# Outbound Telegram API calls (base_url/base_file_url) specifically — separate from
# CF_WORKER_URL because *.workers.dev is blocked from inside the HF Space container.
# atlas-bot-proxy-pages.pages.dev is a full 1:1 mirror of worker.js (D1 query, webhook,
# file proxy, sendDocument, and the general Telegram proxy all included) — same domain
# already configured as CF_D1_URL for storage.py's D1 queries, confirming it's reachable
# from the HF Space. Defaults here, but overridable via env var without a code change.
CF_TG_API_URL = os.getenv("CF_TG_API_URL", "https://atlas-bot-proxy-pages.pages.dev")
# Dual-platform fallback (mirrors QuizBot's pattern): if RUNNING_ON=Render or
# RENDER_URL is set, the bot is running on Render, which CAN reach
# api.telegram.org directly (no workers.dev block there, unlike HF Space) —
# so webhook/API calls go straight to Telegram instead of through the CF
# proxy. This gives a backup deployment target if HF Space or the CF proxy
# ever goes down. Defaults are empty/unset, so existing HF-only deployments
# are completely unaffected unless these env vars are explicitly configured.
RUNNING_ON = os.getenv("RUNNING_ON", "")
RENDER_URL = os.getenv("RENDER_URL", "").rstrip("/")
IS_RENDER = (RUNNING_ON == "Render") or bool(RENDER_URL and "onrender.com" in RENDER_URL)

DEFAULT_TIMER = 30
DEFAULT_FREE_LIMIT = 3
DEFAULT_DAILY_LIMIT = 5
DEFAULT_NEGATIVE_MARK = -0.50
NEW_PRACTICE_COUNT = 15
MAX_MCQ = 20
MIN_MCQ = 10
POLL_DELAY = 1.5

FREE_NEW_EXAM_LIMIT = 2
PERMITTED_NEW_EXAM_LIMIT = 20

BOT_USERNAME = ""  # filled at startup for deep links

BUSY_MSG = "🤖 এটলাস বট এই মুহূর্তে ব্যস্ত আছে!\nকিছুক্ষণ অপেক্ষা করে আবার চেষ্টা করুন। 🙏\n\nবারবার সমস্যা হলে Owner কে জানান।\n🔗 Owner: @rafi_somc"

try:
    BD_TZ = timezone(timedelta(hours=6))
except:
    BD_TZ = datetime.now().astimezone().tzinfo

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

PROCESSING_MSG = """
🔄 {first_name}, আপনার MCQ তৈরি করা হচ্ছে...
📊 Today: {attempt}/{limit}
⏱️ আনুমানিক সময়: {eta} সেকেন্ড
🕐 শেষ হবে: {end_time}
"""

PREMIUM_MSG = """
🌟 প্রিমিয়াম ফিচার!
আপনার আজকের ফ্রি লিমিট শেষ হয়ে গেছে।
আগামীকাল আবার চেষ্টা করুন অথবা এডমিনের সাথে যোগাযোগ করুন।
"""

# ============================================================
# SECTION 3: SUPABASE CLIENT SETUP (+ v4.0 backup mirror)
# ============================================================
supabase: Client = None
supabase_backup: Client = None

def get_supabase() -> Client:
    global supabase
    if supabase is None:
        try:
            supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
            # postgrest এর httpx session এ keepalive disable করো
            # idle connection server side বন্ধ হলে RemoteProtocolError আসে
            try:
                new_session = httpx.Client(
                    http2=False,
                    timeout=20,
                    limits=httpx.Limits(
                        max_keepalive_connections=0,
                        max_connections=10,
                        keepalive_expiry=0,
                    ),
                )
                # Copy existing headers/auth to new session
                new_session.headers.update(supabase.postgrest.session.headers)
                supabase.postgrest.session = new_session
                log("✅ Supabase client initialized (keepalive disabled)")
            except Exception as patch_err:
                log(f"⚠️ Session patch failed (non-critical): {patch_err}")
        except Exception as e:
            log_error(f"Supabase init failed: {e}")
            raise
    return supabase

def _reset_supabase_client() -> None:
    """force-recreate the Supabase client with HTTP/1.1 patch."""
    global supabase
    supabase = None
    log("🔄 Supabase client reset — will recreate with HTTP/1.1 on next call")

def supabase_call(fn, *, retries: int = 1):
    """v4.1: run a Supabase operation with one automatic retry on
    transient connection errors (ConnectionTerminated, RemoteProtocolError,
    ConnectError). `fn` takes the client and returns the result of .execute().
    Use for any new/critical Supabase call sites; existing call sites are
    unaffected and keep working as before."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return fn(get_supabase())
        except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError) as e:
            last_exc = e
            _reset_supabase_client()
            if attempt < retries:
                continue
            raise
    if last_exc:
        raise last_exc

def _patch_supabase_execute_with_retry() -> None:
    """v4.2: monkey-patch postgrest's execute() so EVERY existing
    get_supabase().table(...).execute() call (36+ call sites across this
    file) auto-retries once on transient HTTP/2 ConnectionTerminated
    errors, without needing to touch any of those call sites individually."""
    try:
        from postgrest._sync.request_builder import SyncQueryRequestBuilder
    except ImportError:
        log_error("Could not patch postgrest execute() — retry-on-disconnect disabled")
        return

    original_execute = SyncQueryRequestBuilder.execute

    def patched_execute(self):
        last_exc = None
        for attempt in range(4):  # initial try + 3 retries
            try:
                return original_execute(self)
            except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError) as e:
                last_exc = e
                _reset_supabase_client()
                get_supabase()
                if attempt < 3:
                    time.sleep(0.5 * (attempt + 1))
        # সব retry fail — তখনই log করো
        log(f"[Supabase] All 4 attempts failed: {type(last_exc).__name__}", "WARNING")
        raise last_exc

    SyncQueryRequestBuilder.execute = patched_execute

_patch_supabase_execute_with_retry()

def get_supabase_backup() -> Optional[Client]:
    """v4.0: optional secondary Supabase mirror. Silent if not configured."""
    global supabase_backup
    if not SUPABASE_BACKUP_URL or not SUPABASE_BACKUP_KEY:
        return None
    if supabase_backup is None:
        try:
            supabase_backup = create_client(SUPABASE_BACKUP_URL, SUPABASE_BACKUP_KEY)
            log("✅ Supabase BACKUP client initialized")
        except Exception as e:
            log_error(f"Supabase backup init failed: {e}")
            return None
    return supabase_backup

def mirror_insert(table: str, row: Dict) -> None:
    """v4.0: fire-and-forget mirror to backup DB. Never raises, never blocks logic."""
    try:
        bk = get_supabase_backup()
        if bk:
            bk.table(table).insert(row).execute()
    except Exception:
        pass

def init_database():
    try:
        client = get_supabase()
        log("✅ Supabase database ready (tables managed via Dashboard)")
    except Exception as e:
        log_error(f"Database init error: {e}")

# ============================================================
# SECTION 4: GEMINI SETUP (Multi-key rotation; AIza & AQ. both work —
# they are plain API-key strings, SDK handles both formats identically)
# ============================================================
GEMINI_KEYS = [k.strip() for k in GENAI_API_KEY.split(",") if k.strip()]
_current_key_idx = 0
_bot_genai_client = None

def setup_gemini():
    global _bot_genai_client
    if GEMINI_KEYS:
        _bot_genai_client = genai.Client(api_key=GEMINI_KEYS[0])
        log(f"✅ Gemini configured ({len(GEMINI_KEYS)} keys loaded)")
    else:
        log("⚠️ No GEMINI keys!", "WARNING")

def rotate_gemini_key():
    global _bot_genai_client, _current_key_idx
    if len(GEMINI_KEYS) <= 1:
        return False
    _current_key_idx = (_current_key_idx + 1) % len(GEMINI_KEYS)
    _bot_genai_client = genai.Client(api_key=GEMINI_KEYS[_current_key_idx])
    log(f"🔄 Rotated to key #{_current_key_idx+1}/{len(GEMINI_KEYS)}")
    return True

# ============================================================
# SECTION 4B: v4.0 MULTI-AI FALLBACK ENGINE
# Chain: Gemini (all keys) → NVIDIA 11B → OpenRouter Qwen VL 72B →
#        Nemotron → Gemma. Providers without keys are silently skipped.
# ============================================================

STRICT_SOURCE_RULES = """

🔒 STRICT MANDATORY RULES (MUST FOLLOW 100%):
1. প্রতিটি প্রশ্ন/অপশন/ব্যাখ্যা শুধুমাত্র Input Source (Image/Text) থেকে আসবে। নিজের জ্ঞান/আন্দাজ থেকে কিছু বানানো সম্পূর্ণ নিষেধ।
2. Source-এর প্রতিটি গুরুত্বপূর্ণ তথ্য কভার করো — যত MCQ সম্ভব বানাও যেন একটাও সম্ভাব্য MCQ মিস না হয়। তবে Quality > Quantity।
3. হাবিজাবি/দুর্বল/অপ্রাসঙ্গিক MCQ একদম নিষেধ।
4. একটি প্রশ্নের একটিই সঠিক উত্তর — একাধিক সঠিক যেন না হয়।
5. Output: ONLY valid JSON, no extra text."""

async def _call_gemini(prompt_text: str, image_bytes: Optional[bytes]) -> Optional[str]:
    global _bot_genai_client
    if not GEMINI_KEYS:
        return None
    if _bot_genai_client is None:
        setup_gemini()
    tries = max(1, len(GEMINI_KEYS))
    for attempt in range(tries):
        klabel = f"gemini#{_current_key_idx+1}"
        for retry in range(2):
            try:
                contents = [prompt_text]
                if image_bytes:
                    contents.append(Image.open(BytesIO(image_bytes)))
                loop = asyncio.get_event_loop()
                resp = await loop.run_in_executor(None, lambda: _bot_genai_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=contents,
                    config=types.GenerateContentConfig(
                        temperature=0.7, top_p=0.95, top_k=40,
                        max_output_tokens=8192,
                        thinking_config=types.ThinkingConfig(thinking_budget=1024),
                    )
                ))
                if resp and resp.text:
                    _track_attempt("gemini", klabel, ok=True)
                    return resp.text
                _track_attempt("gemini", klabel, ok=False)
                break
            except Exception as e:
                es = str(e).lower()
                exhausted = any(s in es for s in ("quota", "429", "resource_exhausted"))
                if exhausted:
                    _track_attempt("gemini", klabel, ok=False, exhausted=True)
                    break
                if "500" in es or "503" in es or "timeout" in es or "unavailable" in es:
                    if retry == 0:
                        await asyncio.sleep(1)
                        continue
                _track_attempt("gemini", klabel, ok=False, exhausted=False)
                break
        rotate_gemini_key()
    return None

_groq_key_idx = 0
_groq_model_idx = 0

async def _call_groq(prompt_text: str, image_bytes: Optional[bytes]) -> Optional[str]:
    """v4.2: Groq PRIMARY provider — smooth key rotation x model rotation.
    On rate-limit/failure, tries the next key; once all keys are exhausted for
    the current model, rotates to the next model and retries all keys again.
    This maximizes free-tier throughput across Groq's multiple vision models."""
    global _groq_key_idx, _groq_model_idx
    if not GROQ_KEYS:
        return None
    models = GROQ_MODELS or [GROQ_MODEL]
    n_keys = len(GROQ_KEYS)
    n_models = len(models)
    for m_attempt in range(n_models):
        model = models[(_groq_model_idx + m_attempt) % n_models]
        model_label = model.split('/')[-1][:18]
        all_exhausted = all(
            _is_key_exhausted_today("groq", f"groq#{i+1}:{model_label}") for i in range(n_keys)
        )
        for k_attempt in range(n_keys):
            key_i = (_groq_key_idx + k_attempt) % n_keys
            k = GROQ_KEYS[key_i]
            klabel = f"groq#{key_i+1}:{model_label}"
            if not all_exhausted and _is_key_exhausted_today("groq", klabel):
                continue  # already known-dead for today -- skip straight to next key
            txt, exhausted = await _call_openai_compat(
                "https://api.groq.com/openai/v1", k, model,
                prompt_text, image_bytes, provider="groq", key_label=klabel
            )
            if txt:
                _groq_key_idx = key_i
                _groq_model_idx = (_groq_model_idx + m_attempt) % n_models
                return txt
        # all keys tried for this model -- rotate model on next outer loop
    return None


_or_model_idx = 0
_or_key_idx: Dict[str, int] = {}  # per-model key rotation index (each model may have its own key pool)


async def _call_openrouter_family(prompt_text: str, image_bytes: Optional[bytes],
                                   extra_headers: Dict) -> Tuple[Optional[str], str]:
    """OpenRouter family (Qwen VL / Nemotron / Gemma) -- smooth model x key
    rotation, same pattern as Groq's _call_groq: on failure, tries the next
    key for the current model; once that model's keys are exhausted, rotates
    to the next model and tries its keys. Remembers the last successful model
    so a currently rate-limited model isn't retried first on every call."""
    global _or_model_idx
    chains = [
        (OPENROUTER_KEYS, OPENROUTER_QWEN_MODEL, "openrouter-qwen"),
        (NEMOTRON_KEYS or OPENROUTER_KEYS, NEMOTRON_MODEL, "nemotron"),
        (GEMMA_KEYS or OPENROUTER_KEYS, GEMMA_MODEL, "gemma"),
    ]
    n_models = len(chains)
    for m_attempt in range(n_models):
        m_i = (_or_model_idx + m_attempt) % n_models
        keys, model, name = chains[m_i]
        if not keys:
            continue
        n_keys = len(keys)
        start_k = _or_key_idx.get(name, 0)
        all_exhausted = all(_is_key_exhausted_today(name, f"{name}#{i+1}") for i in range(n_keys))
        for k_attempt in range(n_keys):
            key_i = (start_k + k_attempt) % n_keys
            k = keys[key_i]
            klabel = f"{name}#{key_i+1}"
            if not all_exhausted and _is_key_exhausted_today(name, klabel):
                continue  # already known-dead for today -- skip straight to next key
            txt, _ = await _call_openai_compat(
                "https://openrouter.ai/api/v1", k, model,
                prompt_text, image_bytes, extra_headers,
                provider=name, key_label=klabel
            )
            if txt:
                _or_key_idx[name] = key_i
                _or_model_idx = m_i
                return txt, name
        # all keys tried for this model -- rotate model on next outer loop
    return None, ""

def _b64_data_url(image_bytes: bytes) -> str:
    mime = "image/jpeg"
    if image_bytes[:8].startswith(b"\x89PNG"):
        mime = "image/png"
    elif image_bytes[:4] == b"RIFF":
        mime = "image/webp"
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"

# ============================================================
# v4.0: PROVIDER/KEY USAGE TRACKING (for /keys command, owner-only)
# Tracks per-key success/fail/exhausted counts for the current BD-day.
# In-memory (resets on restart) + reset daily at BD midnight.
# ============================================================
_provider_stats: Dict[str, Dict] = {}
_provider_stats_day = datetime.now(BD_TZ).strftime('%Y-%m-%d')

# Free-tier daily quota hints (approx) + reset time (BD) per provider — for display only
PROVIDER_QUOTA_HINTS = {
    "gemini":         {"rpd": 200,  "reset": "দুপুর ১-২টা (BD)", "label": "Gemini 2.5 Flash"},
    "groq":           {"rpd": 14400, "reset": "প্রতিদিন (rolling)", "label": "Groq Llama-4-Scout Vision"},
    "nvidia":         {"rpd": 1000, "reset": "প্রতি মাসে credit", "label": "NVIDIA Vision"},
    "openrouter-qwen":{"rpd": 50,   "reset": "ভোর ৬টা (BD)", "label": "OpenRouter Qwen2.5-VL-72B"},
    "nemotron":       {"rpd": 50,   "reset": "ভোর ৬টা (BD)", "label": "OpenRouter Nemotron-VL"},
    "gemma":          {"rpd": 50,   "reset": "ভোর ৬টা (BD)", "label": "OpenRouter Gemma-3-27B"},
    "cf-workers-ai":  {"rpd": 10000, "reset": "প্রতিদিন (free neurons)", "label": "Cloudflare Workers AI (Llama 3.2 Vision)"},
}

def _reset_provider_stats_if_new_day():
    global _provider_stats, _provider_stats_day
    today = datetime.now(BD_TZ).strftime('%Y-%m-%d')
    if today != _provider_stats_day:
        _provider_stats = {}
        _provider_stats_day = today

def _track_attempt(provider: str, key_label: str, ok: bool, exhausted: bool = False):
    if not provider:
        return
    _reset_provider_stats_if_new_day()
    p = _provider_stats.setdefault(provider, {})
    k = p.setdefault(key_label or provider, {"ok": 0, "fail": 0, "exhausted": False, "last": ""})
    if ok:
        k["ok"] += 1
    else:
        k["fail"] += 1
    if exhausted:
        k["exhausted"] = True
    k["last"] = datetime.now(BD_TZ).strftime('%H:%M')

def _key_prefix(k: str) -> str:
    """Safe key fingerprint for display (prefix + suffix only)."""
    if not k:
        return "—"
    if len(k) <= 12:
        return k[:4] + "…"
    return f"{k[:6]}…{k[-4:]}"

def _is_key_exhausted_today(provider: str, key_label: str) -> bool:
    """Checks _provider_stats (resets daily at BD midnight) to see if this
    exact provider+key was already marked quota-exhausted today, so rotators
    can skip straight past a known-dead key instead of wasting a round-trip
    re-confirming it's still exhausted."""
    _reset_provider_stats_if_new_day()
    p = _provider_stats.get(provider)
    if not p:
        return False
    k = p.get(key_label)
    return bool(k and k.get("exhausted"))

async def _call_openai_compat(base_url: str, api_key: str, model: str,
                              prompt_text: str, image_bytes: Optional[bytes],
                              extra_headers: Dict = None,
                              provider: str = "", key_label: str = "") -> Tuple[Optional[str], bool]:
    """Generic OpenAI-compatible chat call with retry. Returns (text, quota_exhausted)."""
    content: Any
    if image_bytes:
        content = [
            {"type": "text", "text": prompt_text},
            {"type": "image_url", "image_url": {"url": _b64_data_url(image_bytes)}},
        ]
    else:
        content = prompt_text
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.7,
        "max_tokens": 8192,
    }
    max_retries = 2
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    txt = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if txt:
                        _track_attempt(provider, key_label, ok=True)
                        return txt, False
                    return None, False
                if r.status_code == 429:
                    _track_attempt(provider, key_label, ok=False, exhausted=True)
                    return None, True
                if r.status_code in (500, 502, 503) and attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    continue
                _track_attempt(provider, key_label, ok=False)
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(1)
                continue
            _track_attempt(provider, key_label, ok=False)
        except Exception as e:
            _track_attempt(provider, key_label, ok=False)
            break
    return None, False

async def ai_generate(prompt_text: str, image_bytes: Optional[bytes] = None) -> Tuple[Optional[str], str]:
    """v4.3: Full fallback chain. Returns (text, provider_name) or (None, '').
    Order: Groq (PRIMARY) -> Gemini -> OpenRouter (Qwen VL -> Nemotron -> Gemma)
    -> Cloudflare Workers AI -> NVIDIA Vision.
    Every provider/key with all-key rotation; missing keys silently skipped."""
    full_prompt = prompt_text + STRICT_SOURCE_RULES
    # 1) Groq (PRIMARY -- smooth key x model rotation) -- tracked inside _call_groq
    txt = await _call_groq(full_prompt, image_bytes)
    if txt:
        return txt, "groq"
    # 2) Gemini (fallback, all keys with rotation) -- tracked inside _call_gemini
    txt = await _call_gemini(full_prompt, image_bytes)
    if txt:
        return txt, "gemini"
    or_headers = {"HTTP-Referer": HF_SPACE_URL, "X-Title": "ATLAS MCQ Bot"}
    # 3) OpenRouter family: Qwen VL 72B / Nemotron / Gemma -- smooth model x key
    # rotation (same pattern as Groq): on failure, tries the next key for the
    # current model; once that model's keys are all exhausted, rotates to the
    # next model and tries its keys. Remembers the last successful model so a
    # model that's currently rate-limited doesn't get retried first every time.
    txt, provider = await _call_openrouter_family(full_prompt, image_bytes, or_headers)
    if txt:
        return txt, provider
    # 4) Cloudflare Workers AI — uses CF account directly, no per-request key
    # rotation since it's one shared account token.
    if CF_ACCOUNT_ID and CF_AI_TOKEN:
        txt, _ = await _call_openai_compat(CF_WORKERS_AI_BASE, CF_AI_TOKEN, CF_WORKERS_AI_MODEL,
                                           full_prompt, image_bytes, provider="cf-workers-ai",
                                           key_label="cf#1")
        if txt:
            return txt, "cf-workers-ai"
    # 5) NVIDIA Vision (final fallback) -- all keys rotated
    for i, k in enumerate(NVIDIA_KEYS):
        txt, _ = await _call_openai_compat("https://integrate.api.nvidia.com/v1", k, NVIDIA_MODEL,
                                           full_prompt, image_bytes, provider="nvidia", key_label=f"nvidia#{i+1}")
        if txt:
            return txt, "nvidia"
    return None, ""

def _fix_json_str(t: str) -> str:
    """Fix common AI JSON issues: trailing commas, missing values, unquoted keys, truncation."""
    t = re.sub(r',\s*([}\]])', r'\1', t)
    t = re.sub(r':\s*,', ': "",', t)
    t = re.sub(r':\s*}', ': ""}', t)
    t = re.sub(r',\s*$', ']', t)
    if t.count('[') > t.count(']'):
        t = t.rstrip().rstrip(',') + ']'
    if t.count('{') > t.count('}'):
        t = t.rstrip().rstrip(',') + '}'
        if t.count('[') > t.count(']'):
            t = t + ']'
    return t

def _extract_mcq_objects(t: str) -> List[Dict]:
    """Extract individual MCQ JSON objects from messy text using brace matching."""
    mcqs = []
    i = 0
    while i < len(t):
        if t[i] == '{':
            depth = 0
            start = i
            for j in range(i, len(t)):
                if t[j] == '{': depth += 1
                elif t[j] == '}': depth -= 1
                if depth == 0:
                    candidate = t[start:j+1]
                    if '"question"' in candidate and '"options"' in candidate and '"answer"' in candidate:
                        try:
                            obj = json.loads(candidate)
                            mcqs.append(obj)
                        except json.JSONDecodeError:
                            try:
                                mcqs.append(json.loads(_fix_json_str(candidate)))
                            except json.JSONDecodeError:
                                pass
                    i = j + 1
                    break
            else:
                break
        else:
            i += 1
    return mcqs

def _fix_missing_object_braces(t: str) -> str:
    """Fix AI output like [\"question\":\"...\",\"options\":[...],\"answer\":0,\"explanation\":\"...\"]
    where each object's opening/closing { } got dropped, leaving a flat array
    of "key":value pairs (with nested option arrays) instead of array-of-objects."""
    if '"question"' not in t:
        return t
    # Split into per-question chunks at each occurrence of "question":
    parts = re.split(r'(?="question"\s*:)', t)
    chunks = [p for p in parts if '"question"' in p]
    if not chunks:
        return t
    fixed_objs = []
    for c in chunks:
        c = c.strip().strip('[').strip(']').strip(',').strip()
        c = '{' + c
        # trim trailing stray brackets/commas then close
        c = c.rstrip().rstrip(',').rstrip(']').rstrip(',')
        c = c + '}'
        fixed_objs.append(c)
    return '[' + ','.join(fixed_objs) + ']'



_BENGALI_RE = re.compile(r'[\u0980-\u09FF]')
_ARABIC_RE = re.compile(r'[\u0600-\u06FF]')
_DEVANAGARI_RE = re.compile(r'[\u0900-\u097F]')
_LATIN_RE = re.compile(r'[A-Za-z]')

def _detect_script(text: str) -> str:
    """Detects dominant script/language of a text block for language-lock checks."""
    if not text:
        return "unknown"
    counts = {
        "bengali": len(_BENGALI_RE.findall(text)),
        "arabic": len(_ARABIC_RE.findall(text)),
        "devanagari": len(_DEVANAGARI_RE.findall(text)),
        "latin": len(_LATIN_RE.findall(text)),
    }
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else "unknown"

def _mcq_violates_language_lock(mcq: Dict, source_script: str) -> bool:
    """🔒 STRICT_LANGUAGE_LOCK enforcement: an MCQ's question+options+explanation
    must be in the SAME script as the source. Flags MCQs that were silently
    translated/defaulted to a different script than the input — the single
    most common way prompt rules get broken under speed pressure."""
    if source_script == "unknown":
        return False  # can't verify, don't punish
    combined = (mcq.get('question', '') + ' ' + ' '.join(str(o) for o in mcq.get('options', [])))
    mcq_script = _detect_script(combined)
    if mcq_script == "unknown":
        return False
    return mcq_script != source_script

def _dedupe_mcqs(mcqs: List[Dict]) -> List[Dict]:
    """Remove duplicate MCQs (same question text) so retry-merges don't inflate count with repeats."""
    seen = set()
    out = []
    for m in mcqs:
        q = (m.get('question') or m.get('q') or '').strip().lower()
        key = ''.join(q.split())
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(m)
    return out

_TF_BANNED_OPT_WORDS = ("হ্যাঁ", "না।", "সত্য", "মিথ্যা", "জ্বী", "জ্বি", "yes", "no", "true", "false")

def _is_tf_banned_option(opt: str) -> bool:
    """True/False style (prompt_2) forbids bare yes/no/true/false-ish options —
    every option must be a full fact statement, not a one-word verdict."""
    o = opt.strip().lower()
    if len(o) <= 12:
        for w in _TF_BANNED_OPT_WORDS:
            if w.lower() in o:
                return True
    return False

def _is_tf_style_question(q: str) -> bool:
    """prompt_2 requires questions in one of 4 exact 'বললে ভুল হবে (না)' patterns."""
    q = q.strip()
    return ("বললে ভুল হবে" in q) and ("সত্য" in q or "মিথ্যা" in q)

def parse_mcq_json(response_text: str, source_text: str = "", prompt_type: str = "") -> List[Dict]:
    """Shared cleaner+parser+validator for MCQ JSON from any AI provider.
    If source_text is provided, also enforces STRICT_LANGUAGE_LOCK by
    rejecting MCQs whose script doesn't match the source's dominant script."""
    source_script = _detect_script(source_text) if source_text else "unknown"
    t = (response_text or "").strip()
    t = t.replace('\u060c', ',')  # Arabic comma → normal comma (fixes all-strategies-failed bug)
    if t.startswith('```json'):
        t = t[7:]
    if t.startswith('```'):
        t = t[3:]
    if t.endswith('```'):
        t = t[:-3]
    t = t.strip()
    if not t.startswith('['):
        s, e = t.find('['), t.rfind(']')
        if s != -1 and e != -1 and e > s:
            t = t[s:e+1]
    mcqs = None
    try:
        mcqs = json.loads(t)
    except json.JSONDecodeError as je:
        if "Extra data" in str(je) and je.pos > 0:
            try:
                mcqs = json.loads(t[:je.pos])
            except json.JSONDecodeError:
                pass
    if mcqs is None:
        try:
            mcqs = json.loads(_fix_json_str(t))
        except json.JSONDecodeError:
            pass
    if mcqs is None:
        try:
            mcqs = json.loads(_fix_missing_object_braces(t))
        except json.JSONDecodeError:
            pass
    if mcqs is None:
        mcqs = _extract_mcq_objects(t)
    if not mcqs:
        # 🔧 Last resort: response likely got cut off mid-object (hit token limit).
        # Salvage every complete top-level {...} object before the truncation point,
        # drop the dangling incomplete tail instead of failing the whole batch.
        try:
            last_complete = t.rfind('},')
            if last_complete == -1:
                last_complete = t.rfind('}')
            if last_complete != -1:
                salvage = t[:last_complete+1]
                if not salvage.rstrip().endswith(']'):
                    salvage = salvage.rstrip().rstrip(',') + ']'
                if not salvage.lstrip().startswith('['):
                    salvage = '[' + salvage
                mcqs = json.loads(salvage)
        except Exception:
            mcqs = None
    if not mcqs:
        log_error(f"parse_mcq_json: all strategies failed, input len={len(t)}, first 300 chars: {t[:300]}")
        return []
    valid = []
    seen_questions = set()
    for mcq in mcqs:
        if not all(k in mcq for k in ['question', 'options', 'answer']):
            continue
        if len(mcq['options']) >= 4:
            mcq['options'] = mcq['options'][:4]
        if isinstance(mcq['answer'], str):
            mcq['answer'] = {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(mcq['answer'].upper(), 0)
        if not (0 <= mcq['answer'] <= 3 and len(mcq['options']) == 4):
            continue
        mcq['options'] = [clean_option_prefix(o, i) for i, o in enumerate(mcq['options'])]
        # 🔒 STRICT QUALITY GATE — reject prompt-violating MCQs instead of
        # silently accepting them (no shortcuts even under speed pressure)
        q_text = (mcq.get('question') or '').strip()
        if len(q_text) < 5:
            continue  # empty/garbage question
        opts = [str(o).strip() for o in mcq['options']]
        if any(len(o) == 0 for o in opts):
            continue  # empty option
        if len(set(opts)) < 4:
            continue  # duplicate options (must be 4 distinct choices)
        q_norm = re.sub(r'\s+', ' ', q_text).lower()
        if q_norm in seen_questions:
            continue  # duplicate question within same batch
        if _mcq_violates_language_lock(mcq, source_script):
            continue  # 🔒 STRICT_LANGUAGE_LOCK violation — script mismatch with source
        if prompt_type == 'prompt_2' and any(_is_tf_banned_option(o) for o in opts):
            continue  # 🔒 True/False style: bare হ্যাঁ/না/সত্য/মিথ্যা option not allowed
        if prompt_type == 'prompt_2' and not _is_tf_style_question(q_text):
            continue  # 🔒 True/False style: question must use the required "বললে ভুল হবে (না)" phrasing
        # 🔒 Reject meaningless mnemonic/rhyme-fragment options (e.g. "রূপা","পাশে","থাকে","সার")
        # — single Bangla word ≤3 chars with no digits/punctuation, when ALL 4 options are like this,
        # strongly indicates a rhyme-word leak from a mnemonic table rather than real MCQ content.
        def _is_bare_fragment(o: str) -> bool:
            stripped = re.sub(r'[^\u0980-\u09FF]', '', o)
            return 1 <= len(stripped) <= 4 and stripped == o.strip()
        if all(_is_bare_fragment(o) for o in opts):
            continue  # 🔒 all 4 options are bare 1-3 char fragments — likely mnemonic leak, not real MCQ
        # 🔒 Reject if the question's own subject term is repeated verbatim as one of the options
        # (self-referential/contradictory, common in "X এর সাথে সম্পর্কিত নয়" style questions)
        q_words = re.findall(r'[\u0980-\u09FF]{4,}', q_text)
        if q_words and any(any(w == opt.strip() for w in q_words) for opt in opts):
            continue  # 🔒 question subject reused as its own option
        seen_questions.add(q_norm)
        valid.append(mcq)
    return valid

_OPT_PREFIX_RE = re.compile(r'^\s*[\(\[]?\s*([A-Da-d]|[কখগঘ])\s*[\)\.\:\]।]\s*')

def clean_option_prefix(opt: str, idx: int = 0) -> str:
    """v4.0: Quiz/Poll/Web Exam অপশনের শুরুতে A)/a)/ক) ইত্যাদি prefix remove."""
    if not isinstance(opt, str):
        return opt
    cleaned = _OPT_PREFIX_RE.sub('', opt, count=1).strip()
    return cleaned if cleaned else opt

# ============================================================
# SECTION 5: PROMPTS (4 TYPES) — preserved from v3.0
# ============================================================

PROMPT_01 = """MCQ TYPE: Standard Easy

🟥Overall Instructions:
-Image এ আগে থেকে MCQ বানানো থাকুক বা Information থাকুক,সকল জায়গা থেকেই প্রশ্ন বানাবে
-যেসব লাইন থেকে MCQ বানানো MISS করা যাবে না (MUST PRIORITY):
  • কোনো পেইজ/লাইন যেকোনো কালার দিয়ে দাগানো বা হাইলাইটেড থাকলে (সবুজ, লাল, কমলা, হলুদ — এগুলো সবচেয়ে কমন হাইলাইটার কালার)
  • কোনো প্যারা/লাইন বক্স করা থাকলে বা কালার দিয়ে মার্ক করা থাকলে
  • কোনো লাইনের নিচে কলমের কালি দিয়ে আন্ডারলাইন (underline) করা থাকলে — লাল, কালো, নীল, সবুজ যেকোনো কালারেই হোক
  • বইয়ের মূল লাইনের সাথে হাতে/কলমে এক্সট্রা কোনো কালার, দাগ, মার্ক, আন্ডারলাইন দেখা গেলেই সেটা 100% মিস না করে অবশ্যই MCQ বানাতে হবে
-কোয়ালিটিফুল প্রশ্ন বানাতে হবে।
-এমনভাবে সকল প্রশ্ন বানাবে যাতে সকল লাইন থেকে MCQ কিভাবে আসতে পারে আইডিয়া হয়ে যাবে।
-ছক থাকলে স্পেশাল প্রায়োরিটি পাবে(Use Every Information for Making MCQ)
-টপিকের নাম,অধ্যায়ের নাম,হেডলাইন,পেইজ সংখ্যা এসব info theke mcq banabe na.
-🚫 STRICT: সোর্সে যদি মনে রাখার কৌশল/ছন্দ/rhyme/mnemonic শব্দ থাকে (যেমন "রূপা-রেটিনোব্লাস্টোমা", "পাশে/থাকে/সার" এর মত অর্থহীন সাউন্ড-শব্দ যেগুলো শুধু মুখস্থ করানোর জন্য ব্যবহৃত হয়), সেই ছন্দের শব্দগুলো (রূপা/পাশে/থাকে/সার টাইপ) কখনোই MCQ প্রশ্ন বা অপশন হিসেবে ব্যবহার করা যাবে না। শুধুমাত্র mnemonic এর সাথে যুক্ত আসল মেডিকেল/একাডেমিক তথ্য (রোগের নাম, লক্ষণ, সংজ্ঞা ইত্যাদি) নিয়ে MCQ বানাতে হবে — অর্থহীন ছন্দ-শব্দ নিয়ে না।
-হাবিজাবি MCQ বানানো যাবে না,বেশি প্রশ্ন বানানোর প্রয়োজনে একটি MCQ কেই ঘুরিয়ে ফিরিয়ে দেওয়া যেতে পারে।
-গড়ে ১০ থেকে ২০ টি Mcq বানাতে হবে (তথ্যের পরিমাণ অনুযায়ী)।তথ্য কম থাকলে ১০-১২টি, বেশি থাকলে ১৫-২০টি

💥প্রশ্ন: (ছোট, ১/১.৫/২ লাইন)
-সোর্স থেকে সকল টাইপের প্রশ্ন বানাতে হবে
-যতভাবে প্রশ্ন আসতে পারে প্রশ্ন রেডি হবে
-প্রশ্নগুলো মানসম্মত হবে
-প্রশ্ন কঠিন হবে না।

💥অপশন: (৪টি, এক শব্দের ছোট+20% বড় অপশন)
-নির্দিষ্ট টপিক বা বক্স থেকে ৪ টা অপশন বানানো সীমাবদ্ধ থাকবে না,ইনপুট সোর্স থেকে মিক্সড তথ্যের অপশন থাকবে।
-অবশ্যই প্রশ্ন অনুযায়ী সঠিক তথ্যের অপশন বানাতে হবে।
-সোর্স অনুযায়ী বিভিন্ন অপশনে মিক্সড তথ্য থাকলেও সমস্যা নাই।
-যে টপিক/অংশ থেকে প্রশ্ন বানাবে সেখানে কাছাকাছি অপশন থাকলে সেখান থেকেই অপশন নিবে(Hight Priority),যাতে করে User Confused হয় কোনটা আন্সার হবে ভাবতে গিয়ে।
-অপশনে সঠিক উত্তর অবশ্যই একটিই থাকবে,বাকিগুলো ভুল উত্তর।
-🚫 প্রশ্নে যে বিষয়/রোগ/টার্ম নিয়ে জিজ্ঞেস করা হচ্ছে (যেমন "X এর সাথে সম্পর্কিত নয় কোনটি?"), সেই X নিজেই কোনো অপশনে থাকতে পারবে না — এটা স্ববিরোধী ও অর্থহীন।
-৪ টি অপশনই তথ্য দ্বারা পরিপূর্ণ থাকবে Must.অর্থাৎ অপশনে হ্যাঁ,না,সত্য,মিথ্যা,জ্বী,না এসব টাইপ কথা থাকবে না।

💥উত্তর: 
-A/B/C/D এর মধ্যে একটি
-একাধিক উত্তর যেনো সঠিক না হয় এই বিষয় সর্বাধিক গুরুত্ব দিতে হবে।
-Answer Gulo different Option e hote hobe must.

💥ব্যাখ্যা (STRICT): 
-শুধু সঠিক উত্তরের ব্যাখ্যা না — Options A, B, C, D প্রতিটি নিয়ে আলাদা আলাদা তথ্য থাকতে হবে Must (কেনটা সঠিক + বাকি ৩টা কেন ভুল/কী)।
-এক লাইনের সাধারণ ব্যাখ্যা (শুধু "সঠিক উত্তর X" টাইপ) দেওয়া 100% নিষেধ।
-সব তথ্য 100% Input Source (Image/Text) থেকেই আসবে — নিজে থেকে তথ্য বানানো/অনুমান করা সম্পূর্ণ নিষেধ।
-Bengali explanation, max 200 character
-JSON output only. Format: [{"question":"...","options":["A) ...","B) ...","C) ...","D) ..."],"answer":0,"explanation":"..."}]
-answer must be integer 0-3 (A=0, B=1, C=2, D=3)"""

PROMPT_02 = """MCQ TYPE: True/False Style

🔴 সংখ্যা (সবচেয়ে গুরুত্বপূর্ণ): Source এ যত তথ্য আছে তার ভিত্তিতে গড়ে ১০ থেকে ২০ টি MCQ বানাতে হবে। কখনোই মাত্র ১-২টি MCQ বানিয়ে থামবে না। তথ্য কম থাকলে ১০-১২টি, তথ্য বেশি থাকলে ১৫-২০টি — একই তথ্য বিভিন্ন সত্য/মিথ্যা ভঙ্গিতে ঘুরিয়ে প্রশ্ন করো।

💥প্রশ্নের ধরন (randomly mix করো, একঘেয়ে নয়):
🔴 প্রতিটা প্যাটার্নে answer কোনটা হবে তা নিচে EXACT বলা আছে — ভুল ম্যাপ করা যাবে না:
- "নিচের কোনটিকে সত্য বললে ভুল হবে না?" → answer = যে option টা বাস্তবে সত্য/সঠিক তথ্য
- "নিচের কোনটিকে সত্য বললে ভুল হবে?" → answer = যে option টা বাস্তবে মিথ্যা/ভুল তথ্য
- "নিচের কোনটিকে মিথ্যা বললে ভুল হবে?" → answer = যে option টা বাস্তবে সত্য/সঠিক তথ্য
- "নিচের কোনটিকে মিথ্যা বললে ভুল হবে না?" → answer = যে option টা বাস্তবে মিথ্যা/ভুল তথ্য

⚠️ Self-check করো MCQ বানানোর পর: "বললে ভুল হবে না" মানে সেই দাবিটা সত্যি কথা বলছে (তাই সত্য-দাবিতে ভুল হবে না = আসল সত্য option; মিথ্যা-দাবিতে ভুল হবে না = আসল মিথ্যা option)। "বললে ভুল হবে" মানে উল্টোটা।

💥অপশন: 
-ছোট বা বড় (২ টাইপই হতে পারে)
-নির্দিষ্ট টপিক বা বক্স থেকে অপশন বানানো সীমাবদ্ধ থাকবে না,ইনপুট সোর্স থেকে মিক্সড তথ্যের অপশন থাকবে।
-অবশ্যই প্রশ্ন অনুযায়ী সঠিক তথ্যের অপশন বানাতে হবে।
-প্রশ্নে সঠিক/সত্য/পজিটিভ উত্তর বাছাই করতে বললে একটি অপশন সঠিক/সত্য/পজিটিভ হবে,বাকি গুলো ভুল।
-প্রশ্নে ভুল/মিথ্যা/নেগেটিভ উত্তর বাছাই করতে বললে একটিই অপশনই ভুল/মিথ্যা/নেগেটিভ হবে,বাকিগুলো সঠিক।
-অবশ্যই সকল তথ্য Input Image Or Text থেকেই নিতে হবে।
-৪ টি অপশনই তথ্য দ্বারা পরিপূর্ণ থাকবে Must.অর্থাৎ অপশনে হ্যাঁ,না,সত্য,মিথ্যা,জ্বী,না এসব টাইপ কথা থাকবে না।

💥উত্তর:
-A/B/C/D এর মধ্যে একটি (A/B/C/D format)
-প্রশ্ন অনুযায়ী উত্তর অবশ্যই একটিই হবে।
-একাধিক উত্তর যেনো সঠিক না হয় এই বিষয় সর্বাধিক গুরুত্ব দিতে হবে।

💥ব্যাখ্যা (STRICT):
-4টা Option A, B, C, D প্রতিটির তথ্য আলাদাভাবে থাকবে (কোনটা সত্য/মিথ্যা ও কেন) — শুধু 1 লাইনের সাধারণ ব্যাখ্যা নিষেধ।
-সব তথ্য 100% Input Source থেকেই — নিজে থেকে তথ্য বানানো নিষেধ।
-Bengali, max 165 chars
-JSON output only (একটি বড় array, ১০-২০টি object). Format: [{"question":"...","options":["A) ...","B) ...","C) ...","D) ..."],"answer":0,"explanation":"..."}]
-answer must be integer 0-3 (A=0, B=1, C=2, D=3)"""

PROMPT_03 = """MCQ TYPE: Short Question, Long Options

💥Instructions:
-প্রশ্ন: ছোট, এক লাইন
-অপশন: ৪টি বড় (বাক্য বা phrase)
-উত্তর: A/B/C/D এর মধ্যে একটি (A/B/C/D format)
-ব্যাখ্যা (STRICT): 4টা Option A,B,C,D প্রতিটির তথ্য আলাদা থাকবে — সঠিকটা কেন সঠিক + বাকি ৩টা কেন ভুল, সবই Precisely। শুধু 1 লাইনের সাধারণ ব্যাখ্যা নিষেধ।
-Input source থেকেই সব, নিজে থেকে তথ্য বানানো নিষেধ
-Bengali, max 165 chars
-গড়ে ১০ থেকে ২০ টি Mcq (তথ্যের পরিমাণ অনুযায়ী)
-JSON output only. Format: [{"question":"...","options":["A) ...","B) ...","C) ...","D) ..."],"answer":0,"explanation":"..."}]
-answer must be integer 0-3 (A=0, B=1, C=2, D=3)
-৪ টি অপশনই তথ্য দ্বারা পরিপূর্ণ থাকবে Must. হ্যাঁ/না টাইপ কথা থাকবে না।
-একটিই সঠিক উত্তর হবে, বাকিগুলো ভুল।"""

PROMPT_MIXED = """MCQ TYPE: Mixed (Standard Easy + True/False + Short Q Long Options)

🔴 সংখ্যা ও মিশ্রণ (সবচেয়ে গুরুত্বপূর্ণ):
- Source এর তথ্যের ভিত্তিতে গড়ে ১০ থেকে ২০ টি MCQ বানাতে হবে (তথ্য কম হলে ১০-১২টি, বেশি হলে ১৫-২০টি)। কখনোই ১-২টি বানিয়ে থামবে না।
- নিচের ৩ ধরনের প্রশ্ন বাধ্যতামূলকভাবে মিশ্রিত করতে হবে — প্রতিটি ধরন থেকে প্রায় সমান সংখ্যক (≈৩ ভাগের ১ ভাগ করে):
  Type 1 (Standard Easy): সাধারণ প্রশ্ন, ৪টি অপশন, একটি সঠিক
  Type 2 (True/False): "নিচের কোনটিকে সত্য/মিথ্যা বললে ভুল হবে (না)?" ধরনের — সত্য/মিথ্যা ভঙ্গি randomly। 🔴 Logic: "সত্য বললে ভুল হবে না"→answer=সত্য option, "সত্য বললে ভুল হবে"→answer=মিথ্যা option, "মিথ্যা বললে ভুল হবে"→answer=সত্য option, "মিথ্যা বললে ভুল হবে না"→answer=মিথ্যা option। ভুল ম্যাপ করা যাবে না।
  Type 3 (Short Q + Long Options): এক লাইনের প্রশ্ন, ৪টি বড় বাক্য/phrase অপশন
- পরপর একই ধরনের প্রশ্ন না দিয়ে ধরনগুলো interleave করো (১,২,৩,১,২,৩...) যাতে সত্যিকারের mix হয়।

💥সাধারণ নিয়ম (সব ধরনের জন্য):
-৪ টি অপশনই তথ্য দ্বারা পরিপূর্ণ থাকবে (হ্যাঁ/না/সত্য/মিথ্যা টাইপ একক শব্দ অপশন নিষেধ)
-একটিই সঠিক উত্তর, বাকিগুলো ভুল; একাধিক সঠিক যেন না হয়
-হাইলাইটেড/কালার মার্ক করা টেক্সট priority পাবে
-ছক/table থাকলে special priority
-টপিকের নাম, অধ্যায়ের নাম, পেইজ সংখ্যা থেকে MCQ বানাবে না
-Input source থেকেই সব তথ্য, নিজে থেকে তথ্য বানানো নিষেধ
-ব্যাখ্যা (STRICT): 4টা Option A,B,C,D প্রতিটির তথ্য আলাদা থাকবে, শুধু 1 লাইনের সাধারণ ব্যাখ্যা নিষেধ
-Bengali explanation, max 165-200 chars
-JSON output only (একটি বড় array, ১০-২০টি object). Format: [{"question":"...","options":["A) ...","B) ...","C) ...","D) ..."],"answer":0,"explanation":"..."}]
-answer must be integer 0-3 (A=0, B=1, C=2, D=3)"""

PROMPT_MAP = {
    'prompt_1': {'name': '🩺 Medical Standard MCQ', 'text': PROMPT_01},
    'prompt_2': {'name': '✅ সত্য-মিথ্যার প্রশ্ন', 'text': PROMPT_02},
    'prompt_3': {'name': '🔥 কঠিন প্রশ্ন', 'text': PROMPT_03},
    'prompt_mixed': {'name': '🎲 Mixed (সবগুলো)', 'text': PROMPT_MIXED},
    'qbm_extract': {'name': '📌 শুধুমাত্র পেইজে থাকা MCQ', 'text': (
        "YOU ARE A STRICT MCQ EXTRACTOR IN A PERMANENT SPECIAL MODE. ONLY EXTRACT MCQs THAT "
        "ALREADY EXIST ON THIS PAGE. NEVER INVENT NEW QUESTIONS.\n\n"
        "FORBIDDEN: never create a new question; never add extra MCQs beyond what exists; "
        "never skip any existing MCQ (extract ALL, serially, in order) — if the page has 34 MCQs, "
        "the output MUST contain all 34, not 22, not 30 — MISSING EVEN ONE IS A FAILURE; never guess an answer "
        "without real evidence; never modify question/option wording (only remove numbering). "
        "If zero MCQs exist on the page -> return exactly []. If N MCQs exist -> return exactly N.\n\n"
        "EXTRACTION: extract ALL MCQs already on the page -- Bangla/English/mixed, any font, "
        "blurry/scanned OK. Do at least 3 internal read-throughs and cross-check before finalizing. "
        "Remove only numbering prefixes (\u09e7., 1., Q1., \u0995.). Keep original wording intact.\n\n"
        "ANSWER DETECTION (the answer MUST come from real page evidence -- never guess): scan for, "
        "in order: (A) a mark on an option -- circle/tick/underline/bold/star; (B) answer given right "
        "after the question block; (C) an answer box/table at the bottom of THIS page matching "
        "question number to option; (D) a consolidated answer key SEVERAL PAGES LATER -- scan forward "
        "through all pages, match by question number; (E) an answer key on the page immediately "
        "before/after this one. Re-verify the detected answer against its source at least twice "
        "before finalizing. Only if truly no answer evidence exists anywhere -> default answer to "
        "index 0 and note 'Answer not found in source' in the explanation, as an absolute last resort.\n\n"
        "SHUFFLE: after detecting the real answer, shuffle the 4 options into a new random order "
        "for output, and update the answer index to match the new position. Do this per-MCQ -- never "
        "let the correct answer land on the same position repeatedly across a set.\n\n"
        "EXPLANATION (strict priority): 1) if the page already shows an explanation under the MCQ "
        "-> copy it 100% verbatim; 2) else if the page has other relevant info about this MCQ's topic "
        "-> build explanation from that info; 3) else generate the best accurate explanation yourself. "
        "STRICT: the explanation MUST cover all 4 options A/B/C/D individually (why the correct one is "
        "right, why each of the other 3 is wrong) -- a single generic one-line explanation is FORBIDDEN. "
        "ALL info must come from the source image/page/text -- never invent facts not present in source. "
        "Max 200 characters, Bengali.\n\n"
        "MATH/CHEMISTRY: always use proper Unicode subscript/superscript -- H\u2082O, CO\u2082, Na\u207a, Ca\u00b2\u207a, x\u00b2, "
        "10\u00b3, \u00b0C -- never raw H2O/x^2/x_0 notation. Apply consistently in question, options, explanation.\n\n"
        "NEVER reference the source itself in question or explanation text -- no \u0989\u09b2\u09cd\u09b2\u09c7\u0996\u09bf\u09a4 "
        "\u099a\u09bf\u09a4\u09cd\u09b0\u09c7, \u099a\u09bf\u09a4\u09cd\u09b0\u09c7 \u09a6\u09c7\u0996\u09be \u09af\u09be\u099a\u09cd\u099b\u09c7, \u09ac\u0995\u09cd\u09b8\u09c7, \u099b\u0995\u09c7, "
        "\u0989\u09a6\u09cd\u09a6\u09c0\u09aa\u0995\u09c7, \u09b8\u09be\u09b0\u09a3\u09bf\u09a4\u09c7, \u099f\u09aa\u09bf\u0995\u09c7, \u09aa\u09c3\u09b7\u09cd\u09a0\u09be\u09df, \u09aa\u09cd\u09af\u09be\u09b8\u09c7\u099c\u09c7, "
        "\u0985\u09a8\u09c1\u099a\u09cd\u099b\u09c7\u09a6\u09c7, \u09b2\u09c7\u0996\u099a\u09bf\u09a4\u09cd\u09b0\u09c7, \u0997\u09cd\u09b0\u09be\u09ab\u09c7, \u09a6\u09c7\u0996\u09be \u09af\u09be\u099a\u09cd\u099b\u09c7, \u09ac\u09b2\u09be \u0986\u099b\u09c7, "
        "\u0989\u09b2\u09cd\u09b2\u09c7\u0996 \u0995\u09b0\u09be \u0986\u099b\u09c7, \u09b2\u0995\u09cd\u09b7 \u0995\u09b0\u09be \u09af\u09be\u09df, \u09ac\u09b0\u09cd\u09a3\u09a8\u09be \u0986\u099b\u09c7, \u09a6\u09c7\u0996\u09be\u09a8\u09cb \u09b9\u09df\u09c7\u099b\u09c7, "
        "\u09a6\u09c7\u0993\u09df\u09be \u0986\u099b\u09c7, \u09aa\u09cd\u09b0\u09a6\u09a4\u09cd\u09a4, \u0989\u09aa\u09b0\u09c7 \u09a6\u09c7\u0996\u09be\u09a8\u09cb, or English equivalents "
        "(as shown in the figure/box/table/diagram/passage, shown above, mentioned in the text/page, "
        "as given, according to the figure/table/passage above). State facts directly and naturally, "
        "never mention they came from an image/box/table/passage/page.\n\n"
        "Each option must be exactly what's written on the page -- never a section heading/page "
        "label/card number as an option.\n\n"
        "\u0989\u09a6\u09cd\u09a6\u09c0\u09aa\u0995 (PASSAGE/STIMULUS) HANDLING -- STRICT: if a question or group of "
        "questions is based on a preceding passage/stimulus/scenario paragraph, prepend that "
        "passage's FULL text to the start of EVERY related MCQ's question (self-contained -- the "
        "question must be understandable without seeing the original passage separately). If "
        "multiple MCQs share the same passage, copy the full passage into each one's question "
        "text. Be careful to correctly identify real passages/scenarios vs standalone questions.\n\n"
        "OUTPUT: ONLY a valid JSON array, no extra text. \"answer\" is an INTEGER index (0=A,1=B,2=C,3=D) "
        "matching the option's position AFTER shuffling -- never a letter string.\n"
        "[{\"question\":\"...\",\"options\":[\"...\",\"...\",\"...\",\"...\"],\"answer\":0,\"explanation\":\"...\"}]"
    )},
}

# ============================================================
# SECTION 6: AYATS — preserved from v3.0
# ============================================================

AYATS = {
    'hardship': [
        '🌙 "فَإِنَّ مَعَ الْعُسْرِ يُسْرًا إِنَّ مَعَ الْعُسْرِ يُسْرًا"\n"নিশ্চয়ই কষ্টের সাথেই স্বস্তি আছে, নিশ্চয়ই কষ্টের সাথেই স্বস্তি আছে।"\n[সূরা আশ-শারহ ৯৪:৫-৬]',
        '🌙 "لَا يُكَلِّفُ اللَّهُ نَفْسًا إِلَّا وُسْعَهَا"\n"আল্লাহ কাউকে তার সাধ্যের বাইরে বোঝা দেন না।"\n[সূরা বাকারা ২:২৮৬]',
        '🌙 "سَيَجْعَلُ اللَّهُ بَعْدَ عُسْرٍ يُسْرًا"\n"আল্লাহ কষ্টের পর স্বস্তি দেবেন।"\n[সূরা তালাক ৬৫:৭]',
        '🌙 "فَلَا تَعْلَمُ نَفْسٌ مَّا أُخْفِيَ لَهُم مِّن قُرَّةِ أَعْيُنٍ"\n"কেউ জানে না তাদের জন্য চোখ শীতলকারী কী জিনিস লুকায়িত আছে।"\n[সূরা সাজদাহ ৩২:১৭]',
    ],
    'patience': [
        '🌙 "يَا أَيُّهَا الَّذِينَ آمَنُوا اسْتَعِينُوا بِالصَّبْرِ وَالصَّلَاةِ إِنَّ اللَّهَ مَعَ الصَّابِرِينَ"\n"হে ঈমানদারগণ! ধৈর্য ও সালাতের মাধ্যমে সাহায্য প্রার্থনা কর। নিশ্চয়ই আল্লাহ ধৈর্যশীলদের সাথে আছেন।"\n[সূরা বাকারা ২:১৫৩]',
        '🌙 "وَاصْبِرْ وَمَا صَبْرُكَ إِلَّا بِاللَّهِ"\n"ধৈর্য ধর, তোমার ধৈর্য তো আল্লাহরই সাহায্যে।"\n[সূরা নাহল ১৬:১২৭]',
        '🌙 "إِنَّمَا يُوَفَّى الصَّابِرُونَ أَجْرَهُم بِغَيْرِ حِسَابٍ"\n"ধৈর্যশীলদেরই তো অগণিত পুরস্কার দেওয়া হবে।"\n[সূরা যুমার ৩৯:১০]',
    ],
    'tawakkul': [
        '🌙 "وَمَن يَتَوَكَّلْ عَلَى اللَّهِ فَهُوَ حَسْبُهُ"\n"যে আল্লাহর উপর ভরসা করে, তার জন্য তিনিই যথেষ্ট।"\n[সূরা তালাক ৬৫:৩]',
        '🌙 "وَأُفَوِّضُ أَمْرِي إِلَى اللَّهِ"\n"আমি আমার কাজ আল্লাহর উপর ছেড়ে দিলাম।"\n[সূরা গাফির ৪০:৪৪]',
        '🌙 "فَإِذَا عَزَمْتَ فَتَوَكَّلْ عَلَى اللَّهِ"\n"যখন সিদ্ধান্ত কর, তখন আল্লাহর উপর ভরসা কর।"\n[সূরা আলে ইমরান ৩:১৫৯]',
    ],
    'ibadah': [
        '🌙 "وَقَالَ رَبُّكُمُ ادْعُونِي أَسْتَجِبْ لَكُمْ"\n"তোমাদের রব বলেন: আমাকে ডাকো, আমি সাড়া দেবো।"\n[সূরা গাফির ৪০:৬০]',
        '🌙 "فَاذْكُرُونِي أَذْكُرْكُمْ"\n"তোমরা আমাকে স্মরণ করো, আমি তোমাদের স্মরণ করবো।"\n[সূরা বাকারা ২:১৫২]',
        '🌙 "أَلَا بِذِكْرِ اللَّهِ تَطْمَئِنُّ الْقُلُوبُ"\n"জেনে রেখো, আল্লাহর স্মরণেই হৃদয় প্রশান্ত হয়।"\n[সূরা রা\'দ ১৩:২৮]',
    ],
    'exam': [
        '🌙 "وَلَنَبْلُوَنَّكُم بِشَيْءٍ مِّنَ الْخَوْفِ وَالْجُوعِ"\n"আমি অবশ্যই তোমাদের পরীক্ষা করবো ভয়, ক্ষুধা দিয়ে।"\n[সূরা বাকারা ২:১৫৫]',
        '🌙 "أَحَسِبَ النَّاسُ أَن يُتْرَكُوا أَن يَقُولُوا آمَنَّا وَهُمْ لَا يُفْتَنُونَ"\n"মানুষ কি ভাবে, \'আমরা ইমান এনেছি\' বললেই তাদের ছেড়ে দেওয়া হবে, তাদের পরীক্ষা করা হবে না?"\n[সূরা আনকাবূত ২৯:২]',
        '🌙 "وَلَنَبْلُوَنَّكُمْ حَتَّىٰ نَعْلَمَ الْمُجَاهِدِينَ مِنكُمْ وَالصَّابِرِينَ"\n"আমি অবশ্যই তোমাদের পরীক্ষা করবো যতক্ষণ না জেনে নিই কারা জিহাদ করে ও ধৈর্যশীল।"\n[সূরা মুহাম্মদ ৪৭:৩১]',
    ],
    'effort': [
        '🌙 "وَأَن لَّيْسَ لِلْإِنسَانِ إِلَّا مَا سَعَىٰ"\n"মানুষ তার চেষ্টার ফল ছাড়া কিছুই পায় না।"\n[সূরা নাজম ৫৩:৩৯]',
        '🌙 "إِنَّ اللَّهَ لَا يُغَيِّرُ مَا بِقَوْمٍ حَتَّىٰ يُغَيِّرُوا مَا بِأَنفُسِهِمْ"\n"আল্লাহ কোনো জাতির অবস্থা পরিবর্তন করেন না যতক্ষণ না তারা নিজেদের পরিবর্তন করে।"\n[সূরা রা\'দ ১৩:১১]',
        '🌙 "فَمَن يَعْمَلْ مِثْقَالَ ذَرَّةٍ خَيْرًا يَرَهُ"\n"যে অণু পরিমাণ ভালো কাজ করবে, সে তা দেখতে পাবে।"\n[সূরা যিলযাল ৯৯:৭]',
    ],
    'success': [
        '🌙 "إِن يَنصُرْكُمُ اللَّهُ فَلَا غَالِبَ لَكُمْ"\n"আল্লাহ যদি তোমাদের সাহায্য করেন, কেউ তোমাদের পরাজিত করতে পারবে না।"\n[সূরা আলে ইমরান ৩:১৬০]',
        '🌙 "وَمَا النَّصْرُ إِلَّا مِنْ عِندِ اللَّهِ"\n"সাহায্য তো শুধু আল্লাহর কাছ থেকেই আসে।"\n[সূরা আনফাল ৮:১০]',
        '🌙 "إِنَّ اللَّهَ يُحِبُّ الْمُحْسِنِينَ"\n"নিশ্চয়ই আল্লাহ সৎকর্মশীলদের ভালোবাসেন।"\n[সূরা বাকারা ২:১৯৫]',
    ],
    'hope': [
        '🌙 "لَا تَقْنَطُوا مِن رَّحْمَةِ اللَّهِ"\n"আল্লাহর রহমত থেকে নিরাশ হয়ো না।"\n[সূরা যুমার ৩৯:৫৩]',
        '🌙 "إِنَّ رَحْمَتَ اللَّهِ قَرِيبٌ مِّنَ الْمُحْسِنِينَ"\n"নিশ্চয়ই আল্লাহর রহমত সৎকর্মশীলদের নিকটবর্তী।"\n[সূরা আ\'রাফ ৭:৫৬]',
        '🌙 "وَرَحْمَتِي وَسِعَتْ كُلَّ شَيْءٍ"\n"আমার রহমত সবকিছুকে পরিবেষ্টন করে।"\n[সূরা আ\'রাফ ৭:১৫৬]',
    ],
}

# ============================================================
# SECTION 7: FEEDBACK MESSAGES — preserved
# ============================================================
FEEDBACKS = {
    'excellent': [
        '🌟 অসাধারণ! তুমি সত্যিই অনেক ভালো করেছো!',
        '🏆 দারুণ! তোমার প্রস্তুতি অনেক ভালো!',
        '💪 বাহ! দারুণ ফলাফল! এমনিতেই থাকো!',
        '🔥 অসাধারণ! তুমি পুড়িয়ে দিচ্ছো!',
    ],
    'good': [
        '✅ ভালো হয়েছে! আরও চেষ্টা করো!',
        '👍 মোটামুটি ভালো! ইম্প্রুভ করার জায়গা আছে!',
        '📚 ভালো! আরও পড়াশোনা করে আরও ভালো করবে!',
    ],
    'average': [
        '📖 মোটামুটি! আরও পড়তে হবে!',
        '💭 ঠিক আছে, তবে আরও ভালো করা সম্ভব!',
        '🎯 গড় ফলাফল! নিয়মিত চর্চা করো!',
    ],
    'poor': [
        '📚 বেশি করে পড়তে হবে! হাল ছেড়ো না!',
        '💪 চিন্তা করো না! আবার চেষ্টা করো!',
        '🌱 শুরুটা কঠিনই হয়! লেগে থাকো!',
        '🔄 অনুশীলনের বিকল্প নেই! আবার পড়ো!',
    ],
}

# ============================================================
# SECTION 8: LOGGING + v4.0 OWNER ERROR FORWARDING
# ============================================================
LOG_FILE = os.path.join(LOG_DIR, f"bot_{datetime.now(BD_TZ).strftime('%Y-%m-%d')}.log")

def log(message: str, level: str = "INFO") -> None:
    timestamp = datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] [{level}] [ATLAS] {message}"
    print(log_msg)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_msg + "\n")
    except Exception:
        pass

def log_error(message: str) -> None:
    timestamp = datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")
    log(message, "ERROR")
    error_file = os.path.join(LOG_DIR, f"errors_{datetime.now(BD_TZ).strftime('%Y-%m-%d')}.log")
    try:
        with open(error_file, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n{traceback.format_exc()}\n{'='*50}\n")
    except Exception:
        pass
    # v4.0: auto-forward every error to OWNER (fire-and-forget)
    try:
        if application and _bot_loop and OWNER_ID:
            # Transient connection errors — retry handles them, no need to spam owner
            if any(x in str(message) for x in [
                "ConnectError", "NetworkError",
                "RemoteProtocolError", "ConnectionTerminated",
                "ReadError", "recreating client"
            ]):
                return
            short = str(message)[:900]
            # Ensure we await or handle the coroutine properly
            async def _send():
                try:
                    await application.bot.send_message(chat_id=OWNER_ID, text=f"🚨 ATLAS ERROR\n\n{short}")
                except:
                    pass
            asyncio.run_coroutine_threadsafe(_send(), _bot_loop)
    except Exception:
        pass

async def notify_owner(text: str) -> None:
    try:
        if application and OWNER_ID:
            await application.bot.send_message(chat_id=OWNER_ID, text=text[:4000])
    except Exception:
        pass

async def safe_user_reply(message, custom: str = None) -> None:
    """v4.0: user never sees raw errors — cool busy message only."""
    try:
        await message.reply_text(custom or BUSY_MSG)
    except Exception:
        pass

# ============================================================
# SECTION 9: GLOBAL VARIABLES
# ============================================================
application: Optional[Application] = None
_timer_tasks: Dict[int, asyncio.Task] = {}
_poll_chat_map: Dict[str, int] = {}
_image_cache: Dict[str, Dict] = {}
_IMAGE_CACHE_MAX = 2000  # v4.5: hard cap — entry is tiny (few strings) but grows forever otherwise
_last_quiz_answers: Dict[int, Dict] = {}
_challenge_map: Dict[int, Dict] = {}
_checkin_polls: Dict[str, int] = {}  # v4.0: poll_id -> user_id for 6h check-in
_bot_start_time: Optional[datetime] = None

# ============================================================
# SECTION 10: DATABASE FUNCTIONS (Supabase) — preserved + mirror
# ============================================================

def create_user(user_id: int, first_name: str, username: str) -> bool:
    try:
        client = get_supabase()
        existing = client.table('users').select('user_id').eq('user_id', user_id).execute()
        if existing.data and len(existing.data) > 0:
            client.table('users').update({'first_name': first_name, 'username': username}).eq('user_id', user_id).execute()
            return True
        else:
            row = {
                'user_id': user_id, 'first_name': first_name, 'username': username,
                'is_permitted': False, 'daily_limit': DEFAULT_DAILY_LIMIT,
                'free_limit': DEFAULT_FREE_LIMIT, 'practice_count': 0,
                'usage_count': 0, 'last_reset': datetime.now(BD_TZ).strftime('%Y-%m-%d')
            }
            client.table('users').insert(row).execute()
            mirror_insert('users', row)
            log(f"👥 New user created: {user_id} ({first_name})")
            return True
    except Exception as e:
        log_error(f"create_user error: {e}")
        return False

def get_user(user_id: int) -> Optional[Dict]:
    try:
        client = get_supabase()
        result = client.table('users').select('*').eq('user_id', user_id).execute()
        if result.data and len(result.data) > 0:
            return result.data[0]
        return None
    except Exception as e:
        log_error(f"get_user error: {e}")
        return None

def update_user(user_id: int, data: Dict) -> bool:
    try:
        client = get_supabase()
        client.table('users').update(data).eq('user_id', user_id).execute()
        return True
    except Exception as e:
        log_error(f"update_user error: {e}")
        return False

def is_permitted(user_id: int) -> bool:
    user = get_user(user_id)
    if user:
        return user.get('is_permitted', False)
    return False

def permit_user(user_id: int) -> bool:
    return update_user(user_id, {'is_permitted': True, 'daily_limit': 50})

def unpermit_user(user_id: int) -> bool:
    return update_user(user_id, {'is_permitted': False, 'daily_limit': DEFAULT_DAILY_LIMIT})

def get_all_users() -> List[Dict]:
    try:
        client = get_supabase()
        result = client.table('users').select('user_id,first_name,username').execute()
        return result.data if result.data else []
    except Exception as e:
        log_error(f"get_all_users error: {e}")
        return []

def get_usage_report() -> List[Dict]:
    try:
        client = get_supabase()
        result = client.table('users').select('*').order('usage_count', desc=True).limit(30).execute()
        reports = []
        for row in (result.data or []):
            reports.append({
                'first_name': row.get('first_name', 'Unknown'),
                'user_id': row.get('user_id'),
                'usage': row.get('usage_count', 0),
                'limit': row.get('daily_limit', 0),
                'status': '✅ Permitted' if row.get('is_permitted') else '🔒 Free'
            })
        return reports
    except Exception as e:
        log_error(f"get_usage_report error: {e}")
        return []

def get_setting(key: str, default: Any = None) -> Any:
    try:
        client = get_supabase()
        result = client.table('settings').select('value').eq('key', key).execute()
        if result.data and len(result.data) > 0:
            return result.data[0].get('value', default)
        return default
    except Exception as e:
        log_error(f"get_setting error: {e}")
        return default

def set_setting(key: str, value: Any) -> bool:
    try:
        client = get_supabase()
        existing = client.table('settings').select('key').eq('key', key).execute()
        if existing.data and len(existing.data) > 0:
            client.table('settings').update({'value': str(value)}).eq('key', key).execute()
        else:
            client.table('settings').insert({'key': key, 'value': str(value)}).execute()
        return True
    except Exception as e:
        log_error(f"set_setting error: {e}")
        return False

def get_all_settings() -> Dict:
    try:
        result = supabase_call(lambda c: c.table('settings').select('*').execute())
        settings = {}
        for row in (result.data or []):
            settings[row['key']] = row['value']
        return settings
    except Exception as e:
        log_error(f"get_all_settings error: {e}")
        return {}

def get_user_limit(user_id: int) -> int:
    user = get_user(user_id)
    if user:
        return user.get('daily_limit', DEFAULT_DAILY_LIMIT)
    return DEFAULT_DAILY_LIMIT

def set_user_limit(user_id: int, count: int) -> bool:
    return update_user(user_id, {'daily_limit': count})

def check_access(user_id: int) -> Tuple[bool, int, int, bool]:
    # v4.0: Owner has unlimited access
    if user_id == OWNER_ID:
        return True, 0, 999999, True
    user = get_user(user_id)
    if not user:
        return True, 0, DEFAULT_FREE_LIMIT, False
    is_perm = user.get('is_permitted', False)
    usage = user.get('usage_count', 0)
    limit = user.get('daily_limit', DEFAULT_DAILY_LIMIT)
    allowed = usage < limit
    return allowed, usage, limit, is_perm

def check_new_exam_limit(user_id: int) -> Tuple[bool, int, int, bool]:
    if user_id == OWNER_ID:
        return True, 0, 999999, True
    user = get_user(user_id)
    if not user:
        return True, 0, FREE_NEW_EXAM_LIMIT, False
    is_perm = user.get('is_permitted', False)
    used = user.get('new_exam_count', 0)
    last_reset = user.get('last_new_exam_reset', '')
    today = datetime.now(BD_TZ).strftime('%Y-%m-%d')
    if last_reset != today:
        used = 0
        update_user(user_id, {'new_exam_count': 0, 'last_new_exam_reset': today})
    limit = PERMITTED_NEW_EXAM_LIMIT if is_perm else FREE_NEW_EXAM_LIMIT
    allowed = used < limit
    return allowed, used, limit, is_perm

def increment_new_exam_count(user_id: int) -> int:
    user = get_user(user_id)
    if user:
        new_count = user.get('new_exam_count', 0) + 1
        update_user(user_id, {'new_exam_count': new_count, 'last_new_exam_reset': datetime.now(BD_TZ).strftime('%Y-%m-%d')})
        return new_count
    return 0

def increment_usage(user_id: int) -> int:
    try:
        user = get_user(user_id)
        if user:
            new_usage = user.get('usage_count', 0) + 1
            new_practice = user.get('practice_count', 0) + 1
            # v4.0: track last active date for check-in streak logic
            update_user(user_id, {'usage_count': new_usage, 'practice_count': new_practice,
                                  'last_reset': user.get('last_reset') or datetime.now(BD_TZ).strftime('%Y-%m-%d')})
            return new_usage
        return 0
    except Exception as e:
        log_error(f"increment_usage error: {e}")
        return 0

def reset_daily_usage() -> None:
    try:
        client = get_supabase()
        today = datetime.now(BD_TZ).strftime('%Y-%m-%d')
        client.table('users').update({'usage_count': 0, 'last_reset': today}).neq('user_id', 0).execute()
        client.table('users').update({'new_exam_count': 0, 'last_new_exam_reset': today}).neq('last_new_exam_reset', today).execute()
        log("✅ Daily usage reset for all users")
    except Exception as e:
        log_error(f"reset_daily_usage error: {e}")

async def save_mcq(user_id: int, mcqs: List[Dict], source_type: str, prompt_type: str = 'prompt_1',
             image_file_id: str = None, image_data: bytes = None,
             chat_id: int = None, message_id: int = None, source_hash: str = None) -> str:
    quiz_id = uuid.uuid4().hex[:16]
    mcq_data = {
        'quiz_id': quiz_id, 'user_id': user_id,
        'mcqs': json.dumps(mcqs, ensure_ascii=False),
        'source_type': source_type, 'prompt_type': prompt_type,
        'image_file_id': image_file_id, 'chat_id': chat_id,
        'message_id': message_id, 'created_at': datetime.now(BD_TZ).isoformat()
    }
    if source_hash:
        mcq_data['source_hash'] = source_hash

    # ---- D1 (primary) + Supabase (overflow/mirror) via dual storage ----
    try:
        await dual_insert('mcqs', mcq_data)
    except Exception as e:
        log_error(f"save_mcq dual_insert error: {e}")

    # ---- Supabase legacy write (kept for backward compatibility / dashboards) ----
    try:
        client = get_supabase()
        sb_data = dict(mcq_data)
        try:
            client.table('mcqs').upsert(sb_data, on_conflict='quiz_id').execute()
        except Exception as col_e:
            # If source_hash column doesn't exist yet, retry without it (no data loss)
            if source_hash and 'source_hash' in str(col_e):
                sb_data.pop('source_hash', None)
                client.table('mcqs').upsert(sb_data, on_conflict='quiz_id').execute()
            else:
                raise
        mirror_insert('mcqs', sb_data)
    except Exception as e:
        log_error(f"save_mcq error: {e}")

    if image_file_id:
        if len(_image_cache) >= _IMAGE_CACHE_MAX:
            _image_cache.pop(next(iter(_image_cache)), None)  # evict oldest (dict insertion order)
        _image_cache[quiz_id] = {'image_file_id': image_file_id, 'prompt_type': prompt_type, 'chat_id': chat_id, 'message_id': message_id}
    log(f"💾 MCQ saved: {quiz_id} ({len(mcqs)} questions)")
    return quiz_id

def find_cached_mcq(source_hash: str, prompt_type: str) -> Optional[Dict]:
    """v4.0: same image+type → instant cached result (no AI call, no quota)."""
    try:
        client = get_supabase()
        result = client.table('mcqs').select('*').eq('source_hash', source_hash)\
            .eq('prompt_type', prompt_type).order('created_at', desc=True).limit(1).execute()
        if result.data and len(result.data) > 0:
            row = result.data[0]
            mcqs = json.loads(row['mcqs']) if isinstance(row['mcqs'], str) else row['mcqs']
            return {'mcqs': mcqs, 'image_file_id': row.get('image_file_id')}
        return None
    except Exception:
        return None

async def get_mcq(quiz_id: str) -> Optional[Dict]:
    # ---- D1 (primary) first ----
    try:
        row = await dual_get_mcq(quiz_id)
        if row:
            mcqs = json.loads(row['mcqs']) if isinstance(row['mcqs'], str) else row['mcqs']
            return {
                'quiz_id': row['quiz_id'], 'user_id': row['user_id'], 'mcqs': mcqs,
                'source_type': row.get('source_type', 'unknown'),
                'prompt_type': row.get('prompt_type', 'prompt_1'),
                'image_file_id': row.get('image_file_id'),
                'chat_id': row.get('chat_id'), 'message_id': row.get('message_id'),
                'created_at': row.get('created_at', 'Unknown')
            }
    except Exception as e:
        log_error(f"get_mcq dual_get_mcq error: {e}")

    # ---- Supabase fallback ----
    try:
        client = get_supabase()
        result = client.table('mcqs').select('*').eq('quiz_id', quiz_id).execute()
        if result.data and len(result.data) > 0:
            data = result.data[0]
            mcqs = json.loads(data['mcqs']) if isinstance(data['mcqs'], str) else data['mcqs']
            return {
                'quiz_id': data['quiz_id'], 'user_id': data['user_id'], 'mcqs': mcqs,
                'source_type': data.get('source_type', 'unknown'),
                'prompt_type': data.get('prompt_type', 'prompt_1'),
                'image_file_id': data.get('image_file_id'),
                'chat_id': data.get('chat_id'), 'message_id': data.get('message_id'),
                'created_at': data.get('created_at', 'Unknown')
            }
        return None
    except Exception as e:
        log_error(f"get_mcq error: {e}")
        return None

def delete_mcq(quiz_id: str, user_id: int) -> bool:
    """v4.0: /all delete button."""
    try:
        client = get_supabase()
        client.table('mcqs').delete().eq('quiz_id', quiz_id).eq('user_id', user_id).execute()
        _image_cache.pop(quiz_id, None)
        log(f"🗑️ MCQ deleted: {quiz_id} by {user_id}")
        return True
    except Exception as e:
        log_error(f"delete_mcq error: {e}")
        return False

def get_user_mcqs(user_id: int) -> List[Dict]:
    try:
        client = get_supabase()
        result = client.table('mcqs').select('*').eq('user_id', user_id).order('created_at', desc=True).execute()
        mcq_list = []
        for row in (result.data or []):
            mcqs = json.loads(row['mcqs']) if isinstance(row['mcqs'], str) else row['mcqs']
            mcq_list.append({
                'quiz_id': row['quiz_id'], 'mcqs': mcqs,
                'source_type': row.get('source_type', 'unknown'),
                'prompt_type': row.get('prompt_type', 'prompt_1'),
                'image_file_id': row.get('image_file_id'),
                'chat_id': row.get('chat_id'), 'message_id': row.get('message_id'),
                'created_at': row.get('created_at', 'Unknown')
            })
        return mcq_list
    except Exception as e:
        log_error(f"get_user_mcqs error: {e}")
        return []

def save_result(user_id: int, quiz_id: str, quiz_name: str, total: int, right: int, wrong: int, skipped: int, time_taken: int, mark: float, negative_mark: float) -> bool:
    try:
        client = get_supabase()
        row = {
            'user_id': user_id, 'quiz_id': quiz_id, 'quiz_name': quiz_name,
            'total': total, 'correct': right, 'wrong': wrong, 'skipped': skipped,
            'time_taken': time_taken, 'mark': mark, 'negative_mark': negative_mark,
            'created_at': datetime.now(BD_TZ).isoformat()
        }
        client.table('results').insert(row).execute()
        mirror_insert('results', row)
        log(f"📊 Result saved: {user_id} - {quiz_name} ({right}/{total})")
        return True
    except Exception as e:
        log_error(f"save_result error: {e}")
        return False

def get_user_results(user_id: int, limit: int = 10) -> List[Dict]:
    try:
        client = get_supabase()
        result = client.table('results').select('*').eq('user_id', user_id).order('created_at', desc=True).limit(limit).execute()
        if result.data:
            return result.data
    except Exception as e:
        log_error(f"get_user_results error: {e}")
    # backup mirror থেকে restore
    try:
        bk = get_supabase_backup()
        if bk:
            result = bk.table('results').select('*').eq('user_id', user_id).order('created_at', desc=True).limit(limit).execute()
            return result.data if result.data else []
    except Exception as e:
        log_error(f"get_user_results backup restore error: {e}")
    return []

def add_bookmark(user_id: int, cache_id: str, question_index: int, question_data: Dict, topic: str = '', page: int = 0) -> bool:
    try:
        client = get_supabase()
        now = datetime.now(BD_TZ)
        row = {
            'user_id': user_id, 'cache_id': cache_id, 'question_index': question_index,
            'question_data': json.dumps(question_data, ensure_ascii=False),
            'topic': topic, 'page': page, 'created_at': int(now.timestamp())
        }
        try:
            client.table('bookmarks').insert(row).execute()
        except Exception as e1:
            if "22P02" in str(e1) or "invalid input syntax" in str(e1):
                row['created_at'] = now.isoformat()
                client.table('bookmarks').insert(row).execute()
            else:
                raise e1
        mirror_insert('bookmarks', row)
        return True
    except Exception as e:
        log_error(f"add_bookmark error: {e}")
        return False

def get_bookmarks(user_id: int, cache_id: str) -> List[Dict]:
    try:
        client = get_supabase()
        result = client.table('bookmarks').select('*').eq('user_id', user_id).eq('cache_id', cache_id).order('question_index', desc=False).execute()
        rows = result.data
        if not rows:
            bk = get_supabase_backup()
            if bk:
                result = bk.table('bookmarks').select('*').eq('user_id', user_id).eq('cache_id', cache_id).order('question_index', desc=False).execute()
                rows = result.data
        bookmarks = []
        for row in (rows or []):
            q_data = json.loads(row['question_data']) if isinstance(row['question_data'], str) else row['question_data']
            bookmarks.append({'id': row['id'], 'question_index': row['question_index'], 'question_data': q_data, 'topic': row.get('topic', ''), 'page': row.get('page', 0)})
        return bookmarks
    except Exception as e:
        log_error(f"get_bookmarks error: {e}")
        return []

def get_all_bookmarks(user_id: int) -> List[Dict]:
    """v4.0: all bookmarks across all cache_ids for /bmexam."""
    try:
        client = get_supabase()
        result = client.table('bookmarks').select('*').eq('user_id', user_id).order('created_at', desc=True).limit(500).execute()
        rows = result.data
        if not rows:
            bk = get_supabase_backup()
            if bk:
                result = bk.table('bookmarks').select('*').eq('user_id', user_id).order('created_at', desc=True).limit(500).execute()
                rows = result.data
        out, seen = [], set()
        for row in (rows or []):
            q = json.loads(row['question_data']) if isinstance(row['question_data'], str) else row['question_data']
            key = (q.get('question', '') or '')[:120]
            if key and key not in seen:
                seen.add(key)
                out.append(q)
        return out
    except Exception as e:
        log_error(f"get_all_bookmarks error: {e}")
        return []

def delete_bookmark(user_id: int, cache_id: str, question_index: int) -> bool:
    try:
        client = get_supabase()
        client.table('bookmarks').delete().eq('user_id', user_id).eq('cache_id', cache_id).eq('question_index', question_index).execute()
        return True
    except Exception as e:
        log_error(f"delete_bookmark error: {e}")
        return False

def get_prompts_from_db() -> Dict:
    try:
        client = get_supabase()
        result = client.table('prompts').select('*').execute()
        if result.data and len(result.data) > 0:
            prompts = dict(PROMPT_MAP)  # start from built-in defaults
            for row in result.data:
                pkey = row.get('prompt_key')
                if not pkey or pkey not in PROMPT_MAP:
                    continue  # ignore unknown/corrupt keys from DB
                name = row.get('prompt_name') or PROMPT_MAP[pkey]['name']
                text = row.get('prompt_text') or PROMPT_MAP[pkey]['text']
                prompts[pkey] = {'name': name, 'text': text}
            return prompts
        return PROMPT_MAP
    except Exception as e:
        log_error(f"get_prompts_from_db error: {e}")
        return PROMPT_MAP

def update_prompt_in_db(prompt_key: str, prompt_name: str, prompt_text: str) -> bool:
    try:
        client = get_supabase()
        existing = client.table('prompts').select('prompt_key').eq('prompt_key', prompt_key).execute()
        if existing.data and len(existing.data) > 0:
            client.table('prompts').update({'prompt_name': prompt_name, 'prompt_text': prompt_text, 'updated_at': datetime.now(BD_TZ).isoformat()}).eq('prompt_key', prompt_key).execute()
        else:
            client.table('prompts').insert({'prompt_key': prompt_key, 'prompt_name': prompt_name, 'prompt_text': prompt_text, 'is_active': True, 'updated_at': datetime.now(BD_TZ).isoformat()}).execute()
        log(f"📝 Prompt updated: {prompt_key}")
        return True
    except Exception as e:
        log_error(f"update_prompt_in_db error: {e}")
        return False

def save_active_quiz(chat_id: int, quiz_data: Dict) -> bool:
    try:
        supabase_call(lambda c: c.table('active_quizzes').upsert({
            'chat_id': chat_id,
            'quiz_data': json.dumps(quiz_data, ensure_ascii=False),
            'created_at': datetime.now(BD_TZ).isoformat()
        }).execute())
        return True
    except Exception as e:
        log_error(f"save_active_quiz error: {e}")
        return False

def get_active_quiz(chat_id: int) -> Optional[Dict]:
    try:
        result = supabase_call(lambda c: c.table('active_quizzes').select('*').eq('chat_id', chat_id).execute())
        if result.data and len(result.data) > 0:
            quiz_data = json.loads(result.data[0]['quiz_data']) if isinstance(result.data[0]['quiz_data'], str) else result.data[0]['quiz_data']
            return quiz_data
        return None
    except Exception as e:
        log_error(f"get_active_quiz error: {e}")
        return None

def remove_active_quiz(chat_id: int) -> bool:
    try:
        supabase_call(lambda c: c.table('active_quizzes').delete().eq('chat_id', chat_id).execute())
        return True
    except Exception as e:
        log_error(f"remove_active_quiz error: {e}")
        return False

# ============================================================
# SECTION 11: HELPER FUNCTIONS — preserved
# ============================================================

def is_admin(user_id: int) -> bool:
    return user_id == OWNER_ID

def get_user_info(update: Update) -> Dict:
    user = update.effective_user
    return {
        'user_id': user.id,
        'first_name': user.first_name or "User",
        'username': user.username or "",
        'full_name': f"{user.first_name or ''} {user.last_name or ''}".strip()
    }

def get_ayat(score: Optional[float] = None) -> str:
    if score is not None:
        if score >= 80:
            category = random.choice(['success', 'hope'])
        elif score >= 60:
            category = random.choice(['hope', 'effort'])
        elif score >= 40:
            category = random.choice(['effort', 'patience'])
        else:
            category = random.choice(['hardship', 'patience'])
    else:
        category = random.choice(['tawakkul', 'exam', 'ibadah'])
    ayats_list = AYATS.get(category, AYATS['hope'])
    return random.choice(ayats_list)

def get_feedback(percentage: float) -> str:
    if percentage >= 90:
        return random.choice(FEEDBACKS['excellent'])
    elif percentage >= 75:
        return random.choice(FEEDBACKS['good'])
    elif percentage >= 50:
        return random.choice(FEEDBACKS['average'])
    else:
        return random.choice(FEEDBACKS['poor'])

def apply_tag_exp(mcqs: List[Dict]) -> List[Dict]:
    tag = get_setting('quiz_tag', '')
    exp_text = get_setting('quiz_exp', '')
    if not tag and not exp_text:
        return mcqs
    result = []
    for mcq in mcqs:
        m = dict(mcq)
        if tag:
            m['_tag'] = tag
        if exp_text:
            m['_exp'] = exp_text
        result.append(m)
    return result

def format_poll_question(mcq: Dict, q_num: int) -> str:
    tag = mcq.get('_tag', '')
    q = mcq.get('question', '')
    if tag:
        text = f"[{tag}]\n\n{q_num}. {q}"
    else:
        text = f"{q_num}. {q}"
    return text[:300]

def format_explanation(mcq: Dict) -> str:
    exp = mcq.get('explanation', 'ব্যাখ্যা পাওয়া যায়নি')
    suffix = mcq.get('_exp', '')
    if suffix:
        text = f"{exp}\n\n📌 {suffix}"
    else:
        text = exp
    return text[:200]

def get_prompt_display_name(prompt_type: str) -> str:
    prompt_map = get_prompts_from_db()
    if prompt_type in prompt_map:
        return prompt_map[prompt_type].get('name', prompt_type)
    return PROMPT_MAP.get(prompt_type, {}).get('name', prompt_type)

def generate_caption(user: Dict, practice_no: int, total_mcq: int, prompt_name: str = '') -> str:
    ayat = get_ayat(None)
    prompt_line = f"\n📋 Type: {prompt_name}" if prompt_name else ""
    caption = f"""🌟 স্বাগতম প্রিয় শিক্ষার্থী {user['first_name']}..!
🚀 Today Practice No: {practice_no:02d}
✅ Total MCQ: {total_mcq}{prompt_line}

{ayat}"""
    return caption

def clean_mcq_options(mcqs: List[Dict]) -> List[Dict]:
    """v4.0: ensure no A)/ক) prefixes anywhere (also for old cached sets)."""
    out = []
    for m in mcqs:
        m2 = dict(m)
        opts = m2.get('options', [])
        m2['options'] = [clean_option_prefix(o, i) for i, o in enumerate(opts)]
        out.append(m2)
    return out

def share_button(quiz_id: str, sender_id: int = 0) -> InlineKeyboardButton:
    """v4.0: Share & Challenge deep-link button with sender tracking."""
    uname = BOT_USERNAME or "atlasprepbot"
    dl = f"quiz_{quiz_id}" if not sender_id else f"quiz_{quiz_id}_c{sender_id}"
    return InlineKeyboardButton("🔗 Share & Challenge Your Friend",
                                url=f"https://t.me/share/url?url=https://t.me/{uname}?start={dl}&text=🔥 ATLAS Quiz Challenge! তুমিও Solve করে দেখাও!")

def mcq_set_keyboard(quiz_id: str, user_id: int = 0) -> List[List[InlineKeyboardButton]]:
    challenge = _challenge_map.get(user_id)
    challenger_param = ""
    if challenge and challenge.get('quiz_id') == quiz_id:
        challenger_param = f"&challenger={challenge['sender_id']}"
    return [
        [InlineKeyboardButton("📊 Poll Solve", callback_data=f"poll_{quiz_id}"), InlineKeyboardButton("📝 Quiz Solve", callback_data=f"quiz_{quiz_id}")],
        [InlineKeyboardButton("🌐 Website Exam", url=f"{GH_PAGES_EXAM_URL}?id={quiz_id}&uid={user_id}{challenger_param}"), InlineKeyboardButton("💎 Premium PDF", callback_data=f"prempdf_{quiz_id}")],
        [share_button(quiz_id, user_id)],
    ]

def _get_result_for_quiz(user_id: int, quiz_id: str) -> Optional[Dict]:
    try:
        client = get_supabase()
        r = client.table('results').select('*').eq('user_id', user_id).eq('quiz_id', quiz_id).order('created_at', desc=True).limit(1).execute()
        return r.data[0] if r.data else None
    except Exception:
        return None

async def _send_challenge_comparison(receiver_id: int, sender_id: int, quiz_id: str, receiver_result: Dict) -> None:
    try:
        sender_result = _get_result_for_quiz(sender_id, quiz_id)
        if not sender_result:
            return
        try:
            sender_info = get_supabase().table('users').select('first_name').eq('user_id', sender_id).limit(1).execute()
            sender_name = sender_info.data[0]['first_name'] if sender_info.data else f"User#{sender_id}"
        except Exception:
            sender_name = f"User#{sender_id}"
        try:
            recv_info = get_supabase().table('users').select('first_name').eq('user_id', receiver_id).limit(1).execute()
            recv_name = recv_info.data[0]['first_name'] if recv_info.data else f"User#{receiver_id}"
        except Exception:
            recv_name = f"User#{receiver_id}"
        s_correct = sender_result.get('correct', 0)
        s_wrong = sender_result.get('wrong', 0)
        s_mark = sender_result.get('mark', 0)
        s_total = sender_result.get('total', 0)
        s_time = sender_result.get('time_taken', 0)
        r_correct = receiver_result.get('correct', 0)
        r_wrong = receiver_result.get('wrong', 0)
        r_mark = receiver_result.get('mark', 0)
        r_total = receiver_result.get('total', 0)
        r_time = receiver_result.get('time_taken', 0)
        s_pct = round(s_correct / s_total * 100) if s_total else 0
        r_pct = round(r_correct / r_total * 100) if r_total else 0
        if r_mark > s_mark:
            winner, loser = recv_name, sender_name
            verdict = f"🏆 {recv_name} জিতেছে!"
        elif s_mark > r_mark:
            winner, loser = sender_name, recv_name
            verdict = f"🏆 {sender_name} জিতেছে!"
        else:
            verdict = "🤝 ড্র হয়েছে!"
        s_tstr = f"{s_time//60}m {s_time%60}s" if s_time >= 60 else f"{s_time}s"
        r_tstr = f"{r_time//60}m {r_time%60}s" if r_time >= 60 else f"{r_time}s"
        comp = (
            f"⚔️ <b>CHALLENGE COMPARISON</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>{sender_name}</b>\n"
            f"   ✅ {s_correct} | ❌ {s_wrong} | 📊 {s_mark:.2f}/{s_total} ({s_pct}%)\n"
            f"   ⏱️ {s_tstr}\n\n"
            f"👤 <b>{recv_name}</b>\n"
            f"   ✅ {r_correct} | ❌ {r_wrong} | 📊 {r_mark:.2f}/{r_total} ({r_pct}%)\n"
            f"   ⏱️ {r_tstr}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{verdict}\n━━━━━━━━━━━━━━━━━━━━━━"
        )
        try:
            await application.bot.send_message(chat_id=receiver_id, text=comp, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        try:
            await application.bot.send_message(chat_id=sender_id, text=comp, parse_mode=ParseMode.HTML)
        except Exception:
            pass
    except Exception as e:
        log_error(f"Challenge comparison error: {e}")

# ============================================================
# SECTION 12: MCQ GENERATOR (v4.0 — Multi-AI fallback + cache)
# ============================================================

# ============================================================
# STRICT SOURCE-LANGUAGE LOCK — used by generate_mcq_from_image / generate_mcq_from_text.
# bug fix (root cause of MCQs sometimes coming out in the wrong/mixed language): the old
# instruction was a single soft sentence ("Detect the language... Generate ALL questions in
# that SAME language") appended once at the end of the prompt — easy for the model to treat
# as a low-priority suggestion, especially on longer prompts or when the source itself has
# mixed languages (e.g. English scientific terms inside a Bengali paragraph). This is now a
# zero-tolerance, explicitly-prioritized rule block instead of one soft sentence.
# ============================================================
ACCURACY_AND_COUNT_LOCK = """

================================
🎯 ACCURACY + IMAGE INFO USAGE + COUNT — ABSOLUTE RULE
================================
বানান (SPELLING): প্রতিটি প্রশ্ন, অপশন ও ব্যাখ্যার বানান/স্পেলিং ১০০% নির্ভুল হতে হবে। লেখার আগে
প্রতিটি শব্দ দুইবার চেক করবে। সোর্সে ভুল বানান থাকলেও নিজের আউটপুটে শুদ্ধ বানান লিখবে (মূল
তথ্য/অর্থ পরিবর্তন না করে)।

তথ্য নির্ভুলতা: ছবিতে/সোর্সে যা লেখা আছে তার বাইরে কোনো ভুল তথ্য বা অনুমান দেওয়া যাবে না।

ইমেজের সর্বোচ্চ তথ্য ব্যবহার (image হলে): ছবির প্রতিটি অংশ — মূল লেখা, ছক/টেবিল, ডায়াগ্রাম/ছবির
ভিতরের লেবেল, ফুটনোট, মার্জিনে লেখা, হাইলাইট/আন্ডারলাইন করা অংশ — সব কিছু থেকে MCQ বানানোর
সুযোগ খুঁজে বের করবে।

MCQ সংখ্যা: গড়ে ১০ থেকে ২০টি MCQ বানাবে। তথ্য কম থাকলে ১০-১২টি, তথ্য বেশি থাকলে ১৫-২০টি।
Quality সবসময় Quantity এর আগে।

🔴 অপশন সংখ্যা (ABSOLUTE, প্রতিটি MCQ-তে): প্রতিটি MCQ-তে ঠিক ৪টি (৪টিই, কম না বেশি না) অপশন
থাকতেই হবে — A, B, C, D। কখনো ২টি বা ৩টি অপশন দিয়ে থামবে না (যেমন শুধু হ্যাঁ/না জোড়া)। ৪টি
সম্পূর্ণ, তথ্যপূর্ণ, ভিন্ন অপশন ছাড়া MCQ output-এ দেওয়া নিষেধ।
"""

STRICT_LANGUAGE_LOCK = """

════════════════════════════════
🌐 SOURCE LANGUAGE — ABSOLUTE, ZERO-TOLERANCE RULE (read this before generating anything)
════════════════════════════════
STEP 1 (mandatory, before writing a single MCQ): identify the language the source content is
actually written in. Do this per distinct block of content if the source mixes languages in
different sections — do not assume the whole source is one language from a quick glance.

STEP 2: generate the question, all options, AND the explanation for each MCQ 100% in that
SAME source language — matching script and language exactly, with these absolute rules:
❌ NEVER translate the source content into a different language, under any circumstance.
❌ NEVER default to Bengali (or any other language) out of habit — the source's actual
   language always wins, even if it's English, Bangla, Hindi, Arabic, or anything else.
❌ NEVER blend two languages within a single MCQ unless the source ITSELF genuinely mixes
   them (e.g. an English technical term inside a Bengali sentence, exactly as written in the
   source) — copy that exact mixing pattern faithfully, don't "clean it up" into one language.
❌ If the source has multiple sections in different languages, each MCQ must match the
   language of the SPECIFIC section/content it was built from — not a single language picked
   for the whole output.
✅ Numerals: preserve the digit script the source used for that specific content (Bengali
   ১২৩ stays Bengali, English 123 stays English) — do not let language handling cause a
   digit-script switch.
This rule has the HIGHEST priority in this entire prompt and overrides any language default,
example, or instruction stated anywhere else — if anything above conflicts, this rule wins."""

MNEMONIC_TABLE_LOCK = """

════════════════════════════════
🔤 MNEMONIC / ছন্দ TABLE SOURCE — VERBATIM PAIRING RULE
════════════════════════════════
সোর্সে যদি "মনে রাখার ছন্দ/কৌশল" টেবিল থাকে (একটা ছন্দের শব্দ ↔ একটা নির্দিষ্ট রোগ/টার্ম/তথ্যের
পেয়ার, যেমন "হিমুর → হিমোফিলিয়া", "রূপা → রেটিনোব্লাস্টোমা"), তাহলে:
✅ প্রতিটি mnemonic শব্দের সাথে যুক্ত রোগ/টার্মের নাম টেবিল থেকে হুবহু (verbatim, exact spelling)
   কপি করতে হবে — নিজে থেকে সংক্ষেপ, বানান পরিবর্তন, বা ভিন্ন নাম বসানো যাবে না।
✅ যদি একটি mnemonic শব্দের সাথে একাধিক রোগ/টার্ম যুক্ত থাকে (যেমন "কে → সিকল সেল অ্যানিমিয়া,
   সিস্টিক ফাইব্রোসিস"), option-এ সবগুলো টার্মই রাখতে হবে — একটা বাদ দেওয়া বা কাটছাঁট করা নিষেধ।
❌ ভুল pairing করা (এক mnemonic শব্দের সাথে অন্য শব্দের রোগ জুড়ে দেওয়া) সম্পূর্ণ নিষিদ্ধ — এটা
   সবচেয়ে বড় ভুল যা এই টাইপের সোর্সে হয়ে থাকে, তাই MCQ লেখার আগে টেবিলের প্রতিটি সারি আবার
   দেখে pairing যাচাই করবে।
❌ mnemonic শব্দ (হিমুর/বা/সার/পাশে/কে/ই/থা/রূপা টাইপ ছোট শব্দ) একা কখনো option হবে না —
   অবশ্যই "[mnemonic শব্দ] + [তার সাথে যুক্ত পূর্ণ, সঠিক রোগ/টার্মের নাম]" এই ফরম্যাটে option লিখতে হবে।"""


SELF_VERIFY_THOUGHT_LOCK = """

================================
🧠 INTERNAL MULTI-STEP VERIFICATION — DO THIS SILENTLY BEFORE WRITING FINAL JSON
================================
এই পুরো verification একটাই call/response এর ভিতরে, নিজের ভাবনায় (internal reasoning), আউটপুটে না
দেখিয়ে করবে। শুধু চূড়ান্ত JSON output দিবে — verification steps output এ লিখবে না।

THOUGHT 1 — RULE RECAP: উপরের prompt এর প্রতিটি নিয়ম (MCQ type, count, language, source-only
rule, spelling rule) নিজের মনে একবার পুনরাবৃত্তি করো।
THOUGHT 2 — DRAFT: সোর্স থেকে MCQ গুলোর একটি draft বানাও।
THOUGHT 3 — SELF-CHECK: প্রতিটি draft MCQ কে THOUGHT 1 এর নিয়মের বিপরীতে যাচাই করো — ভুল বানান,
ভুল তথ্য, ভাষা মিসম্যাচ, prompt type না মানা, বা source এর বাইরের তথ্য আছে কিনা চেক করো।
THOUGHT 4 — FIX: THOUGHT 3 তে যা ভুল পেয়েছো তা ঠিক করো, প্রয়োজনে অগ্রহণযোগ্য MCQ বাদ দাও।
THOUGHT 5 — FINAL: শুধুমাত্র THOUGHT 4 এর পর যে MCQ গুলো সব নিয়ম ১০০% মেনেছে সেগুলোই চূড়ান্ত JSON
আকারে আউটপুট দাও।
এই ৫টি thought একই call এ, অতিরিক্ত API call ছাড়াই সম্পন্ন করবে।
"""

QBM_EXTRACT_PROMPT = """YOU ARE A STRICT MCQ EXTRACTOR OPERATING IN A SPECIAL PERMANENT MODE. YOUR ONLY JOB IS TO EXTRACT MCQs THAT ALREADY EXIST ON THIS PAGE. YOU NEVER INVENT NEW QUESTIONS. FOLLOW EVERY RULE BELOW WITHOUT A SINGLE EXCEPTION, ALWAYS, ON EVERY PAGE, EVERY TIME.

════════════════════════════════
🔴 ABSOLUTE FORBIDDEN RULES (ZERO TOLERANCE)
════════════════════════════════
❌ NEVER create a new question from any text, fact, or information on the page
❌ NEVER add even ONE extra MCQ beyond what already exists on the page/image
❌ NEVER skip any existing MCQ — extract ALL of them, serially, in the exact order they appear
❌ NEVER guess an answer — only detect it from actual image/page content
❌ NEVER modify question or option text (only remove numbering prefixes)
❌ If the page has ZERO existing MCQs → output EXACTLY [] (empty array). Do NOT invent a single MCQ.
❌ If the page has exactly N existing MCQs → output EXACTLY those N. Never more, never fewer, never a "similar" or "extra" one.
❌ No question count is ever given to you and none is ever needed — extract however many genuinely exist, nothing else.
❌ This is a PERMANENT, ALWAYS-ON extraction mode — these rules apply identically to every page, every call, no matter what.

════════════════════════════════
📌 EXTRACTION RULES
════════════════════════════════
✅ Extract ALL MCQs that already exist on this page — Bangla, English, or mixed language
✅ Extract from any font style — printed, handwritten, bold, italic
✅ Extract from blurry, low quality, rotated, or scanned images
✅ Perform MULTIPLE independent internal read-throughs of the page (at least 3) and
   cross-check your own extraction before finalizing, so no existing MCQ is missed or misread.
   Pay special attention to the LAST MCQ on the page/column — it is the most commonly missed one.
   After the draft list is built, count the visible MCQs on the page and verify your list length
   matches that count exactly before finalizing.
✅ Remove question numbering only: (১., 1., Q1., Q.1, ক., a.) from question text
✅ Keep original question and option wording intact (do not paraphrase or rewrite existing text)
✅ If any obvious spelling mistake is seen, correct it — but do not alter meaning

════════════════════════════════
🎯 ANSWER DETECTION (ALL FORMATS) — triple-check before finalizing
════════════════════════════════
The correct answer MUST come from an actual source found in the page/image content.
NEVER pick/guess an answer yourself — the answer must always be traceable to one of
the source types below. Scan for ALL of these possible answer sources, in this order
of likelihood, before concluding no answer exists:

Source A — Answer marked directly on an option: circle, tick (✓), cross(✗)-elimination,
  underline, bold, highlight, star (★), or any other visual mark on one option
Source B — Answer given immediately with/after the MCQ itself (right after the question
  block, before the next question starts)
Source C — Answer table/box at the BOTTOM of the SAME page: a small table, boxed list,
  or line like "Answer: 1-A, 2-C, 3-B..." — match question number → correct option
Source D — Combined/consolidated answer key appearing SEVERAL PAGES LATER (not
  necessarily the very next page — scan forward through ALL available pages, since many
  question banks group all answers together after 2-3 pages of questions, or at the very
  end of the document): match question number exactly → correct option
Source E — Answer key on the page(s) immediately BEFORE or AFTER this one, in any of
  the above formats (marked option, inline, or boxed table)

Rules while scanning:
→ Check every source type above before deciding an answer is missing — the answer for a
  question on this page may live on a completely different page from the ones you've
  processed so far, so scan broadly, not just this single page.
→ Match strictly by question number (or exact question text if numbers are unclear/reused).
→ NEVER invent, guess, or default an answer yourself under any circumstance.
→ If — and only if — you have scanned all available pages/sources and genuinely found NO
  answer indication anywhere for that specific question → set answer as "A" and note in
  explanation "Answer not found in source". This is the last resort, never the first choice.
→ Convert whatever format the source uses (number, checkmark, circled letter, bold option,
  etc.) into the standard A/B/C/D letter for output.
→ Re-verify each detected answer against its source at least twice before finalizing —
  a wrong answer is worse than a missing one, so confirm carefully.

════════════════════════════════
🎯 OPTION ORDER (ABSOLUTE, ZERO-TOLERANCE — কখনো শাফল/পুনর্বিন্যাস/re-sort করবে না)
════════════════════════════════
- পেজে option যেই label সিস্টেমেই থাকুক (A,B,C,D / a,b,c,d / ক,খ,গ,ঘ / ১,২,৩,৪ / বুলেট/কোনো
  label ছাড়া top-to-bottom বা left-to-right) — output-এ ঠিক সেই ভিজ্যুয়াল/সোর্স পজিশনের
  ক্রমেই ১ম, ২য়, ৩য়, ৪র্থ option বসাবে output schema-র A,B,C,D slot-এ। Source-এর ১ম
  option → output A slot, ২য় → B slot, ৩য় → C slot, ৪র্থ → D slot। এটা label matching নয়,
  POSITION matching — সোর্সের label যা-ই হোক (a/ক/1/bullet), তার পজিশনই সিদ্ধান্তকারী।
- Option-এর টেক্সট কখনো reorder/sort/rearrange করবে না (বর্ণানুক্রমিক সাজানো, মান অনুযায়ী
  সাজানো — কোনোভাবেই না) — সোর্সে যেই sequence-এ ছিল ঠিক সেই sequence অক্ষুণ্ণ রাখবে।
- Option সিরিয়াল ঠিকভাবে (স্ট্রিক্টলি পজিশন ম্যাচ করে) রাখা হলে answer letter ও স্বয়ংক্রিয়ভাবে
  সঠিক সিরিয়ালেই পাওয়া যাবে — কারণ answer letter নির্ধারণ করা হয় "সঠিক উত্তরটি output-এর কোন
  position-এ আছে" তার ভিত্তিতে, সোর্সের original label-এর ভিত্তিতে না।
  উদাহরণ: সোর্সে option ক্রম গ,খ,ক,ঘ থাকলে এবং সঠিক উত্তর সোর্সের "ক" হলে — output-এ ক পজিশন
  ৩ নম্বরে থাকবে (output slot C), তাই answer = "C" (পজিশন অনুযায়ী), "A" নয়।
- প্রতিটা MCQ finalize করার আগে ৩ ধাপে verify করো (STRICT, SKIP করা যাবে না):
  ধাপ ১: output-এর ৪টা option স্লট সোর্সের ৪টা option-এর পজিশন অনুযায়ী সঠিক কি না চেক করো।
  ধাপ ২: সঠিক উত্তরের টেক্সট output-এর কোন slot-এ (A/B/C/D) বসেছে খুঁজে বের করো।
  ধাপ ৩: answer letter ঠিক সেই slot-কেই নির্দেশ করছে কি না নিশ্চিত করো — অমিল থাকলে ঠিক করো।
- সংখ্যা/সাল/তারিখ (Bengali সংখ্যা যেমন ১৯৭৬ বা English সংখ্যা যেমন 1976) অক্ষত হুবহু রাখবে —
  Bengali সংখ্যাকে English-এ বা English সংখ্যাকে Bengali-তে কখনো convert করবে না। প্রতিটা
  সংখ্যা সোর্সের সাথে digit-by-digit মিলিয়ে verify করবে (৯↔9, ৬↔6 গুলিয়ে ফেলা কড়াভাবে নিষিদ্ধ)।

════════════════════════════════
📖 উদ্দীপক (PASSAGE/STIMULUS) HANDLING — STRICT, ALWAYS ACTIVE
════════════════════════════════
- যদি কোনো প্রশ্ন বা প্রশ্নগোষ্ঠীর আগে একটা উদ্দীপক (passage/stimulus/scenario paragraph) থাকে,
  সেই উদ্দীপকটি প্রথমে identify করবে এবং তার সাথে যুক্ত প্রতিটা MCQ-কে উদ্দীপকের সাথে reply/link
  করেই ধরবে — অর্থাৎ output-এ প্রতিটা সংশ্লিষ্ট MCQ-র question টেক্সটের শুরুতে সেই উদ্দীপকের
  পূর্ণ টেক্সট জুড়ে দিতে হবে, তারপর তার নিচে সেই নির্দিষ্ট MCQ-র প্রশ্ন — যাতে প্রতিটা MCQ standalone
  ভাবে বোঝা যায় (উদ্দীপক ছাড়া প্রশ্নটা অসম্পূর্ণ থাকা উচিত নয়)।
- একই উদ্দীপকের অধীনে একাধিক MCQ থাকলে প্রতিটাতেই সেই একই উদ্দীপক পুনরায় জুড়ে দিতে হবে (কপি
  করে), প্রতিটা MCQ আলাদা আলাদা ভাবে সম্পূর্ণ (self-contained) থাকতে হবে।
- উদ্দীপক শনাক্তকরণে সতর্ক থাকবে: সাধারণ প্রশ্নের সাথে উদ্দীপক-ভিত্তিক প্রশ্ন গুলিয়ে ফেলবে না —
  passage/scenario/case-study টাইপ কনটেন্ট যা একাধিক প্রশ্নের বেস হিসেবে কাজ করছে, সেটাই উদ্দীপক।

════════════════════════════════
💡 EXPLANATION RULES (STRICT PRIORITY ORDER — follow exactly, always, in this order)
════════════════════════════════
1) If the MCQ already has an explanation/answer-reasoning written directly below or attached
   to it on the page → copy that explanation 100% VERBATIM, word-for-word, EXACTLY as written
   in the source. Do not paraphrase, shorten, or rewrite it in any way.
2) Else if there is no explanation directly under the MCQ, but the page contains other
   relevant information related to this MCQ's topic (a paragraph, note, box, table, or fact
   elsewhere on the page/related pages that relates to this question) → build the explanation
   using that relevant information, stated as direct fact (see forbidden-phrase rule below).
3) Else if there is no explanation anywhere and no relevant info anywhere on the page/source
   related to this MCQ → then, and ONLY then, generate the BEST, most relevant, factually
   accurate explanation yourself from your own real knowledge.
- Whichever of the 3 cases applies, the explanation content must always convey: why the
  correct option is correct, AND brief relevant info tied to why the other options are
  wrong/related context — except in case 1, where you copy the source explanation exactly
  as-is even if it doesn't explicitly cover the wrong options.
- Max 165 characters. Language: MUST match that specific MCQ's own source language (see the
  SOURCE LANGUAGE rule below) — never hardcoded to Bengali or any fixed language. Factually
  accurate regardless of language.
- This priority order (1 → 2 → 3) is permanent and always active — never skip a step or
  reorder it, on every single MCQ, every time.

════════════════════════════════
🧮 MATH / CHEMISTRY FORMATTING (MANDATORY, ALWAYS ACTIVE — question, options, AND explanation)
════════════════════════════════
This rule is PERMANENTLY ON for every MCQ produced, with no exceptions, regardless of subject:
- Always use proper Unicode subscript characters for chemical formula quantities and
  proper Unicode superscript characters for exponents/powers/ionic charges — NEVER raw
  underscore/caret notation, NEVER plain inline digits where a subscript/superscript belongs.
- Chemical formulas: subscript quantity numbers correctly.
  Correct: H₂O, CO₂, NaHCO₃, H₂SO₄, Ca(OH)₂, Fe₂O₃, C₆H₁₂O₆
  Wrong: H2O, CO2, NaHCO3, H2SO4 (never output these)
- Ionic charges/oxidation states: use superscript with correct sign.
  Correct: Na⁺, Ca²⁺, Fe³⁺, Cl⁻, SO₄²⁻, O²⁻
- Exponents/powers/scientific notation: superscript the exponent.
  Correct: x², 10³, a⁻¹, E=mc², 6.02×10²³, v₀, xₙ
  Wrong: x^2, 10^3, x_0 (never output caret/underscore literally)
- Units, degree symbols, and multiplication signs must be correctly formatted: °C, °F, m/s²,
  cm³, kg·m/s², use × not x for multiplication in scientific/math contexts.
- Apply this identically and consistently across the question text, all four options, AND
  the explanation — never mix correct and incorrect formatting within the same MCQ.
- Double-check every number adjacent to a letter/formula/exponent before finalizing output:
  if it should be a subscript or superscript, it MUST be rendered as one, always.

════════════════════════════════
🚫 FORBIDDEN SOURCE-REFERENCE PHRASES (PERMANENT, ALWAYS ACTIVE — question AND explanation)
════════════════════════════════
NEVER, under any circumstances, in the question text OR the explanation text, use any of
these phrase patterns (or their Bengali equivalents, or any semantically similar phrase)
that refer back to the source material itself instead of stating the fact directly:
❌ "উল্লেখিত চিত্রে" / "চিত্রে দেখা যাচ্ছে" / "বক্সে" / "ছকে" / "উদ্দীপকে" / "সারণিতে" /
   "টপিকে" / "পৃষ্ঠা নং এ" / "পৃষ্ঠায়" / "প্যাসেজে" / "অনুচ্ছেদে" / "লেখচিত্রে" / "গ্রাফে"
❌ "দেখা যাচ্ছে" / "বলা আছে" / "উল্লেখ করা আছে" / "উল্লেখ আছে" / "লক্ষ করা যায়" /
   "বর্ণনা আছে" / "দেখানো হয়েছে" / "দেওয়া আছে" / "প্রদত্ত" / "উপরে দেখানো"
❌ Any English equivalents: "as shown in the figure/box/table/diagram/passage", "shown above",
   "mentioned in the text/page", "as given", "according to the figure/table/passage above"
❌ Any phrase — in any language, any wording — that talks ABOUT the source (image/box/table/
   diagram/passage/page number/graph) instead of stating the fact/content directly and plainly.
Instead: ALWAYS state the actual fact, information, or content directly and naturally, as if
it were plain general knowledge — NEVER mention or imply that it came from "the shown
image/box/table/passage/page". This rule applies permanently, always, to every single MCQ's
question and explanation, with absolutely no exceptions, regardless of subject or source type.

════════════════════════════════
🌐 SOURCE LANGUAGE — ABSOLUTE, ZERO-TOLERANCE, PERMANENT RULE
════════════════════════════════
❌ NEVER translate, transliterate, or switch the language of ANY MCQ. The question, all four
   options, and the explanation for a given MCQ MUST be in the EXACT SAME language the source
   MCQ itself was written in on the page — character for character, script for script.
❌ NEVER blend languages within a single MCQ (e.g. Bengali question with English options, or
   vice versa) UNLESS the source itself genuinely mixes them (e.g. an English technical/
   scientific term embedded inside an otherwise-Bengali sentence, exactly as printed) — copy
   that exact mixing pattern, do not "clean it up" into one language or the other.
❌ NEVER default to Bengali (or any other language) for the explanation when the source MCQ is
   in a different language — case 3 of the EXPLANATION RULES above (self-generated explanation)
   MUST still be written in the SAME language as that specific MCQ's question/options, never a
   fixed default language.
✅ If the page contains MCQs in multiple different languages (e.g. some in Bengali, some in
   English), extract each MCQ in ITS OWN original language — the output list may legitimately
   contain MCQs in different languages side by side; that is correct behavior, not an error.
✅ Detect the language per-MCQ, not once for the whole page — do not assume every MCQ on a page
   shares the same language as the first one you read.
✅ Numerals/digits: preserve the script the source used for THAT MCQ (Bengali ১২৩ stays Bengali,
   English 123 stays English) — this is already covered above but applies doubly under this rule:
   never let language auto-detection cause a digit-script switch either.
This rule overrides any general "Bengali language" default mentioned elsewhere in this prompt —
wherever a language default is implied, the SOURCE MCQ's own actual language always wins.

════════════════════════════════
📤 OUTPUT FORMAT
════════════════════════════════
Output ONLY a valid JSON array. No extra text. No markdown. No explanation outside JSON.
If NO MCQ exists on this page → return exactly: []

[{"question":"...","options":{"A":"...","B":"...","C":"...","D":"..."},"answer":"A/B/C/D","explanation":"... (max 165 chars Bengali)"}]"""


def _has_mixed_digit_script(text: str) -> bool:
    """একই সংখ্যা token-এ Bengali+English digit মিশে থাকলে সেটা corruption সংকেত।"""
    if not text:
        return False
    bn_digits = set('০১২৩৪৫৬৭৮৯')
    for token in re.findall(r'[০-৯0-9]+', text):
        has_bn = any(c in bn_digits for c in token)
        has_en = any(c.isdigit() and c not in bn_digits for c in token)
        if has_bn and has_en:
            return True
    return False


def _qbm_parse_json(text: str) -> list:
    """Parse extractor JSON output -> list of {question, options[A-D], answer(A-D), explanation}"""
    if not text:
        return []
    t = text.strip()
    if "```json" in t:
        t = t.split("```json")[1].split("```")[0].strip()
    elif "```" in t:
        t = t.split("```")[1].split("```")[0].strip()
    try:
        m = re.search(r'\[.*\]', t, re.DOTALL)
        raw = json.loads(m.group()) if m else json.loads(t)
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    valid = []
    for mc in raw:
        try:
            q = mc.get("question", "")
            opts = mc.get("options", {})
            if not q or not opts:
                continue
            q = re.sub(r'\s*[\[\(].*?[\]\)]\s*$', '', q)
            q = re.sub(r'^\s*[\d০-৯]+\s*[.)\-:\s]+\s*', '', q)
            q = re.sub(r'^\s*[Qq]\.?\s*[\d]+\s*[.)\-:\s]*\s*', '', q)
            opts_list = [opts.get("A", ""), opts.get("B", ""), opts.get("C", ""), opts.get("D", "")]
            expl = mc.get("explanation", "")
            if _has_mixed_digit_script(q) or any(_has_mixed_digit_script(o) for o in opts_list) or _has_mixed_digit_script(expl):
                log(f"[QBM digit-integrity] Mixed Bengali/English digits detected: {q[:60]}")
            valid.append({
                "question": q.strip(),
                "options": opts_list,
                "answer": mc.get("answer", "A") if mc.get("answer") in ("A", "B", "C", "D") else "A",
                "explanation": expl
            })
        except Exception:
            continue
    return valid


def _qbm_normalize_q(question: str) -> str:
    """Whitespace/punctuation normalize করে দুইটা pass-এর একই MCQ-কে duplicate ধরার জন্য."""
    q = re.sub(r'\s+', ' ', (question or '').strip().lower())
    q = re.sub(r'[^\w\u0980-\u09FF ]+', '', q)
    return q


def _qbm_is_duplicate(norm_q: str, existing_keys: list, threshold: float = 0.85) -> bool:
    """Exact match না থাকলেও near-identical প্রশ্ন-কে duplicate হিসেবে ধরার জন্য fuzzy match।"""
    if not norm_q:
        return True
    if norm_q in existing_keys:
        return True
    for k in existing_keys:
        if not k:
            continue
        shorter, longer = (k, norm_q) if len(k) <= len(norm_q) else (norm_q, k)
        if shorter and shorter in longer and len(shorter) >= 0.7 * len(longer):
            return True
        if difflib.SequenceMatcher(None, norm_q, k).ratio() >= threshold:
            return True
    return False


def _qbm_dedup_list(mcqs: list) -> list:
    """Fuzzy-dedup a list in place order, dropping duplicate/ghost MCQs."""
    seen_keys: list = []
    out = []
    for mc in mcqs:
        key_q = _qbm_normalize_q(mc.get("question", ""))
        if not key_q:
            continue
        if not _qbm_is_duplicate(key_q, seen_keys):
            seen_keys.append(key_q)
            out.append(mc)
    return out


async def _qbm_call1_extract(image_bytes: bytes) -> list:
    """
    CALL 1 -- OWN OCR + strict-prompt MCQ extraction + inline dedup.
    Job: extract every existing MCQ on the page (option-serial strictly
    preserved), while checking-as-it-goes so no duplicate/ghost MCQ enters
    the list. Groq primary -> Gemini fallback (via _call_groq/_call_gemini,
    same provider chain already used everywhere else in this bot).
    """
    try:
        txt = await _call_groq(QBM_EXTRACT_PROMPT, image_bytes)
        if not txt:
            txt = await _call_gemini(QBM_EXTRACT_PROMPT, image_bytes)
        result = _qbm_parse_json(txt) if txt else []
        return _qbm_dedup_list(result)
    except Exception as e:
        log_error(f"[QBM Call1] failed: {e}")
        return []


async def _qbm_call2_miss_check(image_bytes: bytes, call1_mcqs: list) -> list:
    """
    CALL 2 -- connected audit of Call 1's specific output (not a fresh
    re-extraction): checks if any existing MCQ was missed (especially the
    last MCQ on the page), adds only the missed ones, then re-dedupes the
    combined list once more.
    """
    if not call1_mcqs:
        return call1_mcqs
    try:
        q_summary = "\n".join(
            f"{i+1}. {(m.get('question') or '')[:100]}" for i, m in enumerate(call1_mcqs)
        )
        prompt = f"""You already extracted these MCQs from this exact page image (Call 1 result):
{q_summary if q_summary else "(none found)"}

TASK (fast audit, connected to Call 1 -- do not redo full extraction):
1) Look at the page again and check if ANY existing MCQ was MISSED by the list above
   (especially the LAST MCQ on the page -- most commonly missed).
2) If you find missed MCQ(s), extract them in the SAME strict format (options in the exact
   source position order, A/B/C/D slots by position -- never relabeled/sorted).
2b) LANGUAGE (strict, zero-tolerance): each missed MCQ's question/options/explanation MUST be
   in that MCQ's own actual source language on the page -- never translated, never defaulted
   to a different language, never blended unless the source itself mixes languages.
3) UDDIPOK CHECK: if a missed MCQ belongs under a passage/উদ্দীপক, prepend that passage's full
   text to its question (self-contained), same as Call 1's rule.
4) Do NOT re-list MCQs already shown above. Only output NEW ones that were missed.
5) If nothing was missed, output exactly: []

Output ONLY a JSON array of the MISSED MCQs (same schema as before):
[{{"question":"...","options":{{"A":"...","B":"...","C":"...","D":"..."}},"answer":"A/B/C/D","explanation":"..."}}]"""
        txt = await _call_groq(prompt, image_bytes)
        if not txt:
            txt = await _call_gemini(prompt, image_bytes)
        missed = _qbm_parse_json(txt) if txt else []

        combined = list(call1_mcqs) + missed
        return _qbm_dedup_list(combined)
    except Exception as e:
        log_error(f"[QBM Call2] failed: {e}")
        return call1_mcqs


async def _qbm_options_dict_to_list(mcqs: list) -> list:
    """No-op placeholder retained for API symmetry -- _qbm_parse_json already
    outputs options as a list, unlike QuizBot's raw dict format."""
    return mcqs


def _qbm_answer_letter_to_index(mcqs: list) -> list:
    """_qbm_parse_json outputs answer as a LETTER ('A'/'B'/'C'/'D'), but this
    bot's poll/quiz solve expects an INTEGER index. Convert here, at the
    pipeline's exit point, so every other qbm_* helper can keep working with
    letters (matching QuizBot's internal format) right up until output."""
    letter_map = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
    out = []
    for m in mcqs:
        m2 = dict(m)
        m2['answer'] = letter_map.get(m2.get('answer', 'A'), 0)
        out.append(m2)
    return out


# v-RAM-fix: caps how many images (across ALL users) run the extraction
# pipeline at once, protecting RAM under high concurrent load on 512MB free tier.
_QBM_EXTRACT_HARD_CAP = asyncio.Semaphore(20)

async def _qbm_ram_aware_acquire():
    """Blocks until (a) a hard-cap slot is free AND (b) live RSS has headroom."""
    await _QBM_EXTRACT_HARD_CAP.acquire()
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        limit_mb = 512
        safe_ceiling_mb = int(limit_mb * 0.75)
        while True:
            rss_mb = proc.memory_info().rss / (1024 * 1024)
            if rss_mb < safe_ceiling_mb:
                return
            await asyncio.sleep(0.5)
    except ImportError:
        return


async def qbm_extract_from_image(image_bytes: bytes) -> list:
    """
    Public entry point: Call 1 (extract) -> Call 2 (miss-check), connected
    2-call pipeline, Groq primary throughout. Returns MCQs in this bot's
    standard {question, options[list], answer[int], explanation} format.
    """
    await _qbm_ram_aware_acquire()
    try:
        call1 = await _qbm_call1_extract(image_bytes)
        combined = await _qbm_call2_miss_check(image_bytes, call1)
        return _qbm_answer_letter_to_index(combined)
    finally:
        _QBM_EXTRACT_HARD_CAP.release()


async def generate_mcq_from_image(image_bytes: bytes, prompt_type: str = 'prompt_1') -> Tuple[List[Dict], Optional[str]]:
    """Generate MCQs from an image — Gemini→NVIDIA→OpenRouter chain + cache."""
    try:
        # v4.0: instant cache hit for same image+prompt_type
        src_hash = hashlib.md5(image_bytes).hexdigest() + f"_{prompt_type}"
        cached = find_cached_mcq(src_hash, prompt_type)
        if cached and cached.get('mcqs'):
            log(f"⚡ Cache hit for image (prompt: {prompt_type})")
            return clean_mcq_options(cached['mcqs']), None

        # v4.7: qbm_extract now uses QuizBot's exact 2-call connected pipeline
        # (Call 1 extract -> Call 2 miss-check), Groq primary, instead of the
        # old single-pass + up-to-5-recheck loop. Fully replaces that path.
        if prompt_type == 'qbm_extract':
            valid_mcqs = await qbm_extract_from_image(image_bytes)
            if not valid_mcqs:
                return [], "📌 এই পেইজে কোনো তৈরি MCQ (প্রশ্ন+অপশন) খুঁজে পাওয়া যায়নি।"
            log(f"✅ [QBM 2-call] Extracted {len(valid_mcqs)} MCQs from image")
            return valid_mcqs, None

        prompts = get_prompts_from_db()
        prompt_text = prompts.get(prompt_type, PROMPT_MAP.get(prompt_type, PROMPT_MAP['prompt_1']))['text']
        prompt_text = prompt_text + ACCURACY_AND_COUNT_LOCK + STRICT_LANGUAGE_LOCK + MNEMONIC_TABLE_LOCK + SELF_VERIFY_THOUGHT_LOCK

        response_text, provider = await ai_generate(prompt_text, image_bytes)
        if not response_text:
            return [], "সব AI Provider ব্যস্ত। কিছুক্ষণ পর আবার চেষ্টা করুন।"

        valid_mcqs = parse_mcq_json(response_text, prompt_type=prompt_type)
        valid_mcqs = _dedupe_mcqs(valid_mcqs)
        # v5.0: code-level count enforcement — loop retrying (not just once) until
        # MIN_MCQ reached or max attempts used, always dedupe, always hard-clamp MAX_MCQ.
        attempts = 0
        while len(valid_mcqs) < MIN_MCQ and attempts < 2:
            attempts += 1
            log(f"⚠️ Only {len(valid_mcqs)} MCQs (attempt {attempts}) — retrying for more (prompt: {prompt_type})")
            retry_prompt = prompt_text + f"\n\n🔴 আগের চেষ্টায় খুব কম প্রশ্ন এসেছে (মাত্র {len(valid_mcqs)}টি)। এবার অবশ্যই কমপক্ষে {MIN_MCQ}টি ভিন্ন, নির্ভুল বানানের MCQ বানাও, source (ছবির প্রতিটি অংশ) থেকে যথাসম্ভব বেশি তথ্য ব্যবহার করো। JSON array তে {MIN_MCQ}+ object থাকতেই হবে।"
            rt, rp = await ai_generate(retry_prompt, image_bytes)
            if rt:
                retry_mcqs = _dedupe_mcqs(parse_mcq_json(rt, prompt_type=prompt_type))
                if len(retry_mcqs) > len(valid_mcqs):
                    valid_mcqs = retry_mcqs
                    provider = rp
        if len(valid_mcqs) == 0:
            return [], "কোনো MCQ তৈরি করা যায়নি। আরো তথ্য দিন।"
        valid_mcqs = valid_mcqs[:MAX_MCQ]
        log(f"✅ Generated {len(valid_mcqs)} MCQs from image (prompt: {prompt_type}, provider: {provider})")
        return valid_mcqs, None

    except json.JSONDecodeError as e:
        log_error(f"JSON parse error: {e}")
        return [], "MCQ ফরম্যাটে সমস্যা হয়েছে। আবার চেষ্টা করুন।"
    except Exception as e:
        log_error(f"AI image generation error: {e}")
        return [], "MCQ তৈরি করতে সমস্যা হয়েছে। আবার চেষ্টা করুন।"

async def generate_mcq_from_text(text: str, prompt_type: str = 'prompt_1', maximize: bool = False) -> Tuple[List[Dict], Optional[str]]:
    """Generate MCQs from text — Multi-AI fallback chain + cache."""
    try:
        cache_suffix = f"_{prompt_type}_{'max' if maximize else 'sel'}"
        src_hash = hashlib.md5(text.encode('utf-8')).hexdigest() + cache_suffix
        cached = find_cached_mcq(src_hash, prompt_type)
        if cached and cached.get('mcqs'):
            log(f"⚡ Cache hit for text (prompt: {prompt_type}, max={maximize})")
            return clean_mcq_options(cached['mcqs']), None

        prompts = get_prompts_from_db()
        prompt_text = prompts.get(prompt_type, PROMPT_MAP.get(prompt_type, PROMPT_MAP['prompt_1']))['text']
        prompt_text = prompt_text + ACCURACY_AND_COUNT_LOCK + STRICT_LANGUAGE_LOCK + MNEMONIC_TABLE_LOCK
        if maximize:
            prompt_text += TEXT_MAX_MCQ_EXTRA
        full_prompt = f"{prompt_text}\n\n📄 INPUT TEXT:\n{text}"

        response_text, provider = await ai_generate(full_prompt, None)
        if not response_text:
            return [], "সব AI Provider ব্যস্ত। কিছুক্ষণ পর আবার চেষ্টা করুন।"

        valid_mcqs = parse_mcq_json(response_text, source_text=text, prompt_type=prompt_type)
        if 0 < len(valid_mcqs) < MIN_MCQ:
            log(f"⚠️ Only {len(valid_mcqs)} MCQs (text) — retrying for more")
            retry_prompt = full_prompt + f"\n\n🔴 আগের চেষ্টায় খুব কম প্রশ্ন এসেছে। এবার অবশ্যই কমপক্ষে ১৫টি ভিন্ন MCQ বানাও। JSON array তে ১৫+ object থাকতেই হবে।"
            rt, rp = await ai_generate(retry_prompt, None)
            if rt:
                retry_mcqs = parse_mcq_json(rt, source_text=text, prompt_type=prompt_type)
                if len(retry_mcqs) > len(valid_mcqs):
                    valid_mcqs = retry_mcqs
                    provider = rp
        if len(valid_mcqs) == 0:
            return [], "কোনো MCQ তৈরি করা যায়নি। আরো তথ্য দিন।"
        valid_mcqs = valid_mcqs[:MAX_MCQ]
        log(f"✅ Generated {len(valid_mcqs)} MCQs from text (prompt: {prompt_type}, provider: {provider})")
        return valid_mcqs, None

    except json.JSONDecodeError as e:
        log_error(f"JSON parse error: {e}")
        return [], "MCQ ফরম্যাটে সমস্যা হয়েছে। আবার চেষ্টা করুন।"
    except Exception as e:
        log_error(f"AI text generation error: {e}")
        return [], "MCQ তৈরি করতে সমস্যা হয়েছে। আবার চেষ্টা করুন।"

async def cmd_atlas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    log(f"📊 /atlas from {user['user_id']}")
    reply = update.message.reply_to_message
    poll = reply.poll if (reply and reply.poll) else None
    if not poll:
        await update.message.reply_text("❌ কোনো Poll-এ reply করে /atlas দিন!")
        return

    question = poll.question
    options = [o.text for o in poll.options]
    correct_idx = poll.correct_option_id

    wait_msg = await update.message.reply_text("⏳ ব্যাখ্যা তৈরি করা হচ্ছে...")

    opts_str = "\n".join(f"{chr(65+i)}) {o}" for i, o in enumerate(options))
    if correct_idx is not None:
        hint = f"\nসঠিক উত্তর: {chr(65+correct_idx)}"
    else:
        hint = "\nসঠিক উত্তর জানা নেই — নিজে বিশ্লেষণ করে বলো কোনটা সঠিক এবং কেন।"

    prompt = (
        "নিচের MCQ প্রশ্নের প্রতিটি অপশন নিয়ে বাংলায় স্পষ্ট ব্যাখ্যা দাও।\n"
        "কোন অপশনটি সঠিক এবং কেন, বাকি অপশনগুলো কেন ভুল — প্রতিটির জন্য আলাদা আলাদা ব্যাখ্যা দাও।\n"
        "সব তথ্য অবশ্যই ১০০% সঠিক ও ফ্যাক্ট-চেকড হতে হবে, ভুল/অনুমাননির্ভর তথ্য দেওয়া যাবে না।\n"
        "সংক্ষিপ্ত কিন্তু স্পষ্ট রাখো। সবার শেষে সঠিক উত্তর সম্পর্কে আরও কিছু অতিরিক্ত গুরুত্বপূর্ণ তথ্য/প্রসঙ্গ যুক্ত করো যাতে ইউজার টপিকটা আরও ভালোভাবে শিখতে পারে।\n"
        "Format:\n"
        "✅ সঠিক উত্তর: [option] — কারণ...\n"
        "❌ [option A]: কেন ভুল...\n"
        "❌ [option B]: কেন ভুল...\n"
        "❌ [option C]: কেন ভুল...\n\n"
        "📌 অতিরিক্ত তথ্য: (সঠিক উত্তরের টপিক নিয়ে আরও ২-৩টি গুরুত্বপূর্ণ, সঠিক তথ্য)\n\n"
        f"প্রশ্ন: {question}\n{opts_str}{hint}"
    )

    async def _edit_wait(t):
        try:
            await wait_msg.edit_text(t)
        except Exception:
            pass
    prog_task = asyncio.create_task(live_progress_task(_edit_wait, "Poll", total_eta=8))

    response_text, _ = await ai_generate(prompt, None)
    prog_task.cancel()
    if not response_text:
        await wait_msg.edit_text("❌ AI ব্যস্ত, পরে চেষ্টা করুন।")
        return

    explanation = response_text.strip()
    if len(explanation) > 4000:
        explanation = explanation[:4000] + "..."

    try:
        await wait_msg.edit_text(f"📊 <b>{question}</b>\n\n{explanation}", parse_mode=ParseMode.HTML)
    except Exception:
        await wait_msg.edit_text(f"📊 {question}\n\n{explanation}")



async def download_image(url: str) -> Optional[bytes]:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)
            if response.status_code == 200:
                return response.content
        return None
    except Exception as e:
        log_error(f"download_image error: {e}")
        return None

# ============================================================
# SECTION 12B: LIVE PROGRESS — preserved from v3.0
# ============================================================

PROGRESS_BAR_LEN = 7

def _progress_bar(pct: int) -> str:
    filled = int(round(PROGRESS_BAR_LEN * pct / 100))
    return "▰" * filled + "▱" * (PROGRESS_BAR_LEN - filled)

async def live_progress_task(edit_fn, source_label: str, total_eta: int = 8) -> None:
    """Edits a message every ~1.5s with live ETA countdown, % and MCQ count."""
    start = time.time()
    try:
        while True:
            elapsed = time.time() - start
            pct = min(94, int(elapsed / total_eta * 100))
            eta_left = max(1, int(total_eta - elapsed))
            made = max(1, int(pct / 100 * 22))
            text = (
                f"🔄 {source_label} থেকে MCQ তৈরি হচ্ছে...\n"
                f"⏱️ আনুমানিক সময়: {eta_left} সেকেন্ড\n"
                f"📊 Progress: {_progress_bar(pct)} {pct}%\n"
                f"✅ তৈরি হয়েছে: {made} টি MCQ"
            )
            try:
                await edit_fn(text)
            except Exception:
                pass
            await asyncio.sleep(1.5)
    except asyncio.CancelledError:
        pass

async def send_countdown(chat_id: int) -> None:
    """3-2-1 countdown (~1 sec total) before Poll/Quiz starts."""
    try:
        msg = await application.bot.send_message(chat_id=chat_id, text="3️⃣")
        for t in ("2️⃣", "1️⃣"):
            await asyncio.sleep(0.35)
            try:
                await msg.edit_text(t)
            except Exception:
                pass
        await asyncio.sleep(0.3)
        try:
            await msg.delete()
        except Exception:
            pass
    except Exception as e:
        log_error(f"Countdown error: {e}")

# ============================================================
# SECTION 13: COMMAND HANDLERS
# ============================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    user_id = user['user_id']
    first_name = user['first_name']
    log(f"📱 /start from {user_id} ({first_name})")
    create_user(user_id, first_name, user['username'])

    # v4.0: Share & Challenge deep link — /start quiz_<id> or /start quiz_<id>_c<sender_id>
    if context.args and context.args[0].startswith('quiz_'):
        raw = context.args[0][5:]  # remove 'quiz_' prefix
        sender_id = 0
        if '_c' in raw:
            parts = raw.rsplit('_c', 1)
            quiz_id = parts[0].strip()
            try:
                sender_id = int(parts[1])
            except (ValueError, IndexError):
                sender_id = 0
        else:
            quiz_id = raw.strip()
        if sender_id and sender_id != user_id:
            _challenge_map[user_id] = {'quiz_id': quiz_id, 'sender_id': sender_id}
        mcq_data = await get_mcq(quiz_id)
        if mcq_data:
            mcqs = mcq_data['mcqs']
            prompt_name = get_prompt_display_name(mcq_data.get('prompt_type', 'prompt_1'))
            challenge_line = ""
            if sender_id and sender_id != user_id:
                try:
                    si = get_supabase().table('users').select('first_name').eq('user_id', sender_id).limit(1).execute()
                    sn = si.data[0]['first_name'] if si.data else "Friend"
                except Exception:
                    sn = "Friend"
                challenge_line = f"\n⚔️ Challenge from: {escape_markdown(sn, version=1)}"
            text = (f"🔥 Quiz Challenge গ্রহণ করো, {first_name}!{challenge_line}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📝 Total MCQ: {len(mcqs)}\n📋 Type: {prompt_name}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n\n{get_ayat(None)}")
            kb = mcq_set_keyboard(quiz_id, user_id)
            image_file_id = mcq_data.get('image_file_id')
            try:
                if image_file_id:
                    await update.message.reply_photo(photo=image_file_id, caption=text, reply_markup=InlineKeyboardMarkup(kb))
                else:
                    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
            except Exception:
                await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
            return
        else:
            await update.message.reply_text("❌ Quiz টি পাওয়া যায়নি বা মেয়াদোত্তীর্ণ।")

    allowed, usage, limit, is_perm = check_access(user_id)
    status = "✅ Permitted" if is_perm else "🔒 Free"
    safe_name = escape_markdown(first_name, version=1)

    if is_admin(user_id):
        welcome = f"""🔐 **ADMIN PANEL**
━━━━━━━━━━━━━━━━━━━━━━
👋 Welcome, {safe_name}!

👥 **ইউজার ম্যানেজমেন্ট:**
  /permit `<user_id>` — ইউজার পারমিট (100/day)
  /permit remove `<user_id>` — পারমিট রিমুভ
  /info — ইউজার ইউসেজ রিপোর্ট

⚙️ **সেটিংস:**
  /limit `<count>` — সবার ডেইলি লিমিট
  /limit `<user_id>` `<count>` — নির্দিষ্ট ইউজার লিমিট
  /free `<count>` — ফ্রি লিমিট
  /daily `<count>` — পারমিটেড লিমিট
  /setneg `<value>` — নেগেটিভ মার্ক (-0.50)
  /settimer `<seconds>` — কুইজ টাইমার
  /tag `<text>` — কুইজ ট্যাগ (off দিলে রিমুভ)
  /exp `<text>` — এক্সপ্লানেশন suffix

📝 **প্রম্পট:**
  /prompt — প্রম্পট লিস্ট/এডিট/অ্যাড

📨 **ব্রডকাস্ট:**
  /send — কোনো মেসেজে reply দিয়ে সবাইকে পাঠান

📊 **অন্যান্য:**
  /log — আজকের এরর লগ
  /error — Latest error log (v4.0)
  /class add — ফ্রী ক্লাস অ্যাড
  /help — সব কমান্ডের বিস্তারিত

━━━━━━━━━━━━━━━━━━━━━━
📊 **Today:** {usage}/{limit} | 👥 **Status:** {status}"""
    else:
        welcome = f"""🌟 স্বাগতম {safe_name}..!

🚀 **ATLAS MCQ BOT** এ আপনাকে স্বাগতম!

📸 একটি **Image/PDF** অথবা **Text** পাঠান — আমি সাথে সাথে MCQ বানিয়ে দিবো।

📊 **আজকের ব্যবহার:** {usage}/{limit}
📋 **Status:** {status}

📋 **কমান্ড:**
  /start — বট শুরু
  /all — আপনার সব তৈরি করা MCQ দেখুন
  /timer — 🍅 Pomodoro Study Timer
  /revision — 🔁 আগের MCQ ঝালাই
  /random — 🎲 Random MCQ Practice
  /progress — 📊 নিজের অগ্রগতি দেখুন
  /report — 📈 বিগত দিনের Report
  /gpa — 🎯 MBBS GPA Score হিসাব
  /bmexam — 🔖 Bookmark MCQ দিয়ে Exam
  /class — 🎓 এটলাসের ফ্রী ক্লাস
  /bm — বুকমার্ক করা MCQ এর PDF ডাউনলোড
  /pdfc — 📸 একাধিক Image → PDF বানান
  /help — সাহায্য"""
    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN)

# ============================================================
# FEATURE: /pdfc — multi-image → single PDF (ported from QuizBot)
# ============================================================
async def cmd_pdfc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data['pdfc_collecting'] = True
    context.user_data['pdfc_imgs'] = []
    await update.message.reply_text(
        "📸 Image collection mode চালু!\n\n"
        "একটার পর একটা image পাঠাও।\n"
        "শেষ হলে /done দাও — ATLAS.pdf বানিয়ে দেব।\n\n"
        "❌ বাতিল করতে /cancel দাও।"
    )

async def cmd_pdfc_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.get('pdfc_collecting'):
        await update.message.reply_text("❌ আগে /pdfc দিয়ে image collection শুরু করো!")
        return
    imgs = context.user_data.get('pdfc_imgs', [])
    context.user_data['pdfc_collecting'] = False
    context.user_data.pop('pdfc_imgs', None)
    if not imgs:
        await update.message.reply_text("❌ কোনো image পাওয়া যায়নি!")
        return

    loading = await update.message.reply_text(f"⏳ {len(imgs)} টি image দিয়ে PDF বানানো হচ্ছে...\n📊 Progress: {_progress_bar(0)} 0%")
    try:
        from PIL import Image as PILImage
        import io as _io
        pdf_images = []
        total_imgs = len(imgs)
        last_pct = -1
        for idx, img_bytes in enumerate(imgs):
            im = PILImage.open(_io.BytesIO(img_bytes)).convert("RGB")
            pdf_images.append(im)
            pct = int((idx + 1) / total_imgs * 100)
            if pct != last_pct and (pct % 10 == 0 or idx == total_imgs - 1):
                last_pct = pct
                try:
                    await loading.edit_text(
                        f"⏳ {total_imgs} টি image দিয়ে PDF বানানো হচ্ছে...\n"
                        f"📊 Progress: {_progress_bar(pct)} {pct}%\n"
                        f"✅ প্রসেস হয়েছে: {idx+1}/{total_imgs}"
                    )
                except Exception:
                    pass

        buf = _io.BytesIO()
        pdf_images[0].save(buf, format="PDF", save_all=True, append_images=pdf_images[1:])
        pdf_bytes = buf.getvalue()
        buf2 = BytesIO(pdf_bytes)
        buf2.name = "ATLAS.pdf"

        await update.message.reply_document(
            document=buf2,
            filename="ATLAS.pdf",
            caption=f"📄 ATLAS.pdf — {len(pdf_images)} pages"
        )
        try:
            await loading.delete()
        except Exception:
            pass
    except Exception as e:
        log_error(f"pdfc PDF build error: {e}")
        await loading.edit_text(f"❌ PDF বানাতে error: {e}")

async def cmd_pdfc_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data['pdfc_collecting'] = False
    context.user_data.pop('pdfc_imgs', None)
    await update.message.reply_text("❌ Image collection বাতিল।")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    user_id = user['user_id']
    if not is_admin(user_id):
        help_text = (
            "📋 <b>সাহায্য</b>\n\n"
            "👥 <b>ইউজার কমান্ড:</b>\n"
            "  /start — বট শুরু, স্বাগতম মেসেজ\n"
            "  /all — আপনার সব তৈরি করা MCQ সেট দেখুন\n"
            "  /timer — 🍅 Pomodoro Study Timer\n"
            "  /revision — 🔁 All/Mistake/Special MCQ ঝালাই\n"
            "  /random — 🎲 সব MCQ থেকে Random Practice\n"
            "  /progress — 📊 Progress Chart + Analysis\n"
            "  /report — 📈 ৩/৭/১৫/৩০ দিনের Report\n"
            "  /gpa — 🎯 SSC+HSC GPA দিয়ে MBBS Score\n"
            "  /bmexam — 🔖 Bookmark করা MCQ দিয়ে Exam\n"
            "  /class — 🎓 এটলাসের ফ্রী ক্লাস (YouTube)\n"
            "  /bm — বুকমার্ক করা MCQ এর PDF ডাউনলোড\n\n"
            "📸 <b>কিভাবে MCQ বানাবেন:</b>\n"
            "  1. একটি Image/PDF পাঠান\n"
            "  2. MCQ টাইপ সিলেক্ট করুন (৪ ধরনের)\n"
            "  3. MCQ রেডি! Poll/Quiz/Web Exam দিন\n"
            "  4. 🧠 জ্ঞানমূলক / 💡 অনুধাবনমূলক PDF ও নিতে পারবেন!\n\n"
            "📝 <b>MCQ টাইপ:</b>\n"
            "  🩺 Medical Standard — সাধারণ মেডিকেল MCQ\n"
            "  ✅ সত্য-মিথ্যা — True/False স্টাইল\n"
            "  🔥 কঠিন প্রশ্ন — অ্যাডভান্সড লেভেল\n"
            "  🎲 Mixed — সবগুলো মিলিয়ে\n\n"
            "❓ <b>কোনো সমস্যা?</b> এডমিনের সাথে যোগাযোগ করুন।\n"
            "🔗 Owner: @rafi_somc"
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)
        return
    help_text = (
        "📋 <b>ALL COMMANDS — ADMIN</b>\n\n"
        "👥 <b>ইউজার কমান্ড:</b>\n"
        "  /start, /all, /bm, /bmexam, /gpa, /timer, /revision, /random, /progress, /report, /class, /help\n\n"
        "👨‍💼 <b>এডমিন কমান্ড:</b>\n"
        "  /permit &lt;id&gt; — পারমিট (50/day)\n"
        "  /permit remove &lt;id&gt; — পারমিট রিমুভ\n"
        "  /info — ইউজার রিপোর্ট\n"
        "  /limit &lt;count&gt; — সবার ডেইলি লিমিট\n"
        "  /limit &lt;id&gt; &lt;count&gt; — নির্দিষ্ট ইউজার\n"
        "  /free &lt;count&gt; — ফ্রি লিমিট\n"
        "  /daily &lt;count&gt; — পারমিটেড লিমিট\n"
        "  /setneg &lt;value&gt; — নেগেটিভ মার্ক\n"
        "  /settimer &lt;sec&gt; — টাইমার\n"
        "  /tag &lt;text&gt; — ট্যাগ (off=রিমুভ)\n"
        "  /exp &lt;text&gt; — exp suffix (off=রিমুভ)\n"
        "  /prompt — প্রম্পট ম্যানেজমেন্ট\n"
        "  /send — ব্রডকাস্ট\n"
        "  /log — এরর লগ\n"
        "  /error — Latest error log\n"
        "  /class add — ক্লাস অ্যাড\n"
        "  /keys — AI Key স্ট্যাটাস\n"
        "  /ping — Bot Status Dashboard\n"
        "  /live — CSV থেকে Live Quiz"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    user_id = user['user_id']
    log(f"📚 /all from {user_id}")
    mcqs_data = get_user_mcqs(user_id)
    if not mcqs_data:
        await update.message.reply_text("📭 আপনার কোনো সংরক্ষিত MCQ নেই।\n\nএকটি Image বা Text পাঠিয়ে MCQ বানান!")
        return
    await update.message.reply_text(f"📚 আপনার মোট **{len(mcqs_data)}** টি MCQ সেট আছে। লোড হচ্ছে...", parse_mode=ParseMode.MARKDOWN)
    for i, mcq_data in enumerate(mcqs_data):
        try:
            mcqs = mcq_data['mcqs']
            quiz_id = mcq_data['quiz_id']
            prompt_type = mcq_data.get('prompt_type', 'prompt_1')
            count = len(mcqs)
            created = mcq_data.get('created_at', 'Unknown')
            prompt_name = get_prompt_display_name(prompt_type)
            # v4.0: date + time both shown
            created_str = f"{created[:10]} 🕐 {created[11:16]}" if created and len(str(created)) >= 16 else (created[:10] if created else 'Unknown')
            text = f"📦 MCQ Set #{i+1}\n📝 {count} টি প্রশ্ন\n📋 Type: {prompt_name}\n🔄 Source: {mcq_data.get('source_type','text')}\n📅 {created_str}"
            keyboard = [
                [InlineKeyboardButton("📊 Poll Solve", callback_data=f"poll_{quiz_id}"), InlineKeyboardButton("📝 Quiz Solve", callback_data=f"quiz_{quiz_id}")],
                [InlineKeyboardButton("🌐 Website Exam", url=f"{GH_PAGES_EXAM_URL}?id={quiz_id}&uid={user_id}"), InlineKeyboardButton("💎 Premium PDF", callback_data=f"prempdf_{quiz_id}")],
                [InlineKeyboardButton("🗑️ Delete", callback_data=f"del_{quiz_id}"), share_button(quiz_id, user_id)],
            ]
            image_file_id = mcq_data.get('image_file_id')
            if image_file_id:
                try:
                    await update.message.reply_photo(photo=image_file_id, caption=text, reply_markup=InlineKeyboardMarkup(keyboard))
                except Exception:
                    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            await asyncio.sleep(0.5)
        except Exception as e:
            log_error(f"Error showing MCQ set {i}: {e}")
            continue

async def cmd_bm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    user_id = user['user_id']
    log(f"📑 /bm from {user_id}")
    bms = get_all_bookmarks(user_id)
    if not bms:
        await update.message.reply_text(
            "📭 আপনার কোনো Bookmark করা MCQ নেই।\n\n🌐 Website Exam এ গিয়ে 🔖 বাটনে চাপ দিয়ে প্রশ্ন Bookmark করুন!\n\n💡 Bookmark MCQ দিয়ে Exam দিতে: /bmexam"
        )
        return
    wait_msg = await update.message.reply_text(f"🔖 **Bookmark PDF তৈরি হচ্ছে...**\n📦 মোট {len(bms)} টি Bookmark MCQ\n⏱️ অনুগ্রহ করে অপেক্ষা করুন...", parse_mode=ParseMode.MARKDOWN)
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{HF_SPACE_URL}/api/bookmark-pdf",
                json={"mcqs": bms, "header_label": "ATLAS Bookmark Practice Sheet"}
            )
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and "pdf" in ct:
                pdf_bytes = resp.content
            else:
                try:
                    j = resp.json()
                    reason = j.get("message") or f"status {resp.status_code}"
                except Exception:
                    reason = f"status {resp.status_code}"
                raise Exception(f"Bookmark PDF API failed: {reason}")
        pdf_file = BytesIO(pdf_bytes)
        pdf_file.name = f"ATLAS_Bookmark_Sheet.pdf"
        await update.message.chat.send_document(
            document=pdf_file,
            caption=f"🔖 **ATLAS Bookmark Practice Sheet**\n📦 মোট {len(bms)} টি Bookmark MCQ\n🚀 সেরা গাইডলাইনে গোছানো প্রস্তুতি - এটলাস",
            parse_mode=ParseMode.MARKDOWN
        )
        try:
            await wait_msg.delete()
        except Exception:
            pass
    except Exception as e:
        log_error(f"Bookmark PDF error: {e}")
        try:
            await wait_msg.edit_text(f"❌ **Bookmark PDF তৈরি করা যায়নি**\n📋 কারণ: {str(e)[:200]}", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

# ── v4.0: /bmexam ──
async def cmd_bmexam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    user_id = user['user_id']
    log(f"🔖 /bmexam from {user_id}")
    bms = get_all_bookmarks(user_id)
    if not bms:
        await update.message.reply_text("📭 আপনার কোনো Bookmark করা MCQ নেই।\n\n🌐 Website Exam এ গিয়ে 🔖 বাটনে চাপ দিয়ে প্রশ্ন Bookmark করুন!")
        return
    _pending_input[user_id] = {'type': 'bmexam_count'}
    await update.message.reply_text(
        f"🔖 **Bookmark Exam!**\n━━━━━━━━━━━━━━━━━━━━━━\n📦 আপনার মোট Bookmark করা MCQ: **{len(bms)}** টি\n\nকয়টা প্রশ্নে Exam দিতে চান?\nসংখ্যা লিখুন (যেমন: 5/10) অথবা সব প্রশ্নে দিতে \"All\" লিখুন",
        parse_mode=ParseMode.MARKDOWN
    )

# ── v4.0: /gpa ──
async def cmd_gpa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    log(f"🎯 /gpa from {user['user_id']}")
    _pending_input[user['user_id']] = {'type': 'gpa_ssc'}
    await update.message.reply_text("🎯 **MBBS GPA Score Calculator**\n━━━━━━━━━━━━━━━━━━━━━━\n\n১) আপনার SSC GPA কত? (যেমন: 5.00)", parse_mode=ParseMode.MARKDOWN)

# ── v4.0: /error (admin) ──
async def cmd_error(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    error_file = os.path.join(LOG_DIR, f"errors_{datetime.now(BD_TZ).strftime('%Y-%m-%d')}.log")
    try:
        if os.path.exists(error_file):
            with open(error_file, "r", encoding="utf-8") as f:
                content = f.read()
            if content.strip():
                tail = content[-3800:]
                await update.message.reply_text(f"🚨 Latest Errors:\n\n{tail}")
            else:
                await update.message.reply_text("✅ আজ কোনো error নেই!")
        else:
            await update.message.reply_text("✅ আজ কোনো error নেই!")
    except Exception as e:
        await update.message.reply_text(f"❌ Log read error: {e}")

async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    try:
        client = get_supabase()
        all_rows = client.table('users').select('user_id,first_name,is_permitted,usage_count,daily_limit,practice_count').order('practice_count', desc=True).execute().data or []
    except Exception as e:
        log_error(f"/info query error: {e}")
        all_rows = []
    if not all_rows:
        await update.message.reply_text("📊 কোনো ইউজার ডাটা নেই।")
        return
    paid = sorted([r for r in all_rows if r.get('is_permitted')], key=lambda r: r.get('practice_count', 0), reverse=True)
    free = sorted([r for r in all_rows if not r.get('is_permitted')], key=lambda r: r.get('practice_count', 0), reverse=True)
    header = (
        f"📊 <b>USER USAGE REPORT</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Total Users:</b> {len(all_rows)}\n"
        f"🌟 <b>Permitted:</b> {len(paid)}\n"
        f"🔒 <b>Free:</b> {len(free)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    permitted_text = "🌟 <b>PERMITTED USERS:</b>\n"
    if not paid:
        permitted_text += "  কোনো permitted ইউজার নেই।\n"
    for i, r in enumerate(paid, 1):
        permitted_text += f"{i}. {r.get('first_name','?')} (<code>{r.get('user_id')}</code>) — 📚 {r.get('practice_count',0)} | Today {r.get('usage_count',0)}/{r.get('daily_limit',0)}\n"
    free_text = "\n🔒 <b>FREE USERS:</b>\n"
    if not free:
        free_text += "  কোনো free ইউজার নেই।\n"
    for i, r in enumerate(free, 1):
        free_text += f"{i}. {r.get('first_name','?')} (<code>{r.get('user_id')}</code>) — 📚 {r.get('practice_count',0)} | Today {r.get('usage_count',0)}/{r.get('daily_limit',0)}\n"
    full_text = header + permitted_text + free_text
    if len(full_text) <= 4000:
        await update.message.reply_text(full_text, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(header + permitted_text[:3800], parse_mode=ParseMode.HTML)
        if free_text.strip():
            await update.message.reply_text(free_text[:4000], parse_mode=ParseMode.HTML)

async def cmd_permit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage:\n`/permit <user_id>` — পারমিট\n`/permit remove <user_id>` — রিমুভ", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        if args[0].lower() == 'remove' and len(args) > 1:
            target_id = int(args[1])
            unpermit_user(target_id)
            await update.message.reply_text(f"❌ User {target_id} permit removed.")
            log(f"🔒 Permit removed: {target_id}")
        else:
            target_id = int(args[0])
            existing = get_user(target_id)
            if not existing:
                create_user(target_id, f"User_{target_id}", "")
            permit_user(target_id)
            await update.message.reply_text(f"✅ User {target_id} permitted!\n📦 Premium Access: 50 pages/day\n🔄 Reset: প্রতি ২৪ ঘণ্টায়")
            log(f"🔓 Permit granted: {target_id} (50/day premium)")
    except ValueError:
        await update.message.reply_text("❌ সঠিক User ID দিন। যেমন: /permit 123456789")

async def cmd_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage:\n`/limit <count>` — সবার\n`/limit <user_id> <count>` — নির্দিষ্ট ইউজার", parse_mode=ParseMode.MARKDOWN)
        return
    if len(args) == 1:
        count = int(args[0])
        set_setting('daily_limit', count)
        await update.message.reply_text(f"✅ সবার daily limit **{count}** সেট করা হয়েছে।", parse_mode=ParseMode.MARKDOWN)
    elif len(args) == 2:
        target_id = int(args[0])
        count = int(args[1])
        set_user_limit(target_id, count)
        await update.message.reply_text(f"✅ User `{target_id}` এর limit **{count}** সেট করা হয়েছে।", parse_mode=ParseMode.MARKDOWN)

async def cmd_free(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    args = context.args
    if not args:
        current = get_setting('free_limit', DEFAULT_FREE_LIMIT)
        await update.message.reply_text(f"বর্তমান free limit: **{current}**\nUsage: `/free <count>`", parse_mode=ParseMode.MARKDOWN)
        return
    count = int(args[0])
    set_setting('free_limit', count)
    await update.message.reply_text(f"✅ Free users **{count}** বার use করতে পারবে।", parse_mode=ParseMode.MARKDOWN)

async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    args = context.args
    if not args:
        current = get_setting('daily_limit', DEFAULT_DAILY_LIMIT)
        await update.message.reply_text(f"বর্তমান permitted daily limit: **{current}**\nUsage: `/daily <count>`", parse_mode=ParseMode.MARKDOWN)
        return
    count = int(args[0])
    set_setting('daily_limit', count)
    await update.message.reply_text(f"✅ Permitted users দৈনিক **{count}** বার use করতে পারবে।", parse_mode=ParseMode.MARKDOWN)

async def cmd_setneg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    args = context.args
    if not args:
        current = get_setting('negative_mark', DEFAULT_NEGATIVE_MARK)
        await update.message.reply_text(f"বর্তমান negative mark: **{current}**\nUsage: `/setneg -0.50`", parse_mode=ParseMode.MARKDOWN)
        return
    value = float(args[0])
    set_setting('negative_mark', value)
    await update.message.reply_text(f"✅ Negative mark **{value}** সেট করা হয়েছে।", parse_mode=ParseMode.MARKDOWN)

async def cmd_settimer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    args = context.args
    if not args:
        current = get_setting('timer_seconds', DEFAULT_TIMER)
        await update.message.reply_text(f"বর্তমান timer: **{current}** সেকেন্ড\nUsage: `/settimer 30`", parse_mode=ParseMode.MARKDOWN)
        return
    seconds = int(args[0])
    set_setting('timer_seconds', seconds)
    await update.message.reply_text(f"✅ Quiz timer **{seconds}** সেকেন্ড সেট করা হয়েছে।", parse_mode=ParseMode.MARKDOWN)

async def cmd_tag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    args = context.args
    if not args:
        current = get_setting('quiz_tag', '')
        if current:
            await update.message.reply_text(f"📌 Current tag: **[{current}]**\n\nRemove করতে: `/tag off`", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("📌 কোনো tag সেট নেই।\nUsage: `/tag ExamName`", parse_mode=ParseMode.MARKDOWN)
        return
    if args[0].lower() == 'off':
        set_setting('quiz_tag', '')
        await update.message.reply_text("✅ Tag remove করা হয়েছে।")
    else:
        tag = ' '.join(args)
        set_setting('quiz_tag', tag)
        await update.message.reply_text(f"✅ Tag সেট: **[{tag}]**\n\nসব Quiz/Poll/Exam এ দেখাবে।", parse_mode=ParseMode.MARKDOWN)

async def cmd_exp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    args = context.args
    if not args:
        current = get_setting('quiz_exp', '')
        if current:
            await update.message.reply_text(f"📝 Current exp: **{current}**\n\nRemove করতে: `/exp off`", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("📝 কোনো exp text সেট নেই।\nUsage: `/exp ExamName`", parse_mode=ParseMode.MARKDOWN)
        return
    if args[0].lower() == 'off':
        set_setting('quiz_exp', '')
        await update.message.reply_text("✅ Exp text remove করা হয়েছে।")
    else:
        exp_text = ' '.join(args)
        set_setting('quiz_exp', exp_text)
        await update.message.reply_text(f"✅ Exp text সেট: **{exp_text}**", parse_mode=ParseMode.MARKDOWN)

async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    error_file = os.path.join(LOG_DIR, f"errors_{datetime.now(BD_TZ).strftime('%Y-%m-%d')}.log")
    try:
        if os.path.exists(error_file):
            with open(error_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if lines:
                text = "📋 **Recent Errors:**\n\n" + "".join(lines[-15:])
                if len(text) > 4000:
                    text = text[-4000:]
                await update.message.reply_text(text)
            else:
                await update.message.reply_text("✅ আজ কোনো error নেই!")
        else:
            await update.message.reply_text("✅ আজ কোনো error নেই!")
    except Exception as e:
        await update.message.reply_text(f"❌ Log read error: {e}")

async def cmd_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    prompts = get_prompts_from_db()
    keyboard = []
    for key, prompt_data in prompts.items():
        name = prompt_data.get('name', key)
        keyboard.append([
            InlineKeyboardButton(f"📝 {name}", callback_data=f"editprompt_{key}"),
            InlineKeyboardButton("👁️ View", callback_data=f"viewprompt_{key}")
        ])
    keyboard.append([InlineKeyboardButton("➕ Add New Prompt", callback_data="addprompt")])
    await update.message.reply_text(
        "⚙️ **PROMPT MANAGEMENT**\n━━━━━━━━━━━━━━━━━━━━━━\n\nবর্তমান প্রম্পটসমূহ। এডিট/ভিউ করতে বাটনে ক্লিক করুন।",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
    )

async def cmd_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    replied_msg = update.message.reply_to_message
    if not replied_msg:
        await update.message.reply_text("📨 **Broadcast Usage:**\n\n1. একটি মেসেজ/ইমিজ পাঠান\n2. সেই মেসেজে reply দিয়ে `/send` দিন\n3. সবার কাছে মেসেজটি পাঠানো হবে", parse_mode=ParseMode.MARKDOWN)
        return
    users = get_all_users()
    if not users:
        await update.message.reply_text("❌ কোনো ইউজার নেই।")
        return
    keyboard = [[InlineKeyboardButton("✅ হ্যাঁ, পাঠান", callback_data="confirm_send"), InlineKeyboardButton("❌ বাতিল", callback_data="cancel_send")]]
    context.user_data['broadcast_msg'] = replied_msg
    context.user_data['broadcast_users'] = users
    await update.message.reply_text(
        f"📨 **Broadcast Confirm**\n\nসবার কাছে মেসেজ পাঠানো হবে।\n👥 Total Users: **{len(users)}**\n\nContinue?",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
    )

# ============================================================
# SECTION 14: MESSAGE HANDLERS
# ============================================================

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    user_id = user['user_id']

    # v4.1: /pdfc image-collection mode check (ported from QuizBot)
    if context.user_data.get('pdfc_collecting'):
        try:
            if update.message.photo:
                photo = update.message.photo[-1]
                file = await context.bot.get_file(photo.file_id)
                image_bytes = bytes(await file.download_as_bytearray())
            elif update.message.document and (update.message.document.mime_type or "").startswith("image"):
                file = await context.bot.get_file(update.message.document.file_id)
                image_bytes = bytes(await file.download_as_bytearray())
            else:
                await update.message.reply_text("❌ দয়া করে একটি Image পাঠান (PDF collection mode চালু আছে)।")
                return
            context.user_data.setdefault('pdfc_imgs', []).append(image_bytes)
            count = len(context.user_data['pdfc_imgs'])
            await update.message.reply_text(f"✅ Image {count} save হয়েছে! (আরো দাও বা /done)")
        except Exception as e:
            log_error(f"pdfc image save error: {e}")
            await update.message.reply_text(f"❌ Image save error: {e}")
        return

    log(f"🖼️ Image from {user_id} ({user['first_name']})")
    allowed, usage, limit, is_perm = check_access(user_id)
    if not allowed:
        if is_perm:
            await update.message.reply_text(f"❌ আপনার আজকের লিমিট ({limit}) শেষ। আগামীকাল আবার চেষ্টা করুন।")
        else:
            await update.message.reply_text(PREMIUM_MSG)
        return
    try:
        # v4.0: INSTANT acknowledgment (sub-second perceived response)
        instant_msg = await update.message.reply_text("⚡ Image পেয়েছি! প্রসেস হচ্ছে...")
        if update.message.photo:
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            image_bytes = bytes(await file.download_as_bytearray())
            file_id = photo.file_id
        elif update.message.document:
            document = update.message.document
            file = await context.bot.get_file(document.file_id)
            image_bytes = bytes(await file.download_as_bytearray())
            file_id = document.file_id
        else:
            await instant_msg.edit_text("❌ দয়া করে একটি Image বা PDF পাঠান।")
            return
        context.user_data['pending_image'] = image_bytes
        context.user_data['pending_image_file_id'] = file_id
        try:
            await instant_msg.delete()
        except Exception:
            pass
        prompts = get_prompts_from_db()
        keyboard = []
        for key, prompt_data in prompts.items():
            name = prompt_data.get('name', key)
            keyboard.append([InlineKeyboardButton(name, callback_data=f"genmcq_{key}")])
        # v4.0: জ্ঞানমূলক / অনুধাবনমূলক buttons (image direct generation)
        keyboard.append([
            InlineKeyboardButton("🧠 জ্ঞানমূলক প্রশ্ন", callback_data="qaimg_k"),
            InlineKeyboardButton("💡 অনুধাবনমূলক প্রশ্ন", callback_data="qaimg_c"),
        ])
        await update.message.reply_photo(
            photo=image_bytes,
            caption=f"""🌟 স্বাগতম {user['first_name']}..!

📸 আপনার Image থেকে MCQ বানাতে MCQ টাইপ সিলেক্ট করুন:

🩺 Medical Standard — সাধারণ মেডিকেল MCQ
✅ সত্য-মিথ্যার প্রশ্ন — True/False ফরম্যাট
🔥 কঠিন প্রশ্ন — অ্যাডভান্সড লেভেল
🎲 Mixed — সবগুলো মিলিয়ে

🧠 জ্ঞানমূলক / 💡 অনুধাবনমূলক — সৃজনশীল PDF""",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        log_error(f"Image handler error: {e}")
        await safe_user_reply(update.message)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    user_id = user['user_id']
    text = update.message.text
    log(f"📝 Text from {user_id} ({len(text)} chars)")
    lines = [line for line in text.split('\n') if line.strip()]
    word_count = len(text.split())
    if len(lines) <= 3 and word_count < 30:
        await update.message.reply_text(
            "❌ **দু:খিত!** 😕\n\nআপনার Text এ Proper info নেই!\nআরো তথ্য দিন, আমি MCQ Practice Tool বানিয়ে দিবো 😃\n\n📝 **টিপস:**\n• কমপক্ষে ৪-৫ লাইন লিখুন\n• বিস্তারিত তথ্য দিন\n• গুরুত্বপূর্ণ পয়েন্ট উল্লেখ করুন\n• ৩০+ শব্দ দিন"
        )
        return
    allowed, usage, limit, is_perm = check_access(user_id)
    if not allowed:
        if is_perm:
            await update.message.reply_text(f"❌ আপনার আজকের লিমিট ({limit}) শেষ। আগামীকাল আবার চেষ্টা করুন।")
        else:
            await update.message.reply_text(PREMIUM_MSG)
        return
    context.user_data['pending_text'] = text
    keyboard = [
        [InlineKeyboardButton("⚡ Maximum MCQ", callback_data="txtmcq_max")],
        [InlineKeyboardButton("⚡ Selected MCQ", callback_data="txtmcq_sel")],
    ]
    await update.message.reply_text(
        f"📝 **Text পেয়েছি!** ({len(lines)} লাইন, {word_count} শব্দ)\n\n"
        "MCQ টাইপ সিলেক্ট করুন:\n\n"
        "⚡ **Maximum MCQ** — প্রতিটি লাইন থেকে সর্বোচ্চ সংখ্যক MCQ\n"
        "⚡ **Selected MCQ** — গুরুত্বপূর্ণ তথ্য থেকে নির্বাচিত MCQ\n\n"
        "⏱️ Response Time: 3-8 sec",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

# ============================================================
# SECTION 15: CALLBACK HANDLERS
# ============================================================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data
    user = query.from_user
    chat_id = query.message.chat_id
    log(f"🔘 Callback: {data} from {user.id}")
    try:
        await query.answer()  # instant feedback to user
    except Exception as e:
        log(f"⚠️ query.answer() failed (stale/expired callback): {e}")
    try:
        if data.startswith("txtmcq_"):
            await handle_text_mcq_generation(query, data.replace("txtmcq_", ""), context)
        elif data.startswith("genmcq_"):
            await handle_mcq_generation(query, data.replace("genmcq_", ""), context)
        elif data.startswith("qaimg_"):
            await handle_creative_from_pending(query, data.replace("qaimg_", ""), context)
        elif data.startswith("qbm_"):
            await handle_qbm_extract(query, data.replace("qbm_", ""), user)
        elif data.startswith("crpdf_k_"):
            await handle_creative_pdf(query, data.replace("crpdf_k_", ""), "knowledge")
        elif data.startswith("crpdf_c_"):
            await handle_creative_pdf(query, data.replace("crpdf_c_", ""), "comprehension")
        elif data.startswith("poll_"):
            await handle_poll_solve(query, data.replace("poll_", ""), user)
        elif data.startswith("quiz_"):
            await handle_quiz_start(query, data.replace("quiz_", ""), user, chat_id)
        elif data.startswith("startquiz_"):
            await send_first_question(query, chat_id)
        elif data.startswith("again_"):
            await handle_poll_solve(query, data.replace("again_", ""), user)
        elif data.startswith("newp_"):
            await handle_new_practice(query, user, 'poll', data.replace("newp_", ""))
        elif data.startswith("newq_"):
            await handle_new_practice(query, user, 'quiz', data.replace("newq_", ""))
        elif data.startswith("retake_"):
            await handle_quiz_start(query, data.replace("retake_", ""), user, chat_id)
        elif data.startswith("mistake_"):
            await handle_mistake_practice(query, user, chat_id, data.replace("mistake_", ""))
        elif data.startswith("back_"):
            await handle_back_to_source(query, data.replace("back_", ""))
        elif data.startswith("del_"):
            quiz_id = data.replace("del_", "")
            kb = [[InlineKeyboardButton("✅ হ্যাঁ, Delete", callback_data=f"delc_{quiz_id}"),
                   InlineKeyboardButton("↩️ না", callback_data="delno")]]
            await query.message.reply_text("🗑️ এই MCQ সেটটি Delete করবেন? এটি আর ফেরত আসবে না।", reply_markup=InlineKeyboardMarkup(kb), reply_to_message_id=query.message.message_id)
        elif data.startswith("delc_"):
            quiz_id = data.replace("delc_", "")
            ok = delete_mcq(quiz_id, user.id)
            try:
                await query.message.delete()
            except Exception:
                pass
            if ok:
                orig = query.message.reply_to_message
                if orig:
                    try:
                        await orig.delete()
                    except Exception:
                        pass
                else:
                    await context.bot.send_message(chat_id=chat_id, text="✅ MCQ সেট Delete করা হয়েছে।")
            else:
                await context.bot.send_message(chat_id=chat_id, text="❌ Delete করা যায়নি। আবার চেষ্টা করুন।")
        elif data == "delno":
            try:
                await query.message.delete()
            except Exception:
                await query.message.edit_text("↩️ Delete বাতিল করা হয়েছে।")
        elif data.startswith("editprompt_"):
            await handle_prompt_edit_start(query, data.replace("editprompt_", ""))
        elif data.startswith("viewprompt_"):
            await handle_prompt_view(query, data.replace("viewprompt_", ""))
        elif data == "addprompt":
            await handle_prompt_add(query)
        elif data.startswith("pomo"):
            await handle_pomodoro_callback(query, data)
        elif data.startswith("rev_"):
            await handle_revision_mode(query, data.replace("rev_", ""))
        elif data.startswith("rep_"):
            await handle_report_days(query, int(data.replace("rep_", "")))
        elif data.startswith("cls_s_"):
            await handle_class_subject(query, int(data.replace("cls_s_", "")), context)
        elif data.startswith("prempdf_"):
            await handle_premium_pdf(query, data.replace("prempdf_", ""))
        elif data == "confirm_send":
            await handle_broadcast_confirm(query, context)
        elif data == "cancel_send":
            await query.message.edit_text("❌ Broadcast বাতিল করা হয়েছে।")
        else:
            log(f"⚠️ Unknown callback: {data}")
    except Exception as e:
        log_error(f"Callback error ({data}): {e}")
        await safe_user_reply(query.message)

# ============================================================
# SECTION 16: MCQ GENERATION HANDLER + v4.0 CREATIVE PDF
# ============================================================

TEXT_MAX_MCQ_EXTRA = """

🔴 MANDATORY RULES (কোনোটাই skip করা যাবে না):
1. INPUT TEXT-এর প্রতিটি লাইন/তথ্য থেকে MUST অন্তত একটি MCQ বানাতে হবে — কোনো লাইন বাদ দেওয়া যাবে না। যত বেশি লাইন, তত বেশি MCQ — সর্বোচ্চ সংখ্যক MCQ বানানোই লক্ষ্য (সর্বনিম্ন ২৫-৩৫টি)।
2. Explanation-এ সঠিক answer confirm করার পাশাপাশি সেই তথ্যের ঠিক আশেপাশের (আগের/পরের লাইনের) source text থেকে অতিরিক্ত related info যোগ করতে হবে — শুধু answer repeat করা চলবে না।
3. সঠিক answer (A/B/C/D) প্রতিটি প্রশ্নে ভিন্ন ভিন্ন option-এ থাকতে হবে — কখনোই sequential pattern বা একই option বারবার না।
4. যত ধরনের সম্ভব MCQ variety বানাও — direct fact, definition, cause-effect, comparison, fill-in-the-blank style, "কোনটি সঠিক নয়" ধরনের প্রশ্ন — সব ধরনের প্রশ্ন mix করে বানাও, শুধু এক প্যাটার্নে আটকে থেকো না।
5. ৪টি option, একটি সঠিক, বাকি ৩টি plausible কিন্তু ভুল distractor (random/অর্থহীন option চলবে না)।"""

async def cmd_txt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/txt — text message-এ reply করে auto max-mode MCQ generate করে, কোনো button ছাড়াই।"""
    reply = update.message.reply_to_message
    if not reply or not reply.text:
        await update.message.reply_text("❌ Text message-এ reply করে /txt দিন!")
        return
    text = reply.text
    user = get_user_info(update)
    user_id = user['user_id']
    lines = [line for line in text.split('\n') if line.strip()]
    word_count = len(text.split())
    if len(lines) <= 3 and word_count < 30:
        await update.message.reply_text(
            "❌ **দু:খিত!** 😕\n\nএই Text এ Proper info নেই!\nআরো তথ্য দিন।\n\n"
            "📝 কমপক্ষে ৪-৫ লাইন, ৩০+ শব্দ লাগবে।"
        )
        return
    allowed, usage, limit, is_perm = check_access(user_id)
    if not allowed:
        if is_perm:
            await update.message.reply_text(f"❌ আপনার আজকের লিমিট ({limit}) শেষ। আগামীকাল আবার চেষ্টা করুন।")
        else:
            await update.message.reply_text(PREMIUM_MSG)
        return

    status = await update.message.reply_text("⏳ Text থেকে MCQ তৈরি হচ্ছে...")
    async def _edit_txt(t):
        try:
            await status.edit_text(t)
        except Exception:
            pass
    prog_task = asyncio.create_task(live_progress_task(_edit_txt, "Text", total_eta=8))
    try:
        mcqs, error = await generate_mcq_from_text(text, 'prompt_1', maximize=True)
        prog_task.cancel()
        if error:
            await status.edit_text(f"❌ MCQ বানাতে সমস্যা হয়েছে: {error}\n\nআবার চেষ্টা করুন।")
            return
        if not mcqs:
            await status.edit_text("❌ কোনো MCQ তৈরি হয়নি। আরো তথ্য দিন।")
            return
        src_hash = hashlib.md5(text.encode('utf-8')).hexdigest() + "_prompt_1_max"
        quiz_id = await save_mcq(user_id=user_id, mcqs=apply_tag_exp(clean_mcq_options(mcqs)), source_type='text', prompt_type='prompt_1', image_file_id=None, chat_id=None, message_id=None, source_hash=src_hash)
        new_usage = increment_usage(user_id)
        user_data = get_user(user_id)
        practice_no = user_data.get('practice_count', 1) if user_data else 1
        caption = generate_caption({'first_name': user.get('first_name') or 'User'}, practice_no, len(mcqs), "Maximum MCQ")
        allowed2, usage2, limit2, is_perm2 = check_access(user_id)
        keyboard = mcq_set_keyboard(quiz_id, user_id)
        full_caption = f"{caption}\n\n📊 আজকের ব্যবহার: {new_usage}/{limit2}"
        await status.edit_text(full_caption, reply_markup=InlineKeyboardMarkup(keyboard))
        try:
            await status.pin(disable_notification=True)
            client = get_supabase()
            client.table('mcqs').update({'chat_id': status.chat_id, 'message_id': status.message_id}).eq('quiz_id', quiz_id).execute()
        except Exception as e:
            log_error(f"Pin message failed: {e}")
        log(f"✅ /txt MCQ generated: {quiz_id} ({len(mcqs)} questions)")
    except Exception as e:
        prog_task.cancel()
        log_error(f"/txt handler error: {e}")
        try:
            await status.edit_text(BUSY_MSG)
        except Exception:
            pass

async def handle_text_mcq_generation(query, mode: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = query.from_user
    user_id = user.id
    text = context.user_data.get('pending_text')
    if not text:
        await query.message.edit_text("❌ Text ডাটা পাওয়া যায়নি। আবার Text পাঠান।")
        return
    async def _edit_txt(t):
        try:
            await query.message.edit_text(t)
        except Exception:
            pass
    prog_task = asyncio.create_task(live_progress_task(_edit_txt, "Text", total_eta=8))
    try:
        is_max = (mode == "max")
        mcqs, error = await generate_mcq_from_text(text, 'prompt_1', maximize=is_max)
        prog_task.cancel()
        if error:
            await query.message.edit_text(f"❌ MCQ বানাতে সমস্যা হয়েছে: {error}\n\nআবার চেষ্টা করুন।")
            return
        if not mcqs:
            await query.message.edit_text("❌ কোনো MCQ তৈরি হয়নি। আরো তথ্য দিন।")
            return
        src_hash = hashlib.md5(text.encode('utf-8')).hexdigest() + f"_prompt_1_{'max' if is_max else 'sel'}"
        quiz_id = await save_mcq(user_id=user_id, mcqs=apply_tag_exp(clean_mcq_options(mcqs)), source_type='text', prompt_type='prompt_1', image_file_id=None, chat_id=None, message_id=None, source_hash=src_hash)
        new_usage = increment_usage(user_id)
        user_data = get_user(user_id)
        practice_no = user_data.get('practice_count', 1) if user_data else 1
        mode_label = "Maximum MCQ" if is_max else "Selected MCQ"
        caption = generate_caption({'first_name': user.first_name or 'User'}, practice_no, len(mcqs), mode_label)
        allowed, usage, limit, is_perm = check_access(user_id)
        keyboard = mcq_set_keyboard(quiz_id, user_id)
        full_caption = f"{caption}\n\n📊 আজকের ব্যবহার: {new_usage}/{limit}"
        await query.message.edit_text(full_caption, reply_markup=InlineKeyboardMarkup(keyboard))
        try:
            await query.message.pin(disable_notification=True)
            client = get_supabase()
            client.table('mcqs').update({'chat_id': query.message.chat_id, 'message_id': query.message.message_id}).eq('quiz_id', quiz_id).execute()
        except Exception as e:
            log_error(f"Pin message failed: {e}")
        context.user_data.pop('pending_text', None)
        log(f"✅ Text MCQ generated ({mode}): {quiz_id} ({len(mcqs)} questions)")
    except Exception as e:
        prog_task.cancel()
        log_error(f"Text MCQ handler error: {e}")
        try:
            await query.message.edit_text(BUSY_MSG)
        except Exception:
            pass

async def handle_mcq_generation(query, prompt_type: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = query.from_user
    user_id = user.id
    image_bytes = context.user_data.get('pending_image')
    if not image_bytes:
        await query.message.reply_text("❌ ইমেজ ডাটা পাওয়া যায়নি। আবার ইমেজ পাঠান।")
        return
    async def _edit_cap(t):
        await query.message.edit_caption(caption=t)
    prog_task = asyncio.create_task(live_progress_task(_edit_cap, "Image", total_eta=8))
    try:
        mcqs, error = await generate_mcq_from_image(image_bytes, prompt_type)
        prog_task.cancel()
        if prompt_type == 'qbm_extract' and (error or not mcqs):
            await query.message.edit_caption(
                caption="📌 এই পেইজে কোনো তৈরি MCQ (প্রশ্ন+অপশন) খুঁজে পাওয়া যায়নি।\n\n"
                        "💡 এই অপশনটি শুধু পেইজে already ছাপানো MCQ খুঁজে বের করে — "
                        "নতুন MCQ বানায় না। এই পেইজে যদি সত্যিই কোনো MCQ ছাপা না থাকে, "
                        "তাহলে অন্য কোনো টাইপ (Medical Standard, সত্য-মিথ্যা ইত্যাদি) সিলেক্ট করে "
                        "নতুন MCQ বানিয়ে নিতে পারেন।"
            )
            return
        if error:
            await query.message.edit_caption(caption=f"❌ {error}")
            return
        if not mcqs:
            await query.message.edit_caption(caption="❌ কোনো MCQ তৈরি হয়নি। আরো তথ্য দিন।")
            return
        image_file_id = context.user_data.get('pending_image_file_id', '')
        src_hash = hashlib.md5(image_bytes).hexdigest() + f"_{prompt_type}"
        quiz_id = await save_mcq(user_id=user_id, mcqs=apply_tag_exp(clean_mcq_options(mcqs)), source_type='image', prompt_type=prompt_type, image_file_id=image_file_id, chat_id=None, message_id=None, source_hash=src_hash)
        new_usage = increment_usage(user_id)
        user_data = get_user(user_id)
        practice_no = user_data.get('practice_count', 1) if user_data else 1
        prompt_name = get_prompt_display_name(prompt_type)
        allowed, usage, limit, is_perm = check_access(user_id)
        caption = generate_caption({'first_name': user.first_name or 'User'}, practice_no, len(mcqs), prompt_name)
        keyboard = mcq_set_keyboard(quiz_id, user_id)
        full_caption = f"{caption}\n\n📊 আজকের ব্যবহার: {new_usage}/{limit}"
        await query.message.edit_caption(caption=full_caption, reply_markup=InlineKeyboardMarkup(keyboard))
        try:
            await query.message.pin(disable_notification=True)
            pinned_msg_id = query.message.message_id
            pinned_chat_id = query.message.chat_id
            client = get_supabase()
            client.table('mcqs').update({'chat_id': pinned_chat_id, 'message_id': pinned_msg_id}).eq('quiz_id', quiz_id).execute()
            if quiz_id in _image_cache:
                _image_cache[quiz_id]['chat_id'] = pinned_chat_id
                _image_cache[quiz_id]['message_id'] = pinned_msg_id
        except Exception as e:
            log_error(f"Pin message failed: {e}")
        context.user_data.pop('pending_image', None)
        context.user_data.pop('pending_image_file_id', None)
        log(f"✅ MCQ generated: {quiz_id} ({len(mcqs)} questions, prompt: {prompt_type})")
    except Exception as e:
        prog_task.cancel()
        log_error(f"MCQ generation handler error: {e}")
        try:
            await query.message.edit_caption(caption=BUSY_MSG)
        except Exception:
            pass

# ── v4.0: Creative (জ্ঞানমূলক/অনুধাবনমূলক) from fresh image ──
async def handle_creative_from_pending(query, ctype_short: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User pressed জ্ঞানমূলক/অনুধাবনমূলক on a freshly sent image:
       1) save image as a lightweight mcqs row (for file_id reference)
       2) call exam_server creative-pdf API
    """
    user = query.from_user
    ctype = "knowledge" if ctype_short == "k" else "comprehension"
    label = "🧠 জ্ঞানমূলক" if ctype == "knowledge" else "💡 অনুধাবনমূলক"
    image_file_id = context.user_data.get('pending_image_file_id', '')
    if not image_file_id:
        await query.message.reply_text("❌ ইমেজ ডাটা পাওয়া যায়নি। আবার ইমেজ পাঠান।")
        return
    # Save a stub row so exam_server can resolve image by quiz_id
    quiz_id = await save_mcq(user_id=user.id, mcqs=[], source_type=f'creative_{ctype}', prompt_type='prompt_1',
                       image_file_id=image_file_id, chat_id=query.message.chat_id, message_id=query.message.message_id)
    await handle_creative_pdf(query, quiz_id, ctype)

def _normalize_qbm_answers(mcqs: List[Dict]) -> List[Dict]:
    """v4.3 fix: qbm_extract prompt outputs answer as a LETTER string ('A'/'B'/'C'/'D'),
    but poll/quiz solve expects an integer option index. Without this conversion,
    int('A') raises ValueError and silently defaults to 0 (always shows option A as
    correct, ignoring the actual answer from the page). Converts letter -> index,
    and safely passes through already-integer answers untouched."""
    letter_map = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'ক': 0, 'খ': 1, 'গ': 2, 'ঘ': 3}
    out = []
    for m in mcqs:
        m2 = dict(m)
        ans = m2.get('answer', 0)
        if isinstance(ans, str):
            ans_clean = ans.strip().upper().rstrip('.):')
            m2['answer'] = letter_map.get(ans_clean, 0)
        else:
            try:
                m2['answer'] = int(ans)
            except (TypeError, ValueError):
                m2['answer'] = 0
        out.append(m2)
    return out

async def handle_qbm_extract(query, quiz_id: str, user) -> None:
    """v4.3: 'শুধুমাত্র পেইজে থাকা MCQ' — QuizBot's /qbm logic ported: only
    extracts existing MCQs already printed on the page, never generates
    new ones. Uses qbm_extract prompt with strict extract-only rules."""
    mcq_data = await get_mcq(quiz_id)
    if not mcq_data or not mcq_data.get('image_file_id'):
        await query.message.reply_text("❌ মূল ইমেজ পাওয়া যায়নি।")
        return
    image_file_id = mcq_data['image_file_id']
    wait_msg = await query.message.reply_text(
        "📌 **পেইজে থাকা MCQ খোঁজা হচ্ছে...**\n⏱️ অনুগ্রহ করে অপেক্ষা করুন...",
        parse_mode=ParseMode.MARKDOWN
    )
    async def _edit_wait(t):
        await wait_msg.edit_text(t)
    prog_task = asyncio.create_task(live_progress_task(_edit_wait, "Page", total_eta=12))
    try:
        file = await application.bot.get_file(image_file_id)
        image_bytes = bytes(await file.download_as_bytearray())
    except Exception as e:
        prog_task.cancel()
        log_error(f"QBM image download failed: {e}")
        await wait_msg.edit_text("❌ ইমেজ ডাউনলোড করতে সমস্যা হয়েছে।")
        return

    mcqs, error = await generate_mcq_from_image(image_bytes, 'qbm_extract')
    prog_task.cancel()
    if error or not mcqs:
        await wait_msg.edit_text(
            "📌 এই পেইজে কোনো তৈরি MCQ (প্রশ্ন+অপশন) খুঁজে পাওয়া যায়নি।\n\n"
            "💡 এই অপশনটি শুধু পেইজে already ছাপানো MCQ খুঁজে বের করে — "
            "নতুন MCQ বানায় না। এই পেইজে যদি সত্যিই কোনো MCQ ছাপা না থাকে, "
            "তাহলে অন্য কোনো টাইপ সিলেক্ট করে নতুন MCQ বানিয়ে নিতে পারেন।"
        )
        return

    new_mcqs = apply_tag_exp(clean_mcq_options(_normalize_qbm_answers(mcqs)))
    new_quiz_id = await save_mcq(
        user_id=user.id, mcqs=new_mcqs, source_type='image',
        prompt_type='qbm_extract', image_file_id=image_file_id,
        chat_id=None, message_id=None
    )
    try:
        await wait_msg.delete()
    except Exception:
        pass
    kb = [
        [InlineKeyboardButton("📊 Poll Solve", callback_data=f"poll_{new_quiz_id}"), InlineKeyboardButton("📝 Quiz Solve", callback_data=f"quiz_{new_quiz_id}")],
        [InlineKeyboardButton("🌐 Website Exam", url=f"{GH_PAGES_EXAM_URL}?id={new_quiz_id}&uid={user.id}"), InlineKeyboardButton("💎 Premium PDF", callback_data=f"prempdf_{new_quiz_id}")],
    ]
    await query.message.reply_text(
        f"✅ **পেইজে থাকা {len(new_mcqs)}টি MCQ পাওয়া গেছে!**",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def handle_creative_pdf(query, quiz_id: str, ctype: str) -> None:
    """Calls exam_server /api/creative-pdf — sends premium PDF or explains why not possible."""
    label = "🧠 জ্ঞানমূলক প্রশ্ন" if ctype == "knowledge" else "💡 অনুধাবনমূলক প্রশ্ন"
    wait_msg = await query.message.reply_text(f"{label} **PDF তৈরি হচ্ছে...**\n⏱️ অনুগ্রহ করে অপেক্ষা করুন (10-25 sec)", parse_mode=ParseMode.MARKDOWN)
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.get(f"{HF_SPACE_URL}/api/creative-pdf/{quiz_id}", params={"ctype": ctype})
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and "pdf" in ct:
                pdf_file = BytesIO(resp.content)
                prefix = "ATLAS_Gyanmulok" if ctype == "knowledge" else "ATLAS_Onudhabonmulok"
                pdf_file.name = f"{prefix}_{quiz_id[:6]}.pdf"
                header = "জ্ঞানমূলক প্রশ্ন [ATLAS]" if ctype == "knowledge" else "অনুধাবনমূলক প্রশ্ন [ATLAS]"
                await query.message.chat.send_document(
                    document=pdf_file,
                    caption=f"📄 **{header}**\n🚀 সেরা গাইডলাইনে গোছানো প্রস্তুতি - এটলাস",
                    parse_mode=ParseMode.MARKDOWN
                )
                try:
                    await wait_msg.delete()
                except Exception:
                    pass
                return
            # JSON response = insufficient data explanation
            try:
                j = resp.json()
                reason = j.get("reason") or j.get("message") or "তথ্য অপর্যাপ্ত।"
            except Exception:
                reason = "PDF তৈরি করা যায়নি।"
            log_error(f"Creative PDF ({ctype}) failed for {quiz_id}: status={resp.status_code}, reason={reason}")
            if any(c in reason for c in ("{", "'error'", "RESOURCE_EXHAUSTED", "Traceback")):
                log_error(f"Creative PDF ({ctype}) raw error for {quiz_id}: {reason}")
                reason = "এই মুহূর্তে AI সার্ভার ব্যস্ত আছে। কিছুক্ষণ পর আবার চেষ্টা করুন।"
            await wait_msg.edit_text(
                f"❌ {label} তৈরি করা যায়নি\n━━━━━━━━━━━━━━━━━━━━━━\n📋 কারণ: {reason}\n\n💡 Image এর শর্ত:\n• স্পষ্ট মূল বিষয় (Topic) থাকতে হবে\n• যথেষ্ট তথ্য থাকতে হবে\n• তথ্য সত্য ও শিক্ষামূলক হতে হবে\n• তথ্য পড়া যায় এমন হতে হবে"
            )
    except Exception as e:
        log_error(f"Creative PDF error: {e}")
        try:
            await wait_msg.edit_text(BUSY_MSG)
        except Exception:
            pass

# ============================================================
# SECTION 17: POLL SOLVE HANDLER
# ============================================================

async def handle_poll_solve(query, quiz_id: str, user) -> None:
    log(f"📊 Poll solve: {quiz_id}")
    mcq_data = await get_mcq(quiz_id)
    if not mcq_data:
        await query.message.reply_text("❌ MCQ data পাওয়া যায়নি।")
        return
    mcqs = apply_tag_exp(clean_mcq_options(mcq_data['mcqs']))
    total = len(mcqs)
    settings = get_all_settings()
    timer = int(settings.get('timer_seconds', DEFAULT_TIMER))
    image_file_id = mcq_data.get('image_file_id')
    if image_file_id:
        async def _send_pre_image():
            await query.message.chat.send_photo(
                photo=image_file_id,
                caption=f"📊 **Poll Session Ready!**\n━━━━━━━━━━━━━━━━━━━━━━\n📝 Total Questions: {total}\n⏱️ Per Question: {timer} sec\n📋 Type: {get_prompt_display_name(mcq_data.get('prompt_type','prompt_1'))}\n━━━━━━━━━━━━━━━━━━━━━━\n\n{get_ayat(None)}",
                parse_mode=ParseMode.MARKDOWN
            )
        try:
            asyncio.create_task(_send_pre_image())
        except Exception as e:
            log_error(f"Failed to send pre-poll image: {e}")
    else:
        await query.message.reply_text(f"📊 **Poll Session Ready!**\n━━━━━━━━━━━━━━━━━━━━━━\n📝 Total Questions: {total}\n⏱️ Per Question: {timer} sec\n━━━━━━━━━━━━━━━━━━━━━━\n\n{get_ayat(None)}", parse_mode=ParseMode.MARKDOWN)
    await query.message.reply_text(f"📊 {total} টি Poll পাঠানো শুরু হচ্ছে... ⏱️ প্রতিটিতে {timer} সেকেন্ড।")
    await send_countdown(query.message.chat_id)
    for i, mcq in enumerate(mcqs):
        try:
            q_text = format_poll_question(mcq, i + 1)
            exp_text = format_explanation(mcq)
            options = mcq['options'][:4]
            correct_id = mcq.get('answer', 0)
            try:
                correct_id = int(correct_id)
            except (TypeError, ValueError):
                correct_id = 0
            if correct_id >= len(options) or correct_id < 0:
                correct_id = 0
            await query.message.chat.send_poll(
                question=q_text, options=options, type=Poll.QUIZ,
                correct_option_id=correct_id, explanation=exp_text, is_anonymous=True,
            )
            if i < total - 1:
                await asyncio.sleep(POLL_DELAY)
        except Exception as e:
            log_error(f"Poll {i+1} send error: {e}")
            continue
    keyboard = [
        [InlineKeyboardButton("🔄 Again Practice", callback_data=f"again_{quiz_id}"), InlineKeyboardButton("🆕 New Practice", callback_data=f"newp_{quiz_id}")],
        [InlineKeyboardButton("📸 Back to Source", callback_data=f"back_{quiz_id}")],
        [share_button(quiz_id, user.id if user else 0)],
        [InlineKeyboardButton("🌐 Atlas Website", url="https://atlascourses.com"), InlineKeyboardButton("▶️ Atlas YouTube", url="https://www.youtube.com/@atlasprep")]
    ]
    await query.message.chat.send_message(f"✅ Total {total} টি poll পাঠানো হয়েছে।", reply_markup=InlineKeyboardMarkup(keyboard))

# ============================================================
# SECTION 18: QUIZ SOLVE HANDLER
# ============================================================

async def handle_quiz_start(query, quiz_id: str, user, chat_id: int) -> None:
    log(f"📝 Quiz start: {quiz_id}")
    mcq_data = await get_mcq(quiz_id)
    if not mcq_data:
        await query.message.reply_text("❌ MCQ data পাওয়া যায়নি।")
        return
    mcqs = clean_mcq_options(mcq_data['mcqs'])
    random.shuffle(mcqs)
    mcqs = apply_tag_exp(mcqs)
    total = len(mcqs)
    settings = get_all_settings()
    timer = int(settings.get('timer_seconds', DEFAULT_TIMER))
    neg_mark = abs(float(settings.get('negative_mark', DEFAULT_NEGATIVE_MARK)))
    quiz_state = {
        'quiz_id': quiz_id, 'mcqs': mcqs, 'current_index': 0, 'answers': {},
        'correct': 0, 'wrong': 0, 'skipped': 0, 'start_time': time.time(),
        'timer': timer, 'neg_mark': neg_mark, 'current_poll_id': None,
    }
    save_active_quiz(chat_id, quiz_state)
    image_file_id = mcq_data.get('image_file_id')
    prompt_name = get_prompt_display_name(mcq_data.get('prompt_type', 'prompt_1'))
    ready_text = (
        f"📝 **Quiz Ready!**\n━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Total Questions: {total}\n⏱️ Per Question: {timer} সেকেন্ড\n"
        f"📊 Negative Mark: -{neg_mark}\n📋 Type: {prompt_name}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n{get_ayat(None)}\n\nপ্রস্তুত? শুরু করুন! 🚀"
    )
    keyboard = [[InlineKeyboardButton("▶️ Start Quiz", callback_data=f"startquiz_{quiz_id}")]]
    if image_file_id:
        async def _send_ready_photo():
            try:
                await query.message.chat.send_photo(photo=image_file_id, caption=ready_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
            except Exception:
                await query.message.reply_text(ready_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        asyncio.create_task(_send_ready_photo())
    else:
        await query.message.reply_text(ready_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def send_first_question(query, chat_id: int) -> None:
    quiz = get_active_quiz(chat_id)
    if not quiz:
        await query.message.reply_text("❌ Quiz session expired। আবার শুরু করুন।")
        return
    quiz['current_index'] = 0
    save_active_quiz(chat_id, quiz)
    await send_countdown(chat_id)
    await send_quiz_poll(chat_id)

async def send_quiz_poll(chat_id: int) -> None:
    quiz = get_active_quiz(chat_id)
    if not quiz:
        return
    idx = quiz['current_index']
    mcqs = quiz['mcqs']
    if idx >= len(mcqs):
        await end_quiz(chat_id)
        return
    mcq = mcqs[idx]
    total = len(mcqs)
    timer = quiz['timer']
    q_text = format_poll_question(mcq, idx + 1)
    exp_text = format_explanation(mcq)
    options = [o[:100] for o in mcq['options'][:4]]  # max 100 chars per option
    q_text = q_text[:300]  # max 300 chars for question
    correct_id = mcq.get('answer', 0)
    try:
        correct_id = int(correct_id)
    except (TypeError, ValueError):
        correct_id = 0
    if correct_id >= len(options) or correct_id < 0:
        correct_id = 0
    try:
        msg = await application.bot.send_poll(
            chat_id=chat_id, question=q_text, options=options, type=Poll.QUIZ,
            correct_option_id=correct_id, explanation=exp_text, is_anonymous=False, open_period=timer,
        )
        poll_id = msg.poll.id
        quiz['current_poll_id'] = poll_id
        _poll_chat_map[poll_id] = chat_id
        old_task = _timer_tasks.pop(chat_id, None)
        if old_task and not old_task.done():
            old_task.cancel()
        task = asyncio.create_task(_quiz_timer_task(chat_id, timer + 0.1))
        _timer_tasks[chat_id] = task
        save_active_quiz(chat_id, quiz)
        log(f"📊 Quiz poll sent: Q{idx+1}/{total} chat={chat_id}")
    except Exception as e:
        log_error(f"Send quiz poll error: {e}")
        quiz['skipped'] += 1
        quiz['current_index'] += 1
        save_active_quiz(chat_id, quiz)
        await asyncio.sleep(0.5)
        await send_quiz_poll(chat_id)

async def _quiz_timer_task(chat_id: int, delay: float) -> None:
    await asyncio.sleep(delay)
    quiz = get_active_quiz(chat_id)
    if not quiz:
        return
    log(f"⏱️ Timer expired chat={chat_id}, auto-next")
    quiz['skipped'] += 1
    quiz['answers'][quiz['current_index']] = -1
    quiz['current_index'] += 1
    save_active_quiz(chat_id, quiz)
    await send_quiz_poll(chat_id)

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    answer = update.poll_answer
    poll_id = answer.poll_id
    user_id = answer.user.id
    # v4.0: 6h check-in poll feedback
    if poll_id in _checkin_polls:
        _checkin_polls.pop(poll_id, None)
        try:
            fb = random.choice([
                "💪 দারুণ! তোমার জবাব পেলাম! ধারাবাহিকতাই সাফল্যের চাবিকাঠি!",
                "🌟 মাশাআল্লাহ! নিজের খবর জানানোর জন্য ধন্যবাদ!",
                "🚀 Keep going! প্রতিদিন একটু একটু করেই বড় সাফল্য আসে!",
            ])
            await application.bot.send_message(
                chat_id=user_id,
                text=f"{fb}\n\n{get_ayat(None)}\n\n📸 এখনই একটা Image পাঠিয়ে আজকের Practice শুরু করে ফেলো! 💪"
            )
        except Exception:
            pass
        return
    chat_id = _poll_chat_map.get(poll_id)
    if not chat_id:
        return
    quiz = get_active_quiz(chat_id)
    if not quiz:
        return
    task = _timer_tasks.pop(chat_id, None)
    if task and not task.done():
        task.cancel()
    idx = quiz['current_index']
    if idx >= len(quiz['mcqs']):
        return
    mcq = quiz['mcqs'][idx]
    correct = mcq.get('answer', 0)
    if answer.option_ids and len(answer.option_ids) > 0:
        chosen = answer.option_ids[0]
        quiz['answers'][idx] = chosen
        if chosen == correct:
            quiz['correct'] += 1
        else:
            quiz['wrong'] += 1
    else:
        quiz['skipped'] += 1
        quiz['answers'][idx] = -1
    quiz['current_index'] += 1
    save_active_quiz(chat_id, quiz)
    await send_quiz_poll(chat_id)

async def end_quiz(chat_id: int) -> None:
    global _poll_chat_map
    quiz = get_active_quiz(chat_id)
    if not quiz:
        return
    total = len(quiz['mcqs'])
    correct = quiz['correct']
    wrong = quiz['wrong']
    skipped = quiz['skipped']
    time_taken = int(time.time() - quiz['start_time'])
    neg_mark = quiz['neg_mark']
    penalty = wrong * neg_mark
    final_mark = correct - penalty
    percentage = (correct / total * 100) if total > 0 else 0
    feedback = get_feedback(percentage)
    ayat = get_ayat(percentage)
    mins = time_taken // 60
    secs = time_taken % 60
    time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
    result_text = (
        f"🎯 **QUIZ RESULT**\n━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 Total: {total}\n✅ Right: {correct}\n❌ Wrong: {wrong}\n⏭️ Skipped: {skipped}\n⏱️ Time: {time_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Negative Mark:**\n❌ {wrong} × {neg_mark} = -{penalty:.2f}\n"
        f"📊 Final Mark: **{final_mark:.2f}/{total}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{feedback}\n\n{ayat}"
    )
    quiz_id = quiz['quiz_id']
    _last_quiz_answers[chat_id] = {'answers': quiz['answers'], 'mcqs': quiz['mcqs']}
    save_mistakes_from_quiz(chat_id, quiz)
    keyboard = [
        [InlineKeyboardButton("🔄 Quiz Again", callback_data=f"retake_{quiz_id}"), InlineKeyboardButton("🆕 New Quiz", callback_data=f"newq_{quiz_id}")],
        [InlineKeyboardButton("❌ Mistake Practice", callback_data=f"mistake_{quiz_id}"), InlineKeyboardButton("📸 Back to Source", callback_data=f"back_{quiz_id}")],
        [share_button(quiz_id, chat_id)],
        [InlineKeyboardButton("🌐 Atlas Website", url="https://atlascourses.com"), InlineKeyboardButton("▶️ Atlas YouTube", url="https://www.youtube.com/@atlasprep")]
    ]
    try:
        save_result(user_id=chat_id, quiz_id=quiz_id, quiz_name=f"Quiz_{quiz_id[:6]}", total=total, right=correct, wrong=wrong, skipped=skipped, time_taken=time_taken, mark=final_mark, negative_mark=penalty)
    except Exception as e:
        log_error(f"Save result error: {e}")
    try:
        await application.bot.send_message(chat_id=chat_id, text=result_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log_error(f"Send result error: {e}")
    challenge = _challenge_map.pop(chat_id, None)
    if challenge and challenge.get('quiz_id') == quiz_id:
        recv_res = {'correct': correct, 'wrong': wrong, 'mark': final_mark, 'total': total, 'time_taken': time_taken}
        asyncio.create_task(_send_challenge_comparison(chat_id, challenge['sender_id'], quiz_id, recv_res))
    remove_active_quiz(chat_id)
    _poll_chat_map = {k: v for k, v in _poll_chat_map.items() if v != chat_id}

# ============================================================
# SECTION 19: NEW PRACTICE & MISTAKE PRACTICE — preserved
# ============================================================

async def handle_new_practice(query, user, mode: str, quiz_id: str) -> None:
    log(f"🆕 New practice: {quiz_id} mode={mode}")
    try:
        mcq_data = await get_mcq(quiz_id)
        if not mcq_data:
            await query.message.reply_text("❌ MCQ data পাওয়া যায়নি।")
            return
        image_file_id = mcq_data.get('image_file_id')
        prompt_type = mcq_data.get('prompt_type', 'prompt_1')
        prompt_name = get_prompt_display_name(prompt_type)
        if not image_file_id:
            await query.message.reply_text("❌ মূল ইমেজ পাওয়া যায়নি। নতুন ইমেজ পাঠান।")
            return
        wait_msg = await query.message.reply_text(
            f"🔄 **নতুন MCQ তৈরি হচ্ছে...**\n\nএকই ইমেজ থেকে নতুন ১৫টি প্রশ্ন বানানো হচ্ছে।\n📋 Same Prompt Type: {prompt_name}",
            parse_mode=ParseMode.MARKDOWN
        )
        async def _edit_np(t):
            try:
                await wait_msg.edit_text(t)
            except Exception:
                pass
        prog_task = asyncio.create_task(live_progress_task(_edit_np, "Image", total_eta=8))
        try:
            file = await application.bot.get_file(image_file_id)
            image_bytes = bytes(await file.download_as_bytearray())
        except Exception as e:
            log_error(f"Failed to download image for new practice: {e}")
            prog_task.cancel()
            await wait_msg.edit_text("❌ ইমেজ ডাউনলোড করতে সমস্যা হয়েছে।")
            return
        mcqs, error = await generate_mcq_from_image(image_bytes, prompt_type)
        prog_task.cancel()
        if error or not mcqs:
            err_msg = error or "MCQ তৈরি করতে সমস্যা হয়েছে।"
            await wait_msg.edit_text(f"❌ {err_msg}")
            return
        random.shuffle(mcqs)
        new_mcqs = apply_tag_exp(clean_mcq_options(mcqs[:NEW_PRACTICE_COUNT]))
        new_quiz_id = await save_mcq(user_id=user.id, mcqs=new_mcqs, source_type='image', prompt_type=prompt_type, image_file_id=image_file_id, chat_id=None, message_id=None)
        try:
            await wait_msg.delete()
        except:
            pass
        if mode == 'quiz':
            chat_id = query.message.chat_id
            quiz_state = {
                'quiz_id': new_quiz_id, 'mcqs': apply_tag_exp(new_mcqs), 'current_index': 0,
                'answers': {}, 'correct': 0, 'wrong': 0, 'skipped': 0, 'start_time': time.time(),
                'timer': int(get_setting('timer_seconds', DEFAULT_TIMER)),
                'neg_mark': abs(float(get_setting('negative_mark', DEFAULT_NEGATIVE_MARK))),
                'current_poll_id': None,
            }
            save_active_quiz(chat_id, quiz_state)
            keyboard = [[InlineKeyboardButton("▶️ Start Quiz", callback_data=f"startquiz_{new_quiz_id}")]]
            await query.message.reply_text(f"✅ {len(new_mcqs)} টি নতুন MCQ রেডি!\n\n[▶️ Start Quiz] চাপুন।", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await handle_poll_solve(query, new_quiz_id, user)
        log(f"✅ New practice generated: {new_quiz_id}")
    except Exception as e:
        log_error(f"New practice error: {e}")
        await safe_user_reply(query.message)

async def handle_mistake_practice(query, user, chat_id: int, quiz_id: str) -> None:
    log(f"❌ Mistake practice: {quiz_id}")
    snap = _last_quiz_answers.get(chat_id, {})
    # v4.0 FIX: use the exact mcqs+answers from the just-finished quiz (shuffled
    # order), so wrong-question detection aligns correctly by index.
    last_answers = snap.get('answers', {}) if isinstance(snap, dict) and 'answers' in snap else snap
    quiz_mcqs = snap.get('mcqs') if isinstance(snap, dict) else None
    if not last_answers:
        await query.message.reply_text("❌ আগের কুইজের ডাটা পাওয়া যায়নি। আবার একটি Quiz শেষ করুন।")
        return
    if not quiz_mcqs:
        mcq_data = await get_mcq(quiz_id)
        if not mcq_data:
            await query.message.reply_text("❌ MCQ data পাওয়া যায়নি।")
            return
        quiz_mcqs = mcq_data['mcqs']
    wrong_mcqs = []
    for idx, mcq in enumerate(quiz_mcqs):
        user_answer = last_answers.get(idx, last_answers.get(str(idx)))
        correct_answer = mcq.get('answer', 0)
        if user_answer is not None and user_answer != -1 and user_answer != correct_answer:
            clean = {k: v for k, v in mcq.items() if not k.startswith('_')}
            wrong_mcqs.append(clean)
    if not wrong_mcqs:
        await query.message.reply_text("✅ কোনো ভুল উত্তর নেই! সব সঠিক ছিল! 🎉")
        return
    random.shuffle(wrong_mcqs)
    tagged_mcqs = apply_tag_exp(clean_mcq_options(wrong_mcqs))
    quiz_state = {
        'quiz_id': quiz_id, 'mcqs': tagged_mcqs, 'current_index': 0,
        'answers': {}, 'correct': 0, 'wrong': 0, 'skipped': 0, 'start_time': time.time(),
        'timer': int(get_setting('timer_seconds', DEFAULT_TIMER)),
        'neg_mark': abs(float(get_setting('negative_mark', DEFAULT_NEGATIVE_MARK))),
        'current_poll_id': None,
    }
    save_active_quiz(chat_id, quiz_state)
    keyboard = [[InlineKeyboardButton("▶️ Start Mistake Practice", callback_data=f"startquiz_{quiz_id}")]]
    await query.message.reply_text(
        f"❌ **Mistake Practice**\n━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 Only Wrong Questions: {len(wrong_mcqs)}\n⏱️ Per Question: {quiz_state['timer']} sec\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\nভুল থেকে শিখুন! 💪",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
    )

async def handle_back_to_source(query, quiz_id: str) -> None:
    """v4.0: direct main image + buttons resent + reply-jump to pinned source."""
    log(f"📸 Back to source: {quiz_id}")
    mcq_data = await get_mcq(quiz_id)
    if not mcq_data:
        await query.message.reply_text("❌ সোর্স মেসেজ পাওয়া যায়নি।")
        return
    chat_id = mcq_data.get('chat_id')
    message_id = mcq_data.get('message_id')
    if not chat_id or not message_id:
        cache = _image_cache.get(quiz_id, {})
        chat_id = cache.get('chat_id')
        message_id = cache.get('message_id')
    image_file_id = mcq_data.get('image_file_id')
    # 1) Best: resend the main source image WITH full buttons right here (direct access)
    if image_file_id:
        try:
            kb = mcq_set_keyboard(quiz_id, query.from_user.id)
            await query.message.chat.send_photo(
                photo=image_file_id,
                caption=f"📸 **আপনার মূল Source Image**\n📝 Total MCQ: {len(mcq_data.get('mcqs', []))}\n📋 Type: {get_prompt_display_name(mcq_data.get('prompt_type','prompt_1'))}",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )
            return
        except Exception as e:
            log_error(f"Back-to-source direct image failed: {e}")
    # 2) Fallback: reply-jump to pinned message
    if chat_id and message_id:
        try:
            await application.bot.send_message(
                chat_id=chat_id,
                text="📸 **Back to Source**\n\nউপরের reply করা মেসেজটিতে tap করুন — সরাসরি আপনার Source এ পৌঁছে যাবেন! 🚀",
                reply_to_message_id=message_id,
                parse_mode=ParseMode.MARKDOWN
            )
            return
        except Exception as e:
            log_error(f"Back-to-source reply failed: {e}")
    await query.message.reply_text("📸 **Back to Source**\n\nউপরে স্ক্রল করে Pinned মেসেজটি দেখুন। 📌", parse_mode=ParseMode.MARKDOWN)

# ============================================================
# SECTION 20: PROMPT MANAGEMENT HANDLERS — preserved
# ============================================================

_prompt_edit_state: Dict[int, str] = {}

async def handle_prompt_edit_start(query, prompt_key: str) -> None:
    user_id = query.from_user.id
    if not is_admin(user_id):
        await query.message.reply_text("❌ Unauthorized")
        return
    prompts = get_prompts_from_db()
    prompt_data = prompts.get(prompt_key, PROMPT_MAP.get(prompt_key, {}))
    prompt_name = prompt_data.get('name', prompt_key)
    prompt_text = prompt_data.get('text', '')
    _prompt_edit_state[user_id] = prompt_key
    await query.message.reply_text(
        f"✏️ **Edit Prompt: {prompt_name}**\n━━━━━━━━━━━━━━━━━━━━━━\n\n**Current Prompt:**\n```\n{prompt_text[:500]}...\n```\n\n📝 নতুন Prompt টেক্সট পাঠান:",
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_prompt_view(query, prompt_key: str) -> None:
    user_id = query.from_user.id
    if not is_admin(user_id):
        await query.message.reply_text("❌ Unauthorized")
        return
    prompts = get_prompts_from_db()
    prompt_data = prompts.get(prompt_key, PROMPT_MAP.get(prompt_key, {}))
    prompt_name = prompt_data.get('name', prompt_key)
    prompt_text = prompt_data.get('text', 'N/A')
    if len(prompt_text) > 3500:
        prompt_text = prompt_text[:3500] + "\n\n... (truncated)"
    await query.message.reply_text(f"📝 **{prompt_name}**\n━━━━━━━━━━━━━━━━━━━━━━\n\n```\n{prompt_text}\n```", parse_mode=ParseMode.MARKDOWN)

async def handle_prompt_add(query) -> None:
    user_id = query.from_user.id
    if not is_admin(user_id):
        await query.message.reply_text("❌ Unauthorized")
        return
    _prompt_edit_state[user_id] = 'new_prompt'
    await query.message.reply_text(
        "➕ **Add New Prompt**\n━━━━━━━━━━━━━━━━━━━━━━\n\nFormat:\nLine 1: Prompt Key (e.g., prompt_custom)\nLine 2: Display Name (e.g., Custom MCQ)\nLine 3+: Full Prompt Text\n\n```\nprompt_custom\nCustom MCQ\nYour full prompt text here...\n```",
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_broadcast_confirm(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = query.from_user.id
    if not is_admin(user_id):
        await query.message.reply_text("❌ Unauthorized")
        return
    replied_msg = context.user_data.get('broadcast_msg')
    users = context.user_data.get('broadcast_users', [])
    if not replied_msg or not users:
        await query.message.edit_text("❌ Broadcast data expired। আবার চেষ্টা করুন।")
        return
    await query.message.edit_text(f"📨 পাঠানো শুরু... 0/{len(users)}")
    success = 0
    failed = 0
    for i, user in enumerate(users):
        try:
            target_id = user['user_id']
            if replied_msg.photo:
                await application.bot.send_photo(chat_id=target_id, photo=replied_msg.photo[-1].file_id, caption=replied_msg.caption or "")
            elif replied_msg.text:
                await application.bot.send_message(chat_id=target_id, text=replied_msg.text)
            elif replied_msg.document:
                await application.bot.send_document(chat_id=target_id, document=replied_msg.document.file_id, caption=replied_msg.caption or "")
            else:
                failed += 1
                continue
            success += 1
            if (i + 1) % 10 == 0:
                try:
                    await query.message.edit_text(f"📨 পাঠানো হচ্ছে... {success}/{len(users)}")
                except:
                    pass
            await asyncio.sleep(0.05)
        except Forbidden:
            failed += 1
            continue
        except Exception as e:
            log_error(f"Broadcast to {user['user_id']} failed: {e}")
            failed += 1
            continue
    context.user_data.pop('broadcast_msg', None)
    context.user_data.pop('broadcast_users', None)
    await query.message.edit_text(
        f"📨 **Broadcast Complete!**\n━━━━━━━━━━━━━━━━━━━━━━\n✅ Success: {success}\n❌ Failed: {failed}\n👥 Total: {len(users)}\n━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.MARKDOWN
    )
    log(f"📨 Broadcast done: {success}/{len(users)}")

# ============================================================
# SECTION 21: TEXT HANDLER FOR PROMPT EDITS — preserved
# ============================================================

async def handle_prompt_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in _prompt_edit_state:
        return
    prompt_key = _prompt_edit_state.pop(user_id)
    text = update.message.text
    if prompt_key == 'new_prompt':
        lines = text.strip().split('\n')
        if len(lines) < 3:
            await update.message.reply_text("❌ Format: Line1=Key, Line2=Name, Line3+=Prompt Text")
            return
        new_key = lines[0].strip()
        new_name = lines[1].strip()
        new_text = '\n'.join(lines[2:])
        update_prompt_in_db(new_key, new_name, new_text)
        await update.message.reply_text(f"✅ New prompt **{new_name}** added!\nKey: `{new_key}`", parse_mode=ParseMode.MARKDOWN)
        log(f"📝 New prompt added: {new_key}")
    else:
        prompts = get_prompts_from_db()
        prompt_data = prompts.get(prompt_key, PROMPT_MAP.get(prompt_key, {}))
        prompt_name = prompt_data.get('name', prompt_key)
        update_prompt_in_db(prompt_key, prompt_name, text)
        await update.message.reply_text(f"✅ Prompt **{prompt_name}** updated!", parse_mode=ParseMode.MARKDOWN)
        log(f"📝 Prompt updated: {prompt_key}")
    raise ApplicationHandlerStop

# ============================================================
# SECTION 21B: v3.0 FEATURES (preserved) + v4.0 additions
# Pomodoro | Progress | Revision | Report | Random | Class | Premium PDF
# Pending-input router (incl. bmexam_count, gpa) | /keys | schedulers
# ============================================================

POMODORO_IMAGE_PATH = "pomodoro.png"

def _generate_pomodoro_image() -> BytesIO:
    img = Image.new('RGB', (800, 400), color=(20, 20, 50))
    draw = ImageDraw.Draw(img)
    try:
        draw.rounded_rectangle([20, 20, 780, 380], radius=30, fill=(30, 30, 70), outline=(100, 100, 200), width=2)
    except AttributeError:
        draw.rectangle([20, 20, 780, 380], fill=(30, 30, 70), outline=(100, 100, 200), width=2)
    draw.ellipse([320, 30, 480, 190], fill=(220, 50, 50), outline=(180, 30, 30), width=3)
    draw.ellipse([370, 20, 430, 45], fill=(50, 160, 50))
    draw.ellipse([355, 65, 445, 155], fill=(240, 70, 70))
    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
        font_emoji = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except Exception:
        font_large = ImageFont.load_default()
        font_small = font_large
        font_emoji = font_large
    draw.text((400, 230), "ATLAS", fill=(255, 255, 255), font=font_large, anchor="mm")
    draw.text((400, 280), "Pomodoro Study Timer", fill=(180, 180, 220), font=font_small, anchor="mm")
    draw.text((400, 320), "Focus  |  Study  |  Achieve", fill=(120, 120, 180), font=font_emoji, anchor="mm")
    draw.text((400, 360), "atlascourses.com", fill=(100, 100, 160), font=font_emoji, anchor="mm")
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf
HADITHS = [
    "🕌 রাসূলুল্লাহ ﷺ বলেছেন: “যে ব্যক্তি জ্ঞান অন্বেষণে কোনো পথ অবলম্বন করে, আল্লাহ তার জন্য জান্নাতের পথ সহজ করে দেন।” [সহীহ মুসলিম]",
    "🕌 রাসূলুল্লাহ ﷺ বলেছেন: “জ্ঞান অর্জন করা প্রত্যেক মুসলিমের উপর ফরজ।” [ইবনে মাজাহ]",
    "🕌 রাসূলুল্লাহ ﷺ বলেছেন: “দুটি নিয়ামত এমন আছে যাতে অনেক মানুষ ধোঁকায় আছে — সুস্থতা ও অবসর সময়।” [সহীহ বুখারী]",
]
POMO_MOTIVATION = ["🚀 Let's go!", "🔥 Keep going!", "💪 You're doing great!", "⚡ প্রায় অর্ধেক শেষ!", "🌟 দারুণ চলছে!", "🏁 প্রায় শেষ, হাল ছেড়ো না!"]

_pomodoro_sessions: Dict[int, Dict] = {}
_pending_input: Dict[int, Dict] = {}

POMODORO_CAPTION = """🎯 **ATLAS Pomodoro Timer**
━━━━━━━━━━━━━━━━━━━━━━

✅ একটা timer select করো!

📖 Work সময়ে পড়বে
☕ Break সময়ে বিশ্রাম নেবে

💡 সময় ধরে Smartly পড়ো!
🚀 আশা করি পড়ায় গতি ফিরবে"""

async def cmd_timer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    log(f"🍅 /timer from {user['user_id']}")
    keyboard = [
        [InlineKeyboardButton("⏱️ 15 মিনিট", callback_data="pomo_15"), InlineKeyboardButton("🍅 25 মিনিট", callback_data="pomo_25")],
        [InlineKeyboardButton("📖 40 মিনিট", callback_data="pomo_40"), InlineKeyboardButton("🔥 60 মিনিট", callback_data="pomo_60")],
        [InlineKeyboardButton("⚙️ Custom", callback_data="pomo_custom")],
    ]
    try:
        if os.path.exists(POMODORO_IMAGE_PATH):
            with open(POMODORO_IMAGE_PATH, 'rb') as f:
                pomo_img = BytesIO(f.read())
            pomo_img.name = "pomodoro.png"
        else:
            pomo_img = _generate_pomodoro_image()
        await update.message.reply_photo(photo=pomo_img, caption=POMODORO_CAPTION, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log_error(f"Pomodoro image error, fallback to text: {e}")
        await update.message.reply_text(POMODORO_CAPTION, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

def _pomo_text(first_name: str, total_sec: int, left_sec: int, paused: bool) -> str:
    done = total_sec - left_sec
    pct = min(100, int(done / total_sec * 100)) if total_sec else 0
    bar_len = 12
    filled = int(round(bar_len * pct / 100))
    bar = "▰" * filled + "▱" * (bar_len - filled)
    mins, secs = left_sec // 60, left_sec % 60
    motiv = POMO_MOTIVATION[min(len(POMO_MOTIVATION) - 1, int(pct / 100 * len(POMO_MOTIVATION)))]
    status = "⏸️ Timer Paused — বিশ্রাম চলছে" if paused else "📖 পড়ার সময় চলছে..."
    return (
        f"🌟 **ATLAS Pomodoro Timer** 🌟\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{status}\n\n"
        f"{bar} {pct}%\n"
        f"{motiv} {pct}%\n\n"
        f"🌱 বাকি সময়: {mins} মিনিট {secs} সেকেন্ড\n\n"
        f"🚀 সেরা গাইডলাইনে গোছানো প্রস্তুতি - এটলাস"
    )

def _pomo_keyboard(paused: bool):
    if paused:
        return InlineKeyboardMarkup([[InlineKeyboardButton("▶️ আবার শুরু", callback_data="pomo_resume"), InlineKeyboardButton("⏹️ বন্ধ করো", callback_data="pomo_stop")]])
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏸️ থামাও", callback_data="pomo_pause"), InlineKeyboardButton("⏹️ বন্ধ করো", callback_data="pomo_stop")]])

async def start_pomodoro(chat_id: int, first_name: str, minutes: int) -> None:
    old = _pomodoro_sessions.pop(chat_id, None)
    if old and old.get('task') and not old['task'].done():
        old['task'].cancel()
    total = minutes * 60
    ayat = get_ayat(None)
    hadith = random.choice(HADITHS)
    msg = await application.bot.send_message(
        chat_id=chat_id,
        text=_pomo_text(first_name, total, total, False) + f"\n\n🔗 **আজকের আয়াত:**\n{ayat}\n\n🔗 **আজকের হাদিস:**\n{hadith}",
        reply_markup=_pomo_keyboard(False), parse_mode=ParseMode.MARKDOWN
    )
    sess = {'total': total, 'left': total, 'paused': False, 'msg_id': msg.message_id,
            'first_name': first_name, 'ayat': ayat, 'hadith': hadith, 'task': None,
            'last_render': total}
    sess['task'] = asyncio.create_task(_pomodoro_loop(chat_id))
    _pomodoro_sessions[chat_id] = sess
    log(f"🍅 Pomodoro started: {minutes}m chat={chat_id}")

async def _pomodoro_edit(chat_id: int) -> None:
    sess = _pomodoro_sessions.get(chat_id)
    if not sess:
        return
    text = _pomo_text(sess['first_name'], sess['total'], sess['left'], sess['paused'])
    text += f"\n\n🔗 **আজকের আয়াত:**\n{sess['ayat']}\n\n🔗 **আজকের হাদিস:**\n{sess['hadith']}"
    try:
        await application.bot.edit_message_text(chat_id=chat_id, message_id=sess['msg_id'], text=text,
                                                reply_markup=_pomo_keyboard(sess['paused']), parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

async def _pomodoro_loop(chat_id: int) -> None:
    """v4.0 FIX: ticks every 1 second so countdown decreases smoothly (1s steps).
    Telegram edit rate-limit respected by editing the message only every ~5s,
    but the countdown value itself decrements by 1s continuously."""
    try:
        last_edit = 0
        while True:
            await asyncio.sleep(1)
            sess = _pomodoro_sessions.get(chat_id)
            if not sess:
                return
            if not sess['paused']:
                sess['left'] = max(0, sess['left'] - 1)
            if sess['left'] <= 0:
                await _pomodoro_finish(chat_id)
                return
            last_edit += 1
            # edit message every 5s (rate-limit safe) OR on last 10s every 2s for smooth feel
            interval = 2 if sess['left'] <= 10 else 5
            if last_edit >= interval:
                last_edit = 0
                await _pomodoro_edit(chat_id)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log_error(f"Pomodoro loop error: {e}")

async def _pomodoro_finish(chat_id: int) -> None:
    sess = _pomodoro_sessions.pop(chat_id, None)
    if not sess:
        return
    keyboard = [
        [InlineKeyboardButton("✅ Yes, 100%", callback_data="pomofb_yes100")],
        [InlineKeyboardButton("🟡 Yes, Not 100%", callback_data="pomofb_yesnot")],
        [InlineKeyboardButton("❌ No", callback_data="pomofb_no")],
    ]
    try:
        await application.bot.send_message(
            chat_id=chat_id,
            text=f"শুভকামনা প্রিয় {sess['first_name']}... 🙌\n\nতোমার টাইম শেষ হয়েছে। যে উদ্দেশ্যে টাইম সেট করেছিলে কাজটি কি শেষ হয়েছে?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        log_error(f"Pomodoro finish msg error: {e}")

POMO_FEEDBACK = {
    'yes100': "🏆 **মাশাআল্লাহ! অসাধারণ!** 🎉\n\nতুমি প্রমাণ করলে তুমি পারো! এই consistency টা ধরে রাখো — সাফল্য তোমার হাতের নাগালে।\n\n💡 একটা ছোটো break নিয়ে আবার /timer দিয়ে পরের সেশন শুরু করো! 🚀",
    'yesnot': "👏 **Good job! এগিয়ে যাচ্ছো!**\n\nশেষ হয়নি তাতে কি? অর্ধেক কাজ হয়ে যাওয়াও একটা জয়। পরের সেশনে বাকিটা শেষ করে ফেলো!\n\n💡 Tips: পরেরবার একটু বড় timer নাও — /timer 🍅",
    'no': "🌱 **সমস্যা নেই, হাল ছেড়ো না!**\n\nশুরুটাই আসল। বসেছিলে — এটাই অনেকের চেয়ে এগিয়ে। এখন distraction গুলো সরিয়ে আবার বসো।\n\n💪 ছোটো করে শুরু করো — 15 মিনিটের timer দিয়ে দেখো: /timer",
}

async def handle_pomodoro_callback(query, data: str) -> None:
    chat_id = query.message.chat_id
    user = query.from_user
    if data == "pomo_custom":
        _pending_input[user.id] = {'type': 'pomo_custom'}
        await query.message.reply_text("⚙️ কত মিনিটের timer চাও? সংখ্যা লিখো (যেমন: 30)")
        return
    if data in ("pomo_15", "pomo_25", "pomo_40", "pomo_60"):
        await start_pomodoro(chat_id, user.first_name or "User", int(data.split("_")[1]))
        return
    sess = _pomodoro_sessions.get(chat_id)
    if data == "pomo_pause":
        if sess:
            sess['paused'] = True
            await _pomodoro_edit(chat_id)
        return
    if data == "pomo_resume":
        if sess:
            sess['paused'] = False
            await _pomodoro_edit(chat_id)
        return
    if data == "pomo_stop":
        old = _pomodoro_sessions.pop(chat_id, None)
        if old and old.get('task') and not old['task'].done():
            old['task'].cancel()
        await query.message.reply_text("⏹️ Timer বন্ধ করা হয়েছে। আবার শুরু করতে: /timer")
        return
    if data.startswith("pomofb_"):
        fb = POMO_FEEDBACK.get(data.replace("pomofb_", ""), "")
        if fb:
            await query.message.reply_text(fb, parse_mode=ParseMode.MARKDOWN)

# ------------------------------------------------------------
# MISTAKES PERSISTENCE (for /revision) — preserved
# ------------------------------------------------------------

def save_mistakes_from_quiz(user_id: int, quiz: Dict) -> None:
    try:
        client = get_supabase()
        rows = []
        now = datetime.now(BD_TZ).isoformat()
        for idx, mcq in enumerate(quiz.get('mcqs', [])):
            ans = quiz.get('answers', {}).get(idx, quiz.get('answers', {}).get(str(idx)))
            if ans is None:
                continue
            correct = mcq.get('answer', 0)
            clean = {k: v for k, v in mcq.items() if not k.startswith('_')}
            if ans == -1:
                rows.append({'user_id': user_id, 'quiz_id': quiz.get('quiz_id', ''), 'question_data': json.dumps(clean, ensure_ascii=False), 'status': 'skip', 'created_at': now})
            elif ans != correct:
                rows.append({'user_id': user_id, 'quiz_id': quiz.get('quiz_id', ''), 'question_data': json.dumps(clean, ensure_ascii=False), 'status': 'wrong', 'created_at': now})
        if rows:
            client.table('mistakes').insert(rows).execute()
            for r in rows:
                mirror_insert('mistakes', r)
            log(f"💾 Mistakes saved: {len(rows)} for user {user_id}")
    except Exception as e:
        log_error(f"save_mistakes_from_quiz error: {e}")

def get_mistake_mcqs(user_id: int, statuses: List[str]) -> List[Dict]:
    try:
        client = get_supabase()
        result = client.table('mistakes').select('question_data,status').eq('user_id', user_id).in_('status', statuses).order('created_at', desc=True).limit(500).execute()
        rows = result.data
        if not rows:
            bk = get_supabase_backup()
            if bk:
                result = bk.table('mistakes').select('question_data,status').eq('user_id', user_id).in_('status', statuses).order('created_at', desc=True).limit(500).execute()
                rows = result.data
        mcqs, seen = [], set()
        for row in (rows or []):
            q = json.loads(row['question_data']) if isinstance(row['question_data'], str) else row['question_data']
            key = q.get('question', '')[:120]
            if key and key not in seen:
                seen.add(key)
                mcqs.append(q)
        return mcqs
    except Exception as e:
        log_error(f"get_mistake_mcqs error: {e}")
        return []

def get_all_user_mcq_pool(user_id: int) -> List[Dict]:
    pool, seen = [], set()
    for mset in get_user_mcqs(user_id):
        for q in mset.get('mcqs', []):
            key = q.get('question', '')[:120]
            if key and key not in seen:
                seen.add(key)
                pool.append(q)
    return pool

# ------------------------------------------------------------
# /revision — preserved
# ------------------------------------------------------------

REVISION_LABELS = {'all': '📚 All MCQ', 'mistake': '❌ Mistake Practice', 'special': '⭐ Special Practice', 'random': '🎲 Random'}

async def cmd_revision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    log(f"🔁 /revision from {user['user_id']}")
    keyboard = [
        [InlineKeyboardButton("📚 All MCQ", callback_data="rev_all")],
        [InlineKeyboardButton("❌ Mistake Practice (Only Wrong)", callback_data="rev_mistake")],
        [InlineKeyboardButton("⭐ Special Practice (Wrong + Skip)", callback_data="rev_special")],
    ]
    await update.message.reply_text(
        f"🌟 **Welcome to Revision Mood, {user['first_name']}!**\n━━━━━━━━━━━━━━━━━━━━━━\n\nএই অব্দি আগের বানানো সকল MCQ গুলো নিয়ে ঝালাই হয়ে যাক! 💪",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
    )

async def handle_revision_mode(query, mode: str) -> None:
    user_id = query.from_user.id
    if mode == 'all':
        pool = get_all_user_mcq_pool(user_id)
    elif mode == 'mistake':
        pool = get_mistake_mcqs(user_id, ['wrong'])
    else:
        pool = get_mistake_mcqs(user_id, ['wrong', 'skip'])
    if not pool:
        await query.message.reply_text("📭 এই ক্যাটাগরিতে কোনো MCQ নেই। আগে কিছু practice করুন!")
        return
    _pending_input[user_id] = {'type': 'rev_count', 'mode': mode}
    await query.message.reply_text(
        f"{REVISION_LABELS[mode]}\n📦 Total MCQ আছে: **{len(pool)}** টি\n\nএখান থেকে কয়টা MCQ practice করতে চান?\nসংখ্যা লিখুন (যেমন: 2/5/8)\nসব practice করতে চাইলে \"All\" লিখুন",
        parse_mode=ParseMode.MARKDOWN
    )

async def _start_practice_set(message, user, mode: str, count_text: str, mcqs_override: List[Dict] = None) -> None:
    """Shared by /revision, /random, /bmexam: builds saved MCQ set + Quiz/Poll/Web Exam."""
    user_id = user.id if hasattr(user, 'id') else user['user_id']
    first_name = getattr(user, 'first_name', None) or (user.get('first_name') if isinstance(user, dict) else 'User') or 'User'
    if mcqs_override is not None:
        pool = mcqs_override
    elif mode == 'all':
        pool = get_all_user_mcq_pool(user_id)
    elif mode == 'mistake':
        pool = get_mistake_mcqs(user_id, ['wrong'])
    elif mode == 'special':
        pool = get_mistake_mcqs(user_id, ['wrong', 'skip'])
    elif mode == 'bookmark':
        pool = get_all_bookmarks(user_id)
    else:
        pool = get_all_user_mcq_pool(user_id)
    if not pool:
        await message.reply_text("📭 কোনো MCQ পাওয়া যায়নি।")
        return
    ct = count_text.strip().lower()
    if ct == 'all':
        count = len(pool)
    else:
        try:
            count = max(1, min(len(pool), int(re.sub(r'[^0-9]', '', ct) or '0')))
        except Exception:
            count = 0
    if count <= 0:
        await message.reply_text("❌ সঠিক সংখ্যা লিখুন অথবা \"All\" লিখুন।")
        return
    random.shuffle(pool)
    selected = apply_tag_exp(clean_mcq_options(pool[:count]))
    src = {'random': 'random', 'bookmark': 'bookmark_exam'}.get(mode, f'revision_{mode}')
    quiz_id = await save_mcq(user_id=user_id, mcqs=selected, source_type=src, prompt_type='prompt_1', image_file_id=None, chat_id=None, message_id=None)
    label = REVISION_LABELS.get(mode, '🔖 Bookmark Exam' if mode == 'bookmark' else mode)
    emoji = "🚀" if mode in ('random', 'bookmark') else "⚡"
    keyboard = [
        [InlineKeyboardButton("📝 Quiz Solve", callback_data=f"quiz_{quiz_id}"), InlineKeyboardButton("📊 Poll Solve", callback_data=f"poll_{quiz_id}")],
        [InlineKeyboardButton("🌐 Website Exam", url=f"{GH_PAGES_EXAM_URL}?id={quiz_id}&uid={user_id}")],
        [share_button(quiz_id, user_id)],
    ]
    await message.reply_text(
        f"{emoji} **Type:** {label}\n🔗 **MCQ:** {len(selected)}\n\n🚀 Are you ready, Dear {first_name}?",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
    )

# ------------------------------------------------------------
# /random — preserved
# ------------------------------------------------------------

async def cmd_random(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    log(f"🎲 /random from {user['user_id']}")
    pool = get_all_user_mcq_pool(user['user_id'])
    if not pool:
        await update.message.reply_text("📭 আপনার কোনো সংরক্ষিত MCQ নেই। আগে Image/Text পাঠিয়ে MCQ বানান!")
        return
    _pending_input[user['user_id']] = {'type': 'rand_count'}
    await update.message.reply_text(
        f"🎲 **Random Practice!**\n📦 আপনার মোট MCQ: **{len(pool)}** টি\n\nকয়টা MCQ practice করতে চান? সংখ্যা লিখুন (অথবা \"All\")",
        parse_mode=ParseMode.MARKDOWN
    )

# ------------------------------------------------------------
# /progress — preserved
# ------------------------------------------------------------

def build_progress_chart(results: List[Dict]) -> Optional[bytes]:
    try:
        from PIL import ImageDraw
        data = list(reversed(results[:10]))
        W, H, PAD = 900, 480, 60
        img = Image.new('RGB', (W, H), (16, 18, 38))
        d = ImageDraw.Draw(img)
        d.text((PAD, 18), "ATLAS Progress  -  Last Practices (%)", fill=(180, 180, 255))
        chart_h = H - 2 * PAD - 20
        base_y = H - PAD
        n = max(1, len(data))
        bw = max(20, int((W - 2 * PAD) / n * 0.55))
        gap = int((W - 2 * PAD) / n)
        for gy in range(0, 101, 25):
            y = base_y - int(chart_h * gy / 100)
            d.line([(PAD, y), (W - PAD, y)], fill=(40, 44, 80))
            d.text((10, y - 7), f"{gy}", fill=(120, 120, 170))
        for i, r in enumerate(data):
            total = r.get('total', 1) or 1
            pct = max(0, min(100, (r.get('correct', 0) / total) * 100))
            x = PAD + i * gap + (gap - bw) // 2
            y = base_y - int(chart_h * pct / 100)
            color = (76, 217, 130) if pct >= 75 else (255, 196, 61) if pct >= 50 else (255, 95, 95)
            d.rectangle([x, y, x + bw, base_y], fill=color)
            d.text((x, y - 18), f"{int(pct)}%", fill=(230, 230, 255))
            d.text((x, base_y + 8), str(r.get('created_at', ''))[5:10], fill=(150, 150, 200))
        buf = BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()
    except Exception as e:
        log_error(f"build_progress_chart error: {e}")
        return None

async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    user_id = user['user_id']
    log(f"📊 /progress from {user_id}")
    results = get_user_results(user_id, limit=50)
    udata = get_user(user_id) or {}
    if not results:
        await update.message.reply_text("📭 এখনো কোনো Quiz result জমা হয়নি। আগে কিছু practice করুন!")
        return
    total_attempts = len(results)
    total_q = sum(r.get('total', 0) for r in results)
    total_right = sum(r.get('correct', 0) for r in results)
    total_wrong = sum(r.get('wrong', 0) for r in results)
    total_skip = sum(r.get('skipped', 0) for r in results)
    avg = (total_right / total_q * 100) if total_q else 0
    recent5 = results[:5]
    recent_avg = (sum(r.get('correct', 0) for r in recent5) / max(1, sum(r.get('total', 0) for r in recent5))) * 100
    trend = "📈 উন্নতি হচ্ছে!" if recent_avg >= avg else "📉 একটু নেমেছে — আরো ফোকাস!"
    best = max(results, key=lambda r: (r.get('correct', 0) / max(1, r.get('total', 1))))
    best_pct = best.get('correct', 0) / max(1, best.get('total', 1)) * 100
    text = (
        f"📊 **{user['first_name']} এর Progress Report**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Total Practice: **{total_attempts}** বার\n"
        f"📝 Total Question: **{total_q}**\n"
        f"✅ Right: **{total_right}** | ❌ Wrong: **{total_wrong}** | ⏭️ Skip: **{total_skip}**\n"
        f"📈 Overall Accuracy: **{avg:.1f}%**\n"
        f"🔥 Recent 5 Avg: **{recent_avg:.1f}%** — {trend}\n"
        f"🏆 Best Score: **{best_pct:.0f}%** ({str(best.get('created_at',''))[:10]})\n"
        f"📚 Lifetime Practice Count: **{udata.get('practice_count', 0)}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{get_feedback(avg)}\n\n{get_ayat(avg)}"
    )
    chart = build_progress_chart(results)
    if chart:
        try:
            await update.message.reply_photo(photo=chart, caption=text, parse_mode=ParseMode.MARKDOWN)
            return
        except Exception as e:
            log_error(f"Progress chart send failed: {e}")
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ------------------------------------------------------------
# /report — preserved
# ------------------------------------------------------------

async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    user_id = user['user_id']
    log(f"📈 /report from {user_id}")
    udata = get_user(user_id) or {}
    practiced = udata.get('practice_count', 0)
    limit = udata.get('daily_limit', DEFAULT_DAILY_LIMIT)
    try:
        created = udata.get('created_at') or udata.get('last_reset')
        days_active = max(1, (datetime.now(BD_TZ).date() - datetime.fromisoformat(str(created)[:10]).date()).days + 1)
    except Exception:
        days_active = 1
    opportunity = days_active * limit
    keyboard = [
        [InlineKeyboardButton("📅 ৩ দিন", callback_data="rep_3"), InlineKeyboardButton("📅 ৭ দিন", callback_data="rep_7")],
        [InlineKeyboardButton("📅 ১৫ দিন", callback_data="rep_15"), InlineKeyboardButton("📅 ৩০ দিন", callback_data="rep_30")],
    ]
    await update.message.reply_text(
        f"📈 **তোমার বিগত দিনগুলোর সকল report জমা আছে।**\nReport দেখে নিজের অবস্থা যাচাই করো!\n━━━━━━━━━━━━━━━━━━━━━━\n\n✅ এই অব্দি যতবার practice করেছ: **{practiced}** বার\n📊 যতবার করার সুযোগ ছিল: **~{opportunity}** বার\n\n🔗 বিগত কত দিনের Report দেখতে চাও?",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
    )

async def handle_report_days(query, days: int) -> None:
    user_id = query.from_user.id
    try:
        client = get_supabase()
        cutoff = (datetime.now(BD_TZ) - timedelta(days=days)).isoformat()
        result = client.table('results').select('*').eq('user_id', user_id).gte('created_at', cutoff).order('created_at', desc=True).limit(60).execute()
        rows = result.data or []
    except Exception as e:
        log_error(f"report query error: {e}")
        rows = []
    if not rows:
        await query.message.reply_text(f"📭 বিগত {days} দিনে কোনো practice record নেই।")
        return
    text = f"📈 **বিগত {days} দিনের Report** ({len(rows)} টি practice)\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, r in enumerate(rows[:25], 1):
        ts = str(r.get('created_at', ''))
        date_part, time_part = ts[:10], ts[11:16]
        total = r.get('total', 0)
        pct = (r.get('correct', 0) / max(1, total)) * 100
        text += f"{i}. 📅 {date_part} 🕒 {time_part}\n   ✅ {r.get('correct',0)} | ❌ {r.get('wrong',0)} | ⏭️ {r.get('skipped',0)} | 🎯 {pct:.0f}% | Mark: {r.get('mark',0)}\n"
    if len(rows) > 25:
        text += f"\n... আরো {len(rows)-25} টি record"
    total_q = sum(r.get('total', 0) for r in rows)
    total_r = sum(r.get('correct', 0) for r in rows)
    avg = (total_r / total_q * 100) if total_q else 0
    text += f"\n━━━━━━━━━━━━━━━━━━━━━━\n📊 **{days} দিনের Avg Accuracy: {avg:.1f}%**\n\n{get_feedback(avg)}"
    await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ------------------------------------------------------------
# /class — preserved
# ------------------------------------------------------------

def get_classes() -> List[Dict]:
    try:
        client = get_supabase()
        result = client.table('classes').select('*').order('id', desc=False).execute()
        return result.data or []
    except Exception as e:
        log_error(f"get_classes error: {e}")
        return []

async def cmd_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    user_id = user['user_id']
    log(f"🎓 /class from {user_id}")
    if is_admin(user_id) and context.args and context.args[0].lower() == 'add':
        _pending_input[user_id] = {'type': 'class_add'}
        await update.message.reply_text(
            "➕ **Add Class**\n\nFormat (এক লাইনে, | দিয়ে আলাদা):\n`Subject Name | Chapter Name | YouTube Link`\n\nযেমন:\n`Biology | Chapter 1 | https://youtube.com/watch?v=xxxx`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    rows = get_classes()
    if not rows:
        msg = "📭 এখনো কোনো class add করা হয়নি।"
        if is_admin(user_id):
            msg += "\n\n🔧 Add করতে: `/class add`"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        return
    subjects = []
    for r in rows:
        if r['subject'] not in subjects:
            subjects.append(r['subject'])
    keyboard = [[InlineKeyboardButton(f"📘 {sub}", callback_data=f"cls_s_{i}")] for i, sub in enumerate(subjects)]
    context.bot_data['class_subjects'] = subjects
    extra = "\n\n🔧 Admin: `/class add` দিয়ে নতুন class যোগ করুন" if is_admin(user_id) else ""
    await update.message.reply_text(
        f"🚀 **এটলাসের সকল ফ্রী ক্লাস করতে তোমাকে স্বাগতম প্রিয় শিক্ষার্থী {user['first_name']}!**\n\n📚 Subject সিলেক্ট করো:{extra}",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
    )

async def handle_class_subject(query, idx: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    subjects = context.bot_data.get('class_subjects', [])
    if idx >= len(subjects):
        await query.message.reply_text("❌ Subject পাওয়া যায়নি। আবার /class দিন।")
        return
    subject = subjects[idx]
    rows = [r for r in get_classes() if r['subject'] == subject]
    keyboard = [[InlineKeyboardButton(f"▶️ {r['chapter']}", url=r['link'])] for r in rows]
    await query.message.reply_text(
        f"📘 **{subject}**\n\n👇 Chapter এ click করলেই YouTube এ class শুরু হবে:",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
    )

# ------------------------------------------------------------
# Premium PDF (calls exam_server /api/premium-pdf) — preserved
# ------------------------------------------------------------

async def handle_premium_pdf(query, quiz_id: str) -> None:
    log(f"💎 Premium PDF: {quiz_id}")
    mcq_data = await get_mcq(quiz_id)
    if not mcq_data:
        await query.message.reply_text("❌ MCQ data পাওয়া যায়নি।")
        return
    wait_msg = await query.message.reply_text("💎 **Premium PDF তৈরি হচ্ছে...**\n⏱️ অনুগ্রহ করে অপেক্ষা করুন (10-20 sec)", parse_mode=ParseMode.MARKDOWN)
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(f"{HF_SPACE_URL}/api/premium-pdf/{quiz_id}")
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and "pdf" in ct:
                pdf_bytes = resp.content
            else:
                try:
                    j = resp.json()
                    reason = j.get("message") or j.get("reason") or f"status {resp.status_code}"
                except Exception:
                    reason = f"status {resp.status_code}, non-JSON response"
                raise Exception(f"PDF API failed: {reason}")
        pdf_file = BytesIO(pdf_bytes)
        pdf_file.name = f"ATLAS_Practice_Sheet_{quiz_id[:6]}.pdf"
        await query.message.chat.send_document(
            document=pdf_file,
            caption="💎 **ATLAS Practice Sheet (Premium PDF)**\n🚀 সেরা গাইডলাইনে গোছানো প্রস্তুতি - এটলাস",
            parse_mode=ParseMode.MARKDOWN
        )
        try:
            await wait_msg.delete()
        except Exception:
            pass
    except Exception as e:
        log_error(f"Premium PDF error: {e}")
        try:
            await wait_msg.edit_text(f"❌ **Premium PDF তৈরি করা যায়নি**\n📋 কারণ: {str(e)[:200]}", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

# ------------------------------------------------------------
# v4.0: /keys — owner-only model+key analytics (single message)
# ------------------------------------------------------------

async def cmd_keys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    _reset_provider_stats_if_new_day()
    # Build key inventory from env (reflects HF secrets live on restart)
    inventory = [
        ("gemini", GEMINI_KEYS),
        ("groq", GROQ_KEYS),
        ("nvidia", NVIDIA_KEYS),
        ("openrouter-qwen", OPENROUTER_KEYS),
        ("nemotron", NEMOTRON_KEYS or OPENROUTER_KEYS),
        ("gemma", GEMMA_KEYS or OPENROUTER_KEYS),
    ]
    today = datetime.now(BD_TZ).strftime('%Y-%m-%d')
    lines = [f"🔑 **ATLAS AI KEYS & QUOTA**", f"📅 {today} (BD)", "━━━━━━━━━━━━━━━━━━━━━━"]
    total_ok = total_fail = 0
    total_keys = 0
    total_active = 0
    total_healthy = 0
    total_exhausted = 0
    total_idle = 0
    for provider, keys in inventory:
        hint = PROVIDER_QUOTA_HINTS.get(provider, {})
        plabel = hint.get("label", provider)
        reset = hint.get("reset", "—")
        rpd = hint.get("rpd", "?")
        pstat = _provider_stats.get(provider, {})
        n_keys = len(keys)
        total_keys += n_keys
        if n_keys == 0:
            lines.append(f"\n*{plabel}*\n  ⚪ কোনো key সেট নেই")
            continue
        rpd_int = rpd if isinstance(rpd, int) else 0
        lines.append(f"\n*{plabel}*  (RPD≈{rpd}/key · reset: {reset})")
        for i, k in enumerate(keys):
            klabel = f"{provider}#{i+1}"
            ks = pstat.get(klabel, {})
            ok = ks.get("ok", 0)
            fail = ks.get("fail", 0)
            exhausted = ks.get("exhausted", False)
            last = ks.get("last", "—")
            total_ok += ok
            total_fail += fail
            used_today = ok + fail
            remaining = max(0, rpd_int - used_today) if rpd_int > 0 else "?"
            if exhausted:
                status = "🔴 Exhausted"
                total_exhausted += 1
                remaining = 0
            elif ok > 0 or fail > 0:
                status = "🟢 Active"
                total_active += 1
                total_healthy += 1
            else:
                status = "⚪ Idle (untested)"
                total_active += 1
                total_idle += 1
            lines.append(f"  {i+1}. `{_key_prefix(k)}` {status}")
            lines.append(f"     ✅{ok} ❌{fail} | 📊 Used:{used_today}/{rpd} | 🟩 বাকি:{remaining} | 🕐{last}")
    # Overall daily attempt capacity estimate
    cap = 0
    for provider, keys in inventory:
        rpd = PROVIDER_QUOTA_HINTS.get(provider, {}).get("rpd", 0)
        if isinstance(rpd, int):
            # avoid double counting openrouter shared keys
            if provider in ("nemotron", "gemma") and not (NEMOTRON_KEYS if provider=="nemotron" else GEMMA_KEYS):
                continue
            cap += rpd * len(keys)
    images_per_day = cap  # 1 attempt ≈ 1 image/text generation
    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 **Summary**")
    lines.append(f"  🔑 Total keys: {total_keys}")
    lines.append(f"  🟢 Healthy (used today, no quota issue): {total_healthy}")
    lines.append(f"  ⚪ Untested (not used yet): {total_idle}")
    lines.append(f"  🔴 Exhausted/Problem: {total_exhausted}")
    lines.append(f"  ✅ Today success: {total_ok} · ❌ fail: {total_fail}")
    lines.append(f"  📈 আনুমানিক দৈনিক capacity: ~{cap} attempts")
    lines.append(f"  🖼️ আনুমানিক দৈনিক image/text MCQ: ~{images_per_day} টি")
    lines.append(f"\n💡 HF Secrets এ নতুন key যোগ করে restart দিলে এখানে auto update হবে।")
    text = "\n".join(lines)
    # Telegram 4096-char limit: chunk safely
    if len(text) <= 4000:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    else:
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 1 > 3900:
                await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
                chunk = ""
            chunk += line + "\n"
        if chunk:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)

# ------------------------------------------------------------
# v4.0: PENDING INPUT ROUTER (pomo/rev/rand/class + bmexam_count + gpa)
# ------------------------------------------------------------

async def handle_pending_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    state = _pending_input.get(user_id)
    if not state:
        return
    text = (update.message.text or '').strip()
    stype = state['type']
    # gpa_ssc keeps state alive for a second question, so don't pop prematurely there
    if stype != 'gpa_ssc':
        _pending_input.pop(user_id, None)
    try:
        if stype == 'pomo_custom':
            try:
                minutes = max(1, min(10000, int(re.sub(r'[^0-9]', '', text) or '0')))
            except Exception:
                minutes = 0
            if minutes <= 0:
                await update.message.reply_text("❌ সঠিক সংখ্যা লিখুন (যেমন: 30)। আবার /timer দিন।")
            else:
                await start_pomodoro(update.effective_chat.id, update.effective_user.first_name or "User", minutes)
        elif stype == 'rev_count':
            await _start_practice_set(update.message, update.effective_user, state['mode'], text)
        elif stype == 'rand_count':
            await _start_practice_set(update.message, update.effective_user, 'random', text)
        elif stype == 'bmexam_count':
            await _start_practice_set(update.message, update.effective_user, 'bookmark', text)
        elif stype == 'gpa_ssc':
            # validate SSC GPA then ask HSC
            try:
                ssc = float(re.sub(r'[^0-9.]', '', text) or '0')
            except Exception:
                ssc = 0
            if ssc <= 0 or ssc > 5:
                _pending_input.pop(user_id, None)
                await update.message.reply_text("❌ সঠিক SSC GPA লিখুন (0.00 - 5.00)। আবার /gpa দিন।")
            else:
                _pending_input[user_id] = {'type': 'gpa_hsc', 'ssc': ssc}
                await update.message.reply_text("২) আপনার HSC GPA কত? (যেমন: 5.00)")
        elif stype == 'gpa_hsc':
            try:
                hsc = float(re.sub(r'[^0-9.]', '', text) or '0')
            except Exception:
                hsc = 0
            ssc = state.get('ssc', 0)
            if hsc <= 0 or hsc > 5:
                await update.message.reply_text("❌ সঠিক HSC GPA লিখুন (0.00 - 5.00)। আবার /gpa দিন।")
            else:
                # MBBS admission: SSC GPA×8 + HSC GPA×12 = /100
                ssc_part = ssc * 8
                hsc_part = hsc * 12
                score = ssc_part + hsc_part
                kata = 100 - score
                await update.message.reply_text(
                    f"🎯 **MBBS GPA Score Result**\n━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📘 SSC GPA: {ssc:.2f} × 8 = **{ssc_part:.2f}**\n"
                    f"📗 HSC GPA: {hsc:.2f} × 12 = **{hsc_part:.2f}**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🚀 **MBBS ভর্তি পরীক্ষায় আপনার GPA Score: ({score:.2f}/100)**\n"
                    f"✅ **কাটা যাবে: ({kata:.2f})**\n\n"
                    f"💪 বাকি {kata:.0f} নম্বরের জন্য MCQ practice চালিয়ে যান!",
                    parse_mode=ParseMode.MARKDOWN
                )
        elif stype == 'class_add':
            parts = [p.strip() for p in text.split('|')]
            if len(parts) < 3 or not parts[2].startswith('http'):
                await update.message.reply_text("❌ Format ভুল। আবার `/class add` দিন।\nFormat: `Subject | Chapter | YouTube Link`", parse_mode=ParseMode.MARKDOWN)
            else:
                client = get_supabase()
                row = {'subject': parts[0], 'chapter': parts[1], 'link': parts[2], 'created_at': datetime.now(BD_TZ).isoformat()}
                client.table('classes').insert(row).execute()
                mirror_insert('classes', row)
                await update.message.reply_text(f"✅ Class added!\n📘 {parts[0]} → {parts[1]}\n🔗 {parts[2]}")
                log(f"🎓 Class added: {parts[0]} / {parts[1]}")
    except Exception as e:
        log_error(f"Pending input error ({stype}): {e}")
        await safe_user_reply(update.message)
    raise ApplicationHandlerStop

# ============================================================
# SECTION 21C: v4.0 BACKGROUND TASKS (check-in + keep-alive)
# ============================================================

async def _scheduled_restart_task() -> None:
    """v-RAM-fix: clean self-exit every 12h so Render restarts the process
    fresh, fully resetting RAM regardless of any leak. Safe because Render
    auto-restarts on process exit, and webhook mode has no in-flight state
    to lose (unlike long-polling)."""
    await asyncio.sleep(12 * 3600)
    log("🔄 Scheduled restart: exiting cleanly for fresh RAM (Render will auto-restart)")
    os._exit(0)


async def _memory_cleanup_task() -> None:
    """v-RAM-fix: periodic hard trim + gc every 30 min, so caches/leaks never
    accumulate over days/weeks/months even if a cap is missed somewhere."""
    import gc
    await asyncio.sleep(300)
    while True:
        try:
            try:
                from exam_server import exam_store, _EXAM_STORE_MAX
                if len(exam_store) > _EXAM_STORE_MAX:
                    excess = len(exam_store) - _EXAM_STORE_MAX
                    for _ in range(excess):
                        exam_store.pop(next(iter(exam_store)), None)
                exam_count = len(exam_store)
            except Exception:
                exam_count = -1
            if len(_image_cache) > _IMAGE_CACHE_MAX:
                excess = len(_image_cache) - _IMAGE_CACHE_MAX
                for _ in range(excess):
                    _image_cache.pop(next(iter(_image_cache)), None)
            gc.collect()
            log(f"🧹 Memory cleanup: exam_store={exam_count}, image_cache={len(_image_cache)}")
        except Exception as e:
            log_error(f"[MemCleanup] {e}")
        await asyncio.sleep(1800)


async def _ram_guard_task() -> None:
    """Proactive RSS watchdog for 512MB Render instances: checks own process
    RSS every 60s. On free tier, hitting the OS memory limit means Render
    hard-kills the process with no cleanup/logging -- this self-restarts
    cleanly at 85% (~435MB) BEFORE that happens.
    Auto-failover: right before self-restarting, if RENDER_URL_2 (secondary
    instance) is configured, switches Telegram's webhook to it FIRST -- so
    users get zero downtime instead of waiting for this instance to restart."""
    try:
        import psutil
    except ImportError:
        log("⚠️ [RAMGuard] psutil not installed -> proactive RAM guard disabled")
        return
    proc = psutil.Process(os.getpid())
    limit_mb = 512
    threshold_mb = int(limit_mb * 0.85)
    await asyncio.sleep(60)
    while True:
        try:
            rss_mb = proc.memory_info().rss / (1024 * 1024)
            if rss_mb >= threshold_mb:
                log(f"⚠️ [RAMGuard] RSS {rss_mb:.0f}MB >= {threshold_mb}MB threshold -> failover + self-restart")
                secondary = (os.environ.get("RENDER_URL_2", "") or "").strip()
                if secondary and BOT_TOKEN:
                    try:
                        webhook_url = secondary.rstrip("/") + "/webhook/" + BOT_TOKEN
                        async with httpx.AsyncClient(timeout=8) as _c:
                            r = await _c.post(
                                f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                                json={"url": webhook_url, "drop_pending_updates": False, "max_connections": 40}
                            )
                            if r.json().get("ok"):
                                log(f"✅ [RAMGuard] Webhook switched to SECONDARY: {webhook_url}")
                            else:
                                log_error(f"[RAMGuard] Failover webhook switch failed: {r.json().get('description')}")
                    except Exception as fe:
                        log_error(f"[RAMGuard] Failover attempt error: {fe}")
                await asyncio.sleep(1)
                os._exit(0)
        except Exception as e:
            log_error(f"[RAMGuard] check failed: {e}")
        await asyncio.sleep(60)


async def _local_health_ok() -> bool:
    """Checks the app's OWN /health via localhost (127.0.0.1:7860) instead of
    the public Render URL. If this succeeds, the FastAPI server itself is
    definitely alive and the process isn't hung — any failure on the PUBLIC
    URL in that case is a network/DNS/Render-proxy issue, not a real outage,
    and should not spam the owner with false 'service down' alerts."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("http://127.0.0.1:7860/health")
            return r.status_code == 200
    except Exception:
        return False


async def keepalive_task() -> None:
    """Self-ping own Render URL /health every 5 min for 24/7 uptime
    (prevents Render free-tier sleep). Alerts owner ONCE when a real outage
    starts (confirmed via localhost cross-check, not just the public URL
    failing) and ONCE when it recovers -- never repeats the same alert."""
    await asyncio.sleep(60)
    log("💓 Keep-alive task started")
    fails = 0
    was_down = False
    while True:
        if RENDER_URL:
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    r = await client.get(f"{RENDER_URL}/health")
                    fails = 0 if r.status_code == 200 else fails + 1
            except Exception:
                fails += 1
            if fails >= 3:
                if not await _local_health_ok():
                    if not was_down:
                        await notify_owner(f"🚨 AtlasBot keep-alive: {fails} consecutive /health failures — bot may be down.")
                        was_down = True
                else:
                    fails = 0  # localhost confirms app is fine -- was a network blip, not a real outage
            elif fails == 0 and was_down:
                await notify_owner("✅ AtlasBot keep-alive: service reachable again.")
                was_down = False
        await asyncio.sleep(300)


async def watchdog_task() -> None:
    """Independent watchdog — offset-timed second ping loop that detects
    downtime even if keepalive_task itself crashes, and attempts a self-wake.
    Alerts owner ONCE per outage (confirmed via localhost), not repeatedly."""
    await asyncio.sleep(150)
    log("🐕 Watchdog task started")
    fails = 0
    was_down = False
    while True:
        healthy = False
        if RENDER_URL:
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    r = await client.get(f"{RENDER_URL}/health")
                    healthy = r.status_code == 200
            except Exception:
                healthy = False
        if healthy:
            fails = 0
            if was_down:
                await notify_owner("✅ AtlasBot WATCHDOG: service reachable again.")
                was_down = False
        else:
            fails += 1
            log(f"⚠️ [Watchdog] health check failed ({fails} in a row)")
            if fails >= 2:
                if await _local_health_ok():
                    # App itself is fine -- public URL blip, not a real outage. Don't alert.
                    fails = 0
                    await asyncio.sleep(300)
                    continue
                if not was_down:
                    await notify_owner(f"🚨 AtlasBot WATCHDOG: service unreachable ({fails}x) — attempting self-wake.")
                    was_down = True
                if RENDER_URL:
                    try:
                        async with httpx.AsyncClient(timeout=30) as client:
                            await client.get(f"{RENDER_URL}/health")
                    except Exception:
                        pass
        await asyncio.sleep(300)


async def watchdog2_task() -> None:
    """3rd independent ping layer — different offset/interval than keepalive_task
    and watchdog_task so all three never crash/miss at the same moment.
    Alerts owner ONCE per outage (confirmed via localhost), not repeatedly."""
    await asyncio.sleep(240)
    log("🐕‍🦺 Watchdog-2 task started")
    fails = 0
    was_down = False
    while True:
        healthy = False
        if RENDER_URL:
            try:
                async with httpx.AsyncClient(timeout=25) as client:
                    r = await client.get(f"{RENDER_URL}/health")
                    healthy = r.status_code == 200
            except Exception:
                healthy = False
        if healthy:
            fails = 0
            if was_down:
                await notify_owner("✅ AtlasBot WATCHDOG-2: service reachable again.")
                was_down = False
        else:
            fails += 1
            if fails >= 2:
                if await _local_health_ok():
                    fails = 0
                    await asyncio.sleep(420)
                    continue
                if not was_down:
                    await notify_owner(f"🚨 AtlasBot WATCHDOG-2: unreachable ({fails}x) — self-wake attempt.")
                    was_down = True
                if RENDER_URL:
                    for _ in range(2):
                        try:
                            async with httpx.AsyncClient(timeout=30) as client:
                                await client.get(f"{RENDER_URL}/health")
                            break
                        except Exception:
                            await asyncio.sleep(5)
        await asyncio.sleep(420)


async def cross_bot_watchdog_task() -> None:
    """Mutual watchdog: also pings QuizBot's health endpoint (set via
    QUIZBOT_URL env). If QuizBot looks down, alerts owner ONCE per outage —
    and vice versa QuizBot pings this bot. Two separate services checking
    each other means a single service's total crash still gets detected."""
    quizbot_url = os.getenv("QUIZBOT_URL", "").rstrip("/")
    sca_url = os.getenv("SAVECONTENTATLAS_URL", "https://savecontentatlas.onrender.com").rstrip("/")
    if not quizbot_url and not sca_url:
        return
    await asyncio.sleep(200)
    log("🔗 Cross-bot watchdog (-> QuizBot, SaveContentAtlas) started")
    fails = 0
    was_down = False
    sca_fails = 0
    sca_was_down = False
    while True:
        if quizbot_url:
            healthy = False
            try:
                async with httpx.AsyncClient(timeout=45) as client:
                    r = await client.get(f"{quizbot_url}/health")
                    healthy = r.status_code == 200
                    if not healthy and r.status_code == 404:
                        r2 = await client.get(quizbot_url)
                        healthy = r2.status_code == 200
            except Exception:
                healthy = False
            if healthy:
                fails = 0
                if was_down:
                    await notify_owner("✅ QuizBot reachable again (cross-bot check).")
                    was_down = False
            else:
                fails += 1
                if fails >= 3 and not was_down:
                    await notify_owner(f"🚨 QuizBot unreachable via cross-bot check ({fails}x) — checked from AtlasBot.")
                    was_down = True
                if fails >= 3:
                    try:
                        async with httpx.AsyncClient(timeout=30) as client:
                            await client.get(quizbot_url)
                    except Exception:
                        pass
        if sca_url:
            sca_healthy = False
            try:
                async with httpx.AsyncClient(timeout=45) as client:
                    r2 = await client.get(sca_url)
                    sca_healthy = r2.status_code == 200
            except Exception:
                sca_healthy = False
            if sca_healthy:
                sca_fails = 0
                if sca_was_down:
                    await notify_owner("✅ SaveContentAtlas reachable again (cross-bot check).")
                    sca_was_down = False
            else:
                sca_fails += 1
                if sca_fails >= 3 and not sca_was_down:
                    await notify_owner(f"🚨 SaveContentAtlas unreachable via cross-bot check ({sca_fails}x) — checked from AtlasBot.")
                    sca_was_down = True
                if sca_fails >= 3:
                    try:
                        async with httpx.AsyncClient(timeout=30) as client:
                            await client.get(sca_url)
                    except Exception:
                        pass
        await asyncio.sleep(300)

def _get_active_checkin_users() -> List[int]:
    """Active = used bot on >=2 distinct days within last 3 days (via results table)."""
    try:
        client = get_supabase()
        cutoff = (datetime.now(BD_TZ) - timedelta(days=3)).isoformat()
        rows = client.table('results').select('user_id,created_at').gte('created_at', cutoff).limit(5000).execute().data or []
        daymap: Dict[int, set] = {}
        for r in rows:
            uid = r.get('user_id')
            day = str(r.get('created_at', ''))[:10]
            if uid and day:
                daymap.setdefault(uid, set()).add(day)
        return [uid for uid, days in daymap.items() if len(days) >= 2]
    except Exception as e:
        log_error(f"_get_active_checkin_users error: {e}")
        return []

async def checkin_scheduler() -> None:
    """Every 6h send a check-in poll to active users. OFF 12am-7am BD.
    v4.1: last-sent time persisted in `settings` so a process restart
    (e.g. Render free-tier sleep/wake) doesn't immediately re-fire it."""
    await asyncio.sleep(120)
    log("🔔 Check-in scheduler started")
    while True:
        try:
            now = datetime.now(BD_TZ)
            if 0 <= now.hour < 7:
                # sleep until 7am BD
                nxt = now.replace(hour=7, minute=0, second=0, microsecond=0)
                await asyncio.sleep(max(60, (nxt - now).total_seconds()))
                continue

            last_str = get_setting('checkin_last_sent_at', '')
            if last_str:
                try:
                    last_dt = datetime.fromisoformat(last_str)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=BD_TZ)
                    elapsed = (now - last_dt).total_seconds()
                    if elapsed < 6 * 3600:
                        await asyncio.sleep(max(60, 6 * 3600 - elapsed))
                        continue
                except Exception:
                    pass

            users = _get_active_checkin_users()
            log(f"🔔 Check-in: {len(users)} active users")
            for uid in users:
                try:
                    msg = await application.bot.send_poll(
                        chat_id=uid,
                        question="📚 আজকে পড়াশোনা কেমন চলছে?",
                        options=["🔥 পুরোদমে চলছে", "🙂 মোটামুটি", "😅 এখনো শুরু করিনি", "💪 মোটিভেশন দরকার"],
                        is_anonymous=False,
                    )
                    _checkin_polls[msg.poll.id] = uid
                    await asyncio.sleep(0.1)
                except Forbidden:
                    continue
                except Exception:
                    continue
            set_setting('checkin_last_sent_at', now.isoformat())
        except Exception as e:
            log_error(f"checkin_scheduler error: {e}")
        await asyncio.sleep(6 * 3600)  # every 6 hours

# ============================================================
# SECTION 22: SYSTEM & SETUP
# ============================================================

async def daily_reset_scheduler() -> None:
    log("⏰ Daily reset scheduler started")
    while True:
        try:
            now = datetime.now(BD_TZ)
            midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            wait_seconds = (midnight - now).total_seconds()
            log(f"⏰ Next daily reset in {wait_seconds/3600:.1f} hours")
            await asyncio.sleep(wait_seconds)
            log("🔄 Running daily reset...")
            reset_daily_usage()
            try:
                await enforce_quotas()
                log("✅ Storage quota enforcement complete")
            except Exception as qe:
                log_error(f"enforce_quotas error: {qe}")
            log("✅ Daily reset complete!")
        except Exception as e:
            log_error(f"Daily reset scheduler error: {e}")
            await asyncio.sleep(60)

# ============================================================
# SECTION: /ping — Owner Bot Status Dashboard
# ============================================================

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু Owner ব্যবহার করতে পারবেন।")
        return
    try:
        now = datetime.now(BD_TZ)
        if _bot_start_time:
            uptime_delta = now - _bot_start_time
            days = uptime_delta.days
            hours, rem = divmod(uptime_delta.seconds, 3600)
            mins, secs = divmod(rem, 60)
            uptime_parts = []
            if days > 0:
                uptime_parts.append(f"{days} দিন")
            if hours > 0:
                uptime_parts.append(f"{hours} ঘণ্টা")
            if mins > 0:
                uptime_parts.append(f"{mins} মিনিট")
            uptime_parts.append(f"{secs} সেকেন্ড")
            uptime_str = " ".join(uptime_parts)
            start_str = _bot_start_time.strftime("%Y-%m-%d %I:%M:%S %p")
        else:
            uptime_str = "অজানা"
            start_str = "অজানা"

        try:
            client = get_supabase()
            all_users = client.table('users').select('user_id,first_name,is_permitted,usage_count,daily_limit,last_reset').execute().data or []
        except Exception:
            all_users = []
        total_users = len(all_users)
        permitted_users = sum(1 for u in all_users if u.get('is_permitted'))
        free_users = total_users - permitted_users
        today_str = now.strftime('%Y-%m-%d')
        active_today = sum(1 for u in all_users if u.get('usage_count', 0) > 0)

        inventory = [
            ("gemini", GEMINI_KEYS),
            ("groq", GROQ_KEYS),
            ("nvidia", NVIDIA_KEYS),
            ("openrouter-qwen", OPENROUTER_KEYS),
            ("nemotron", NEMOTRON_KEYS or OPENROUTER_KEYS),
            ("gemma", GEMMA_KEYS or OPENROUTER_KEYS),
            ("cf-workers-ai", [CF_AI_TOKEN] if (CF_ACCOUNT_ID and CF_AI_TOKEN) else []),
        ]
        total_keys = 0
        key_lines = []
        for provider, keys in inventory:
            hint = PROVIDER_QUOTA_HINTS.get(provider, {})
            plabel = hint.get("label", provider)
            n = len(keys)
            total_keys += n
            if n > 0:
                key_lines.append(f"  {plabel}: <b>{n}</b> keys")

        pomo_count = len(_pomodoro_sessions)
        active_quizzes = len(_timer_tasks)

        # v4.1: কোন platform-এ চলছে আর webhook এখন আসলে কোন route-এ আছে তা
        # Telegram থেকে সরাসরি জিজ্ঞেস করে দেখানো হয় (local _failover_active
        # ফ্ল্যাগের উপর নির্ভর না করে — কারণ GitHub Actions watchdog যদি
        # switch করে, HF process নিজে সেটা জানে না)।
        host_label = "🟦 Render"
        route_label = "❓ Unknown"
        try:
            wh_info = await application.bot.get_webhook_info()
            wh_url = wh_info.url or ""
            _render_primary = (os.environ.get("RENDER_URL", "") or "").replace("https://", "").replace("http://", "").rstrip("/")
            _render_secondary = (os.environ.get("RENDER_URL_2", "") or "").replace("https://", "").replace("http://", "").rstrip("/")
            if _render_secondary and _render_secondary in wh_url:
                route_label = "🟠 Render SECONDARY (failover active! Primary down)"
            elif _render_primary and _render_primary in wh_url:
                route_label = "🟢 Render PRIMARY (normal)"
            elif "onrender.com" in wh_url:
                route_label = "🟡 Render (unknown account)"
            elif wh_url:
                route_label = "🟢 Cloudflare Proxy (স্বাভাবিক)"
            else:
                route_label = "⚠️ Webhook সেট নেই"
        except Exception:
            pass

        text = (
            f"📌 <b>ATLAS BOT — STATUS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🟢 <b>Bot Status:</b> Active\n"
            f"🖥️ <b>Host:</b> {host_label}\n"
            f"🔌 <b>Webhook Route:</b> {route_label}\n"
            f"🕐 <b>চালু হয়েছে:</b> {start_str} (BD)\n"
            f"⏱️ <b>Uptime:</b> {uptime_str}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 <b>USERS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  📊 Total Users: <b>{total_users}</b>\n"
            f"  🌟 Permitted: <b>{permitted_users}</b>\n"
            f"  🔒 Free: <b>{free_users}</b>\n"
            f"  🔥 আজ Active: <b>{active_today}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 <b>AI KEYS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  🔑 Total Keys: <b>{total_keys}</b>\n"
        )
        for line in key_lines:
            text += line + "\n"
        text += (
            f"\n━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>LIVE SESSIONS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  🍅 Pomodoro চলছে: <b>{pomo_count}</b>\n"
            f"  📝 Quiz চলছে: <b>{active_quizzes}</b>\n\n"
            f"📅 <b>Date:</b> {now.strftime('%Y-%m-%d %I:%M %p')} (BD)"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        log_error(f"cmd_ping error: {e}")
        await update.message.reply_text("⏳ সমস্যা হয়েছে। আবার চেষ্টা করুন।")

# ============================================================
# SECTION: LIVE QUIZ (CSV-based, auto-pin pre-message)
# ============================================================

_live_quiz_sessions: Dict[int, Dict] = {}

async def cmd_live(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    reply = update.message.reply_to_message
    if not reply or not reply.document:
        await update.message.reply_text(
            "📋 <b>Live Quiz - ব্যবহার:</b>\n\n"
            "1. একটি CSV ফাইল আপলোড করুন\n"
            "2. সেই ফাইলে reply দিয়ে /live লিখুন\n\n"
            "<b>CSV Format:</b>\n"
            "<code>question,option_a,option_b,option_c,option_d,answer,explanation</code>\n\n"
            "answer = 0(A), 1(B), 2(C), 3(D)\n"
            "explanation ঐচ্ছিক",
            parse_mode=ParseMode.HTML
        )
        return
    file_name = reply.document.file_name or ""
    if not file_name.lower().endswith('.csv'):
        await update.message.reply_text("❌ শুধু CSV ফাইল সাপোর্টেড। .csv এক্সটেনশনের ফাইল দিন।")
        return
    try:
        wait_msg = await update.message.reply_text("⏳ CSV ফাইল পড়া হচ্ছে...")
        file = await reply.document.get_file()
        file_bytes = bytes(await file.download_as_bytearray())
        csv_text = file_bytes.decode('utf-8-sig')
        reader = csv.reader(StringIO(csv_text))
        rows = list(reader)
        if len(rows) < 2:
            await wait_msg.edit_text("❌ CSV ফাইলে কোনো প্রশ্ন পাওয়া যায়নি। Header + data rows থাকতে হবে।")
            return
        header = [h.strip().lower() for h in rows[0]]
        mcqs = []
        for row_num, row in enumerate(rows[1:], 2):
            if len(row) < 5:
                continue
            try:
                q = row[0].strip()
                opts = [row[1].strip(), row[2].strip(), row[3].strip(), row[4].strip()]
                ans = int(row[5].strip()) if len(row) > 5 and row[5].strip().isdigit() else 0
                exp = row[6].strip() if len(row) > 6 else ""
                if not q or not all(opts):
                    continue
                mcqs.append({
                    'question': q,
                    'options': opts,
                    'answer': min(ans, 3),
                    'explanation': exp,
                })
            except Exception:
                continue
        if not mcqs:
            await wait_msg.edit_text("❌ CSV থেকে কোনো valid MCQ parse করা যায়নি। Format চেক করুন।")
            return
        chat_id = update.effective_chat.id
        settings = get_all_settings()
        timer = int(settings.get('timer_seconds', DEFAULT_TIMER))
        pre_text = (
            f"🔴 <b>LIVE QUIZ শুরু হচ্ছে!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📝 মোট প্রশ্ন: <b>{len(mcqs)}</b>\n"
            f"⏱️ প্রতি প্রশ্নে: <b>{timer}</b> সেকেন্ড\n"
            f"📄 Source: <b>{file_name}</b>\n\n"
            f"⚡ প্রস্তুত হও! কিছুক্ষণের মধ্যেই প্রশ্ন আসবে!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
        pre_msg = await application.bot.send_message(
            chat_id=chat_id, text=pre_text, parse_mode=ParseMode.HTML
        )
        try:
            await application.bot.pin_chat_message(
                chat_id=chat_id, message_id=pre_msg.message_id, disable_notification=False
            )
        except Exception as pin_err:
            log_error(f"Live quiz pin error: {pin_err}")
        await wait_msg.edit_text(f"✅ {len(mcqs)} টি প্রশ্ন পাওয়া গেছে! Live Quiz শুরু হচ্ছে...")
        await send_countdown(chat_id)
        for i, mcq in enumerate(mcqs):
            try:
                q_text = f"প্রশ্ন {i+1}/{len(mcqs)}\n\n{mcq['question']}"
                if len(q_text) > 300:
                    q_text = q_text[:297] + "..."
                exp_text = mcq.get('explanation', '')
                if len(exp_text) > 200:
                    exp_text = exp_text[:197] + "..."
                options = mcq['options'][:4]
                correct_id = mcq.get('answer', 0)
                try:
                    correct_id = int(correct_id)
                except (TypeError, ValueError):
                    correct_id = 0
                if correct_id >= len(options) or correct_id < 0:
                    correct_id = 0
                await application.bot.send_poll(
                    chat_id=chat_id, question=q_text, options=options,
                    type=Poll.QUIZ, correct_option_id=correct_id,
                    explanation=exp_text or None, is_anonymous=True,
                    open_period=timer,
                )
                if i < len(mcqs) - 1:
                    await asyncio.sleep(timer + 1)
            except Exception as e:
                log_error(f"Live quiz Q{i+1} error: {e}")
                continue
        done_text = (
            f"✅ <b>LIVE QUIZ শেষ!</b>\n\n"
            f"📝 মোট {len(mcqs)} টি প্রশ্ন পাঠানো হয়েছে।\n"
            f"📊 উপরে scroll করে সব answer দেখুন!"
        )
        await application.bot.send_message(chat_id=chat_id, text=done_text, parse_mode=ParseMode.HTML)
        log(f"🔴 Live Quiz completed: {len(mcqs)} questions, chat={chat_id}")
    except Exception as e:
        log_error(f"cmd_live error: {e}")
        await update.message.reply_text("⏳ কিছু একটা সমস্যা হয়েছে। অনুগ্রহ করে আবার চেষ্টা করুন। 🙏")

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))[:1500] if err else str(context.error)
    log_error(f"Uncaught handler error: {err}\n{tb}")
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "⏳ কিছু একটা সমস্যা হয়েছে। অনুগ্রহ করে আবার চেষ্টা করুন। 🙏"
            )
    except Exception:
        pass

async def register_handlers() -> None:
    application.add_error_handler(global_error_handler)
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("all", cmd_all))
    application.add_handler(CommandHandler("bm", cmd_bm))
    application.add_handler(CommandHandler("bmexam", cmd_bmexam))
    application.add_handler(CommandHandler("gpa", cmd_gpa))
    application.add_handler(CommandHandler("info", cmd_info))
    application.add_handler(CommandHandler("permit", cmd_permit))
    application.add_handler(CommandHandler("limit", cmd_limit))
    application.add_handler(CommandHandler("free", cmd_free))
    application.add_handler(CommandHandler("daily", cmd_daily))
    application.add_handler(CommandHandler("setneg", cmd_setneg))
    application.add_handler(CommandHandler("settimer", cmd_settimer))
    application.add_handler(CommandHandler("tag", cmd_tag))
    application.add_handler(CommandHandler("exp", cmd_exp))
    application.add_handler(CommandHandler("log", cmd_log))
    application.add_handler(CommandHandler("error", cmd_error))
    application.add_handler(CommandHandler("keys", cmd_keys))
    application.add_handler(CommandHandler("prompt", cmd_prompt))
    application.add_handler(CommandHandler("send", cmd_send))
    application.add_handler(CommandHandler("timer", cmd_timer))
    application.add_handler(CommandHandler("live", cmd_live))
    application.add_handler(CommandHandler("ping", cmd_ping))
    application.add_handler(CommandHandler("progress", cmd_progress))
    application.add_handler(CommandHandler("revision", cmd_revision))
    application.add_handler(CommandHandler("report", cmd_report))
    application.add_handler(CommandHandler("random", cmd_random))
    application.add_handler(CommandHandler("class", cmd_class))
    application.add_handler(CommandHandler("pdfc", cmd_pdfc))
    application.add_handler(CommandHandler("done", cmd_pdfc_done))
    application.add_handler(CommandHandler("cancel", cmd_pdfc_cancel))
    application.add_handler(CommandHandler("atlas", cmd_atlas))
    application.add_handler(CommandHandler("txt", cmd_txt))
    application.add_handler(PollAnswerHandler(handle_poll_answer))
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE | filters.Document.PDF, handle_image))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pending_input), group=0)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_prompt_edit_text), group=1)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text), group=2)
    application.add_handler(CallbackQueryHandler(handle_callback))
    log("✅ All handlers registered")

async def set_bot_commands() -> None:
    """User menu = user commands only. Owner gets full set via owner-scope."""
    try:
        user_commands = [
            BotCommand("start", "🤖 বট শুরু / স্বাগতম"),
            BotCommand("all", "📚 আমার সব MCQ সেট দেখুন"),
            BotCommand("bmexam", "🔖 Bookmark MCQ দিয়ে Exam"),
            BotCommand("gpa", "🎯 MBBS GPA Score হিসাব"),
            BotCommand("timer", "🍅 Pomodoro Study Timer"),
            BotCommand("revision", "🔁 আগের MCQ ঝালাই"),
            BotCommand("random", "🎲 Random MCQ Practice"),
            BotCommand("atlas", "📊 Poll-এ reply করে অপশন ব্যাখ্যা"),
            BotCommand("progress", "📊 নিজের অগ্রগতি দেখুন"),
            BotCommand("report", "📈 বিগত দিনের Report"),
            BotCommand("class", "🎓 এটলাসের ফ্রী ক্লাস"),
            BotCommand("bm", "🔖 Bookmark PDF ডাউনলোড"),
            BotCommand("pdfc", "📸 একাধিক Image → PDF"),
            BotCommand("help", "❓ সাহায্য"),
        ]
        await application.bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
        # Owner gets everything (user + admin)
        if OWNER_ID:
            from telegram import BotCommandScopeChat
            owner_commands = user_commands + [
                BotCommand("info", "👥 ইউজার রিপোর্ট"),
                BotCommand("keys", "🔑 AI Keys/Quota analytics"),
                BotCommand("permit", "✅ ইউজার পারমিট"),
                BotCommand("limit", "⚙️ লিমিট সেট"),
                BotCommand("free", "🔢 ফ্রি লিমিট"),
                BotCommand("daily", "🔢 পারমিটেড লিমিট"),
                BotCommand("setneg", "➖ নেগেটিভ মার্ক"),
                BotCommand("settimer", "⏱️ কুইজ টাইমার"),
                BotCommand("tag", "🏷️ কুইজ ট্যাগ"),
                BotCommand("exp", "📝 Exp suffix"),
                BotCommand("prompt", "📋 প্রম্পট ম্যানেজ"),
                BotCommand("send", "📨 ব্রডকাস্ট"),
                BotCommand("log", "📋 এরর লগ"),
                BotCommand("error", "🚨 Latest error"),
            ]
            try:
                await application.bot.set_my_commands(owner_commands, scope=BotCommandScopeChat(chat_id=OWNER_ID))
            except Exception as e:
                log_error(f"Owner command scope set failed: {e}")
        log("✅ Bot commands set (user menu + owner full)")
    except Exception as e:
        log_error(f"Failed to set commands: {e}")

async def setup_bot() -> None:
    global application, BOT_USERNAME
    log("🚀 Setting up bot application...")
    # ConnectError/TLS handshake failures (httpx start_tls) were happening when
    # the bot tried to reach the Cloudflare Worker proxy from inside the HF
    # Space container — the same failure pattern already fixed for the
    # Supabase client by disabling HTTP/2 and keepalive. Applying the same
    # fix here via a custom HTTPXRequest, since ApplicationBuilder's default
    # transport doesn't otherwise let us configure http2/keepalive.
    request_kwargs = dict(
        connection_pool_size=8,
        connect_timeout=30,
        read_timeout=60,
        write_timeout=60,
        pool_timeout=30,
        http_version="1.1",
    )
    bot_request = HTTPXRequest(**request_kwargs)
    get_updates_request = HTTPXRequest(**request_kwargs)
    builder = ApplicationBuilder().token(BOT_TOKEN)
    if IS_RENDER:
        # Render এ আছি — Telegram API সরাসরি reach করা যায়, proxy লাগে না।
        # base_url() না দিলে python-telegram-bot নিজে থেকেই
        # https://api.telegram.org ব্যবহার করে (লাইব্রেরির ডিফল্ট)।
        log(f"🟡 Running on Render — using direct Telegram API (no proxy)")
    else:
        builder = builder.base_url(f"{CF_TG_API_URL}/bot").base_file_url(f"{CF_TG_API_URL}/file/bot")
    application = (
        builder
        .request(bot_request)
        .get_updates_request(get_updates_request)
        .build()
    )
    await register_handlers()
    await set_bot_commands()
    # fetch bot username for deep links (Share & Challenge)
    try:
        me = await application.bot.get_me()
        BOT_USERNAME = me.username or ""
        log(f"🤖 Bot username: @{BOT_USERNAME}")
    except Exception as e:
        log_error(f"get_me failed: {e}")
    async def _supervised(coro_fn, name):
        """Core background task crash korle silently die na kore auto-restart hobe.
        Exponential backoff + alert-spam prevent (repeated crash e max 3 alert)."""
        fail_count = 0
        while True:
            try:
                await coro_fn()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                fail_count += 1
                wait = min(10 * (2 ** min(fail_count - 1, 5)), 300)  # 10s -> 300s cap
                log_error(f"⚠️ [Supervisor] {name} crashed ({fail_count}x): {e} — restarting in {wait}s")
                if fail_count <= 3:
                    try:
                        await notify_owner(f"⚠️ Background task '{name}' crashed ({fail_count}x), auto-restarting: {e}")
                    except Exception:
                        pass
                await asyncio.sleep(wait)

    asyncio.create_task(_supervised(daily_reset_scheduler, "daily_reset_scheduler"))
    asyncio.create_task(_supervised(keepalive_task, "keepalive_task"))
    asyncio.create_task(_supervised(_memory_cleanup_task, "_memory_cleanup_task"))
    asyncio.create_task(_supervised(_ram_guard_task, "_ram_guard_task"))
    asyncio.create_task(_supervised(_scheduled_restart_task, "_scheduled_restart_task"))
    asyncio.create_task(_supervised(watchdog_task, "watchdog_task"))
    asyncio.create_task(_supervised(watchdog2_task, "watchdog2_task"))
    asyncio.create_task(_supervised(cross_bot_watchdog_task, "cross_bot_watchdog_task"))
    asyncio.create_task(_supervised(checkin_scheduler, "checkin_scheduler"))
    asyncio.create_task(_supervised(cf_proxy_health_check_scheduler, "cf_proxy_health_check_scheduler"))
    log("✅ Bot setup complete!")

# ============================================================
# SECTION 22B: CF PROXY HEALTH-CHECK / AUTO-FAILOVER TO RENDER
# ============================================================

_failover_active = False  # true হলে মানে webhook ইতিমধ্যে Render-এ switch হয়ে আছে

async def cf_proxy_health_check_scheduler() -> None:
    """
    ⚠️ PRIMARY failover এখন .github/workflows/watchdog-failover.yml দিয়ে হয় —
    GitHub Actions থেকে সরাসরি api.telegram.org-এ setWebhook করে, CF/HF
    কোনোটার ওপর নির্ভর না করেই, প্রতি ৫ মিনিটে। কারণ: CF proxy down থাকলে
    HF Space নিজে Telegram-কে কিছুই বলতে পারে না (CF-ই একমাত্র outbound
    path, api.telegram.org সরাসরি HF থেকে blocked) — তাই HF-সাইড এই
    scheduler কখনোই pure CF-down অবস্থায় নির্ভরযোগ্যভাবে কাজ করতে পারবে না।

    এই function শুধু best-effort secondary হিসেবে রাখা হয়েছে (Render
    instance যদি জাগ্রত থাকে আর এর /internal/failover-setwebhook
    endpoint reachable হয়, সেই বিশেষ ক্ষেত্রে এটাও কাজ করতে পারে) —
    কিন্তু আসল ভরসা GitHub Actions watchdog।
    """
    global _failover_active
    if IS_RENDER or not RENDER_URL:
        return
    log("🩺 CF proxy health-check scheduler started")
    consecutive_failures = 0
    FAILURE_THRESHOLD = 3
    CHECK_INTERVAL = 120  # সেকেন্ড

    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            import httpx as _hx
            async with _hx.AsyncClient(timeout=15) as _c:
                r = await _c.get(f"{CF_TG_API_URL}/bot{BOT_TOKEN}/getMe")
            ok = r.status_code == 200 and r.json().get("ok")
        except Exception as e:
            ok = False
            log_error(f"[Failover] CF proxy health-check error: {e}")

        if ok:
            if consecutive_failures > 0:
                log("✅ CF proxy recovered")
            consecutive_failures = 0
            if _failover_active:
                # CF proxy আবার সুস্থ — webhook ফিরিয়ে আনা হচ্ছে
                if await _switch_webhook(f"{CF_WORKER_URL}/webhook/{BOT_TOKEN}"):
                    _failover_active = False
                    log("🟢 Webhook switched back to CF proxy")
        else:
            consecutive_failures += 1
            log_error(f"[Failover] CF proxy health-check failed ({consecutive_failures}/{FAILURE_THRESHOLD})")
            if consecutive_failures >= FAILURE_THRESHOLD and not _failover_active:
                if await _trigger_render_self_failover():
                    _failover_active = True
                    log(f"🟡 CF proxy down — Render switched its own webhook to itself")
                else:
                    log_error("[Failover] Could not reach Render to self-switch webhook — both paths unreachable")

async def _trigger_render_self_failover() -> bool:
    """v4.1: CF proxy পুরোপুরি down থাকলে HF নিজে Telegram-কে setWebhook
    বলতে পারে না (proxy-ই একমাত্র outbound path), তাই Render-কে সরাসরি
    (onrender.com domain, CF-independent) request পাঠিয়ে বলা হয় নিজের
    webhook নিজে set করে নিতে — Render api.telegram.org সরাসরি reach
    করতে পারে।"""
    if not RENDER_URL:
        return False
    import httpx as _hx
    try:
        async with _hx.AsyncClient(timeout=20) as _c:
            r = await _c.post(
                f"{RENDER_URL}/internal/failover-setwebhook",
                headers={"X-Bot-Token": BOT_TOKEN}
            )
        return r.status_code == 200
    except Exception as e:
        log_error(f"[Failover] _trigger_render_self_failover failed: {e}")
        return False

async def _switch_webhook(new_url: str) -> bool:
    """setWebhook শুধু CF proxy দিয়েই চেষ্টা করা হয় — HF Space থেকে
    api.telegram.org-এ সরাসরি call নিশ্চিতভাবে blocked, তাই direct
    চেষ্টা করার কোনো মানে নেই (সবসময়ই fail করবে)।

    ⚠️ সীমাবদ্ধতা: CF proxy যদি পুরোপুরি unreachable হয় (পুরো
    pages.dev domain down/DNS fail, শুধু /bot{token} রুটে নির্দিষ্ট
    সমস্যা না), তাহলে HF Space থেকে Telegram-কে webhook switch করতে
    বলার কোনো উপায়ই নেই — কারণ proxy-ই একমাত্র outbound path। এই
    ক্ষেত্রে failover সফল হবে না, এবং bot ম্যানুয়ালি ঠিক করা পর্যন্ত
    অফলাইন থাকবে। এই ফাংশন শুধু সেই ক্ষেত্রে কাজ করবে যেখানে proxy
    domain reachable কিন্তু নির্দিষ্ট bot-API রুটে সমস্যা হচ্ছে।"""
    import httpx as _hx
    payload = {"url": new_url, "drop_pending_updates": False, "max_connections": 40}
    try:
        async with _hx.AsyncClient(timeout=15) as _c:
            r = await _c.post(f"{CF_TG_API_URL}/bot{BOT_TOKEN}/setWebhook", json=payload)
        if r.status_code == 200 and r.json().get("ok"):
            return True
    except Exception as e:
        log_error(f"[Failover] setWebhook via CF proxy failed: {e}")
    return False

# ============================================================
# SECTION 23: WEBHOOK SETUP
# ============================================================

_bot_loop = None

def setup_webhook_route(fastapi_app):
    from fastapi import Request
    from fastapi.responses import PlainTextResponse

    @fastapi_app.post('/webhook')
    async def webhook(request: Request):
        token = request.headers.get('X-Bot-Token', '')
        if token != BOT_TOKEN:
            return PlainTextResponse('Unauthorized', status_code=401)
        data = await request.json()
        if data and application and _bot_loop:
            update = Update.de_json(data, application.bot)
            asyncio.run_coroutine_threadsafe(application.process_update(update), _bot_loop)
        return PlainTextResponse('OK')

    @fastapi_app.post('/webhook/{token}')
    async def webhook_direct(token: str, request: Request):
        # Render fallback mode-এর জন্য — Telegram সরাসরি এই URL কল করে
        # (token path-এ থাকে, কোনো custom header না), তাই request body
        # পাওয়ার সাথে সাথেই token path-parameter দিয়ে যাচাই হয়।
        if token != BOT_TOKEN:
            return PlainTextResponse('Unauthorized', status_code=401)
        data = await request.json()
        if data and application and _bot_loop:
            update = Update.de_json(data, application.bot)
            asyncio.run_coroutine_threadsafe(application.process_update(update), _bot_loop)
        return PlainTextResponse('OK')

    @fastapi_app.post('/internal/failover-setwebhook')
    async def internal_failover_setwebhook(request: Request):
        """v4.1: HF Space CF proxy পুরো unreachable হলে (DNS/domain down),
        HF নিজে Telegram-কে setWebhook বলতে পারে না (CF-ই একমাত্র outbound
        path)। তাই HF এই endpoint দিয়ে Render instance-কে অনুরোধ করে —
        Render সরাসরি api.telegram.org reach করতে পারে (HF Space-এর মতো
        block করা না), তাই সে নিজের webhook URL নিজেই Telegram-এ set করে
        দেয়। শুধু Render mode-এ চলে, এবং token header দিয়ে protected।"""
        token = request.headers.get('X-Bot-Token', '')
        if token != BOT_TOKEN:
            return PlainTextResponse('Unauthorized', status_code=401)
        if not (IS_RENDER and RENDER_URL and application):
            return PlainTextResponse('Not in Render mode', status_code=400)
        try:
            render_webhook = f"{RENDER_URL}/webhook/{BOT_TOKEN}"
            for attempt in range(3):
                try:
                    await application.bot.set_webhook(
                        max_connections=40, url=render_webhook,
                        allowed_updates=["message", "callback_query", "poll_answer", "poll"],
                        drop_pending_updates=False
                    )
                    break
                except Exception as e:
                    wait_s = getattr(e, "retry_after", None) or (2 ** attempt)
                    if attempt == 2:
                        raise
                    log_error(f"[Failover] set_webhook attempt {attempt+1} failed ({e}), retrying in {wait_s}s...")
                    await asyncio.sleep(wait_s)
            log(f"🟡 [Failover] Render self set webhook (requested by HF): {render_webhook}")
            return PlainTextResponse('OK')
        except Exception as e:
            log_error(f"[Failover] internal_failover_setwebhook error: {e}")
            return PlainTextResponse(f'Failed: {e}', status_code=500)

# ============================================================
# SECTION 24: MAIN ENTRY POINT
# ============================================================

async def main() -> None:
    global _bot_loop, _bot_start_time
    _bot_loop = asyncio.get_event_loop()
    _bot_start_time = datetime.now(BD_TZ)
    log("=" * 60)
    log("🚀 ATLAS MCQ BOT STARTING (WEBHOOK MODE) - v4.0")
    log("=" * 60)
    log("📦 Initializing database...")
    init_database()
    get_supabase_backup()
    bind_supabase(get_supabase)
    if d1_enabled():
        await bootstrap_d1_schema()
        log("✅ D1 dual storage enabled")
    else:
        log("ℹ️ D1 not configured — running Supabase-only")
    log("🤖 Setting up Gemini...")
    setup_gemini()
    log("🤖 Setting up bot...")
    await setup_bot()
    await application.initialize()
    await application.start()
    if IS_RENDER and RENDER_URL:
        # Render-এ webhook সরাসরি এই অ্যাপের নিজস্ব URL-এ সেট হয় (proxy লাগে
        # না) — ফলব্যাক রুট setup_webhook_route()-এ /webhook/{token} হিসেবে
        # যোগ করা আছে, ঠিক CF Worker যেভাবে কল করত একই path pattern মেনে,
        # যাতে আলাদা কোনো নতুন handler না লাগে।
        webhook_url = f"{RENDER_URL}/webhook/{BOT_TOKEN}"
    else:
        webhook_url = f"{CF_WORKER_URL}/webhook/{BOT_TOKEN}"
    try:
        current_wh = await application.bot.get_webhook_info()
        if current_wh.url == webhook_url:
            log(f"✅ Webhook already correctly set: {webhook_url} (skipping set_webhook call)")
        else:
            for attempt in range(4):
                try:
                    await application.bot.set_webhook(
                        max_connections=40, url=webhook_url,
                        allowed_updates=["message", "callback_query", "poll_answer", "poll"],
                        drop_pending_updates=True
                    )
                    log(f"✅ Webhook set: {webhook_url}")
                    break
                except Exception as e:
                    wait_s = getattr(e, "retry_after", None) or (2 ** attempt)
                    if attempt == 3:
                        raise
                    log_error(f"Webhook set attempt {attempt+1} failed ({e}), retrying in {wait_s}s...")
                    await asyncio.sleep(wait_s)
    except Exception as e:
        log_error(f"Webhook set failed (will retry on first update): {e}")
    from exam_server import app as fastapi_app
    setup_webhook_route(fastapi_app)
    log("🌐 Starting exam+webhook server on port 7860...")
    import uvicorn
    config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=7860, log_level="warning")
    server = uvicorn.Server(config)
    log("✅ Bot is running in webhook mode!")
    log("=" * 60)
    await server.serve()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("🛑 Bot stopped by user")
    except Exception as e:
        log_error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        log("⚠️ Bot crashed, starting fallback server to keep Space alive...")
        try:
            import uvicorn
            from exam_server import app as fastapi_app
            config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=7860, log_level="warning")
            server = uvicorn.Server(config)
            asyncio.run(server.serve())
        except Exception as e2:
            log_error(f"Fallback server also failed: {e2}")
