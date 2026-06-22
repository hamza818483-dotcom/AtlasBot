"""
Shared configuration constants used by both bot.py and exam_server.py.
"""

import os
from datetime import datetime, timedelta, timezone

# ── Bangladesh Timezone ──
try:
    BD_TZ = timezone(timedelta(hours=6))
except Exception:
    BD_TZ = timezone(timedelta(hours=6))

# ── Supabase ──
SUPABASE_URL = "https://wbdyjpjbczfunyhhmtry.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndiZHlqcGpiY3pmdW55aGhtdHJ5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA2OTI5ODAsImV4cCI6MjA5NjI2ODk4MH0."
    "0WR1sgVsl_1XWZfSd0Pwoe6Uxp-2GMTksfseMn5aWjg"
)
SUPABASE_BACKUP_URL = os.getenv("SUPABASE_BACKUP_URL", "").rstrip("/")
SUPABASE_BACKUP_KEY = os.getenv("SUPABASE_BACKUP_KEY", "")

# ── AI Provider Keys (comma-separated, silent skip if empty) ──
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GENAI_API_KEY = (
    os.getenv("GEMINI_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
).strip()
GEMINI_KEYS = [k.strip() for k in GENAI_API_KEY.split(",") if k.strip()]

GROQ_KEYS = [k.strip() for k in os.getenv("GROQ_KEY", "").split(",") if k.strip()]
GROQ_MODEL = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

NVIDIA_KEYS = [k.strip() for k in os.getenv("NVIDIA_KEY", "").split(",") if k.strip()]
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct")

OPENROUTER_KEYS = [k.strip() for k in os.getenv("OPENROUTER_KEY", "").split(",") if k.strip()]
OPENROUTER_QWEN_MODEL = os.getenv("OPENROUTER_QWEN_MODEL", "qwen/qwen2.5-vl-72b-instruct:free")

NEMOTRON_KEYS = [k.strip() for k in os.getenv("NEMOTRON_KEY", "").split(",") if k.strip()]
NEMOTRON_MODEL = os.getenv("NEMOTRON_MODEL", "nvidia/nemotron-nano-12b-v2-vl:free")

GEMMA_KEYS = [k.strip() for k in os.getenv("GEMMA_KEY", "").split(",") if k.strip()]
GEMMA_MODEL = os.getenv("GEMMA_MODEL", "google/gemma-3-27b-it:free")

# ── URLs ──
HF_SPACE_URL = os.getenv("HF_SPACE_URL", "https://hamzahf1-atlasbot.hf.space")
CF_WORKER_URL = os.getenv("CF_WORKER_URL", "https://atlas-bot-proxy.hamza818483.workers.dev").rstrip("/")

# ── Limits ──
DEFAULT_TIMER = 30
DEFAULT_FREE_LIMIT = 3
DEFAULT_DAILY_LIMIT = 5
DEFAULT_NEGATIVE_MARK = -0.50
NEW_PRACTICE_COUNT = 15
MAX_MCQ = 35
MIN_MCQ = 10
POLL_DELAY = 1.5

FREE_NEW_EXAM_LIMIT = 2
PERMITTED_NEW_EXAM_LIMIT = 20

SEC_PER_QUESTION = 30
NEGATIVE_MARK = 0.50

# ── Prompt display names ──
PROMPT_DISPLAY_NAMES = {
    "prompt_1": "🩺 Medical Standard MCQ",
    "prompt_2": "✅ সত্য-মিথ্যার প্রশ্ন",
    "prompt_3": "🔥 কঠিন প্রশ্ন",
    "prompt_mixed": "🎲 Mixed",
}

# ── Logging ──
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
