"""Account-wise Gemini key pool (shared by bot.py and exam_server.py).

Env formats (first one found wins):
  GEMINI_KEYS_ACC1 = "keyA,keyB"   GEMINI_KEYS_ACC2 = "keyC,keyD" ...
      -> one env var per Google account (same as QuizBot). Preferred.
  GEMINI_ACCOUNTS = "acc1:keyA,keyB;acc2:keyC;acc3:keyD,keyE"
      -> each ';' group is one Google account/project. Free-tier quota is
         per project, so load is spread ACROSS accounts first, then across
         the keys inside an account.
  GEMINI_KEY / GEMINI_API_KEY / GOOGLE_API_KEY = "k1,k2,k3"
      -> legacy: all keys treated as one account each (max spread).

Every call to pick() returns the next healthy key in a global round-robin
that alternates accounts, so concurrent users are spread out evenly instead
of all hitting the same key. Thread-safe (used from async + worker threads).
"""
import os
import threading
import time
from typing import Dict, List, Optional, Tuple

_lock = threading.Lock()


def _parse() -> List[Tuple[str, List[str]]]:
    accounts: List[Tuple[str, List[str]]] = []
    # Preferred: one env var per account (same as QuizBot), e.g.
    #   GEMINI_KEYS_ACC1=keyA,keyB   GEMINI_KEYS_ACC2=keyC,keyD
    for env_name in sorted(k for k in os.environ if k.startswith("GEMINI_KEYS_ACC")):
        ks = [k.strip() for k in os.environ.get(env_name, "").split(",") if k.strip()]
        if ks:
            accounts.append((env_name.replace("GEMINI_KEYS_", ""), ks))
    if accounts:
        return accounts
    raw = (os.getenv("GEMINI_ACCOUNTS") or "").strip()
    if raw:
        for i, grp in enumerate(g for g in raw.split(";") if g.strip()):
            grp = grp.strip()
            if ":" in grp and not grp.split(":", 1)[0].strip().startswith(("AIza", "AQ.")):
                name, keys = grp.split(":", 1)
            else:
                name, keys = f"acc{i + 1}", grp
            ks = [k.strip() for k in keys.split(",") if k.strip()]
            if ks:
                accounts.append((name.strip() or f"acc{i + 1}", ks))
    if not accounts:
        legacy = (os.getenv("GEMINI_KEY") or os.getenv("GEMINI_API_KEY")
                  or os.getenv("GOOGLE_API_KEY") or "").strip()
        for i, k in enumerate(k.strip() for k in legacy.split(",") if k.strip()):
            accounts.append((f"key{i + 1}", [k]))
    return accounts


ACCOUNTS: List[Tuple[str, List[str]]] = _parse()

# Flat, interleaved order: a1k1, a2k1, a3k1, a1k2, a2k2 ...  so consecutive
# picks always land on DIFFERENT accounts.
def _interleave() -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    depth = max((len(k) for _, k in ACCOUNTS), default=0)
    for d in range(depth):
        for name, keys in ACCOUNTS:
            if d < len(keys):
                out.append((name, keys[d]))
    return out


_ORDER: List[Tuple[str, str]] = _interleave()
KEYS: List[str] = [k for _, k in _ORDER]
LABELS: Dict[str, str] = {k: f"gemini#{i + 1}" for i, (n, k) in enumerate(_ORDER)}   # matches /keys panel lookup
ACCOUNT_OF: Dict[str, str] = {k: n for n, k in _ORDER}

_rr = 0
_cooldown_until: Dict[str, float] = {}   # key -> epoch
_dead_day: Dict[str, str] = {}           # key -> YYYY-MM-DD (quota gone for the day)
_clients: Dict[str, object] = {}
_inflight: Dict[str, int] = {}
PER_KEY_CONCURRENCY = int(os.getenv("GEMINI_PER_KEY_CONCURRENCY", "3"))


def _today() -> str:
    # Gemini free quota resets at Pacific midnight; BD midnight is a safe,
    # conservative marker for our own daily bookkeeping (UTC+6).
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 6 * 3600))


def count() -> int:
    return len(KEYS)


def label(key: str) -> str:
    return LABELS.get(key, "gemini")


def account(key: str) -> str:
    return ACCOUNT_OF.get(key, "?")


def is_available(key: str) -> bool:
    if key in _banned:
        return False
    now = time.time()
    if _dead_day.get(key) == _today():
        return False
    if _cooldown_until.get(key, 0.0) > now:
        return False
    return True


def all_dead() -> bool:
    with _lock:
        return bool(KEYS) and all((_dead_day.get(k) == _today() or k in _banned) for k in KEYS)


_acc_rr = 0
_key_rr: Dict[str, int] = {}


def pick(exclude: Optional[set] = None) -> Optional[str]:
    """Two-level fair pick (Google free quota is per PROJECT/account):
      1) choose the next ACCOUNT in round-robin, skipping accounts with no
         healthy key; prefer accounts with spare capacity,
      2) inside that account rotate through its healthy keys.
    Increments the key's in-flight counter: caller MUST call release(key)."""
    if not KEYS:
        return None
    exclude = exclude or set()
    global _acc_rr
    with _lock:
        n = len(ACCOUNTS)
        chosen, fallback = None, None
        for off in range(n):
            ai = (_acc_rr + off) % n
            name, keys = ACCOUNTS[ai]
            healthy = [k for k in keys if k not in exclude and is_available(k)]
            if not healthy:
                continue
            start = _key_rr.get(name, 0)
            ordered = [keys[(start + j) % len(keys)] for j in range(len(keys))]
            ordered = [k for k in ordered if k in healthy]
            spare = [k for k in ordered if _inflight.get(k, 0) < PER_KEY_CONCURRENCY]
            if spare:
                chosen = (ai, name, keys, spare[0])
                break
            if fallback is None:
                fallback = (ai, name, keys, min(ordered, key=lambda k: _inflight.get(k, 0)))
        if chosen is None:
            chosen = fallback
        if chosen is None:
            return None
        ai, name, keys, key = chosen
        _acc_rr = (ai + 1) % n
        _key_rr[name] = (keys.index(key) + 1) % len(keys)
        _inflight[key] = _inflight.get(key, 0) + 1
        return key


def release(key: Optional[str]) -> None:
    if not key:
        return
    with _lock:
        _inflight[key] = max(0, _inflight.get(key, 1) - 1)


def mark_exhausted(key: str) -> None:
    with _lock:
        _dead_day[key] = _today()


def mark_cooldown(key: str, seconds: float = 60.0) -> None:
    with _lock:
        _cooldown_until[key] = time.time() + seconds


def mark_ok(key: str) -> None:
    with _lock:
        _cooldown_until.pop(key, None)


def client(key: str):
    """Cached genai.Client per key (created once, reused, thread-safe)."""
    c = _clients.get(key)
    if c is not None:
        return c
    with _lock:
        c = _clients.get(key)
        if c is None:
            from google import genai
            c = genai.Client(api_key=key)
            _clients[key] = c
        return c


# ── PERMANENT ban (suspended / banned / invalid key): never tried again, survives restart ──
_banned: set = set()
_ban_persist_cb = None        # set by bot.py: fn(key, reason) -> saves to DB (best effort)
_PERM_MARKERS = ("consumer_suspended", "has been suspended", "suspended",
                 "permission_denied", "api key not valid", "api_key_invalid",
                 "api key expired", "key has been disabled", "project has been denied")


def is_banned(key: str) -> bool:
    return key in _banned


def ban(key: str, reason: str = "", persist: bool = True) -> None:
    """Permanently exclude `key` (this process + saved to DB so restarts skip it too)."""
    if not key:
        return
    with _lock:
        newly = key not in _banned
        _banned.add(key)
        _dead_day[key] = _today()
    if newly and persist and _ban_persist_cb:
        try:
            _ban_persist_cb(key, reason)
        except Exception:
            pass


def load_banned(keys) -> int:
    """Called once at startup with keys loaded from DB."""
    n = 0
    with _lock:
        for k in keys or []:
            if k and k not in _banned:
                _banned.add(k); n += 1
    return n


def is_permanent_error(err) -> bool:
    es = str(err).lower()
    return any(m in es for m in _PERM_MARKERS)


def classify_error(err: Exception) -> str:
    """'dead' (quota/suspended for the day) | 'cool' (rate limit / transient) | 'other'."""
    es = str(err).lower()
    if any(s in es for s in ("suspended", "permission_denied", "consumer_suspended",
                              "api key not valid", "api_key_invalid")):
        return "dead"
    if any(s in es for s in ("per day", "daily", "quota", "resource_exhausted", "429")):
        # per-minute limits look the same; treat short-window ones as cooldown
        if any(s in es for s in ("per minute", "retry in", "retrydelay", "rate limit")) and \
           not any(s in es for s in ("per day", "daily")):
            return "cool"
        return "dead" if any(s in es for s in ("per day", "daily", "exhausted")) else "cool"
    if any(s in es for s in ("503", "500", "unavailable", "timeout", "deadline", "overloaded")):
        return "cool"
    return "other"


def status() -> List[str]:
    now = time.time()
    rows = []
    for name, keys in ACCOUNTS:
        for k in keys:
            st = "BANNED" if k in _banned else "dead" if _dead_day.get(k) == _today() else (
                "cool" if _cooldown_until.get(k, 0) > now else "ok")
            rows.append(f"{label(k)} [{name}] {st} inflight={_inflight.get(k, 0)}")
    return rows


def summary() -> Dict[str, int]:
    """Counts for /keys: total / healthy / cooldown / exhausted + per-account."""
    now = time.time()
    out = {"total": 0, "ok": 0, "cool": 0, "dead": 0, "banned": 0, "accounts": 0}
    per = []
    for name, keys in ACCOUNTS:
        a = {"ok": 0, "cool": 0, "dead": 0, "banned": 0}
        for k in keys:
            if k in _banned:
                a["banned"] += 1
            elif _dead_day.get(k) == _today():
                a["dead"] += 1
            elif _cooldown_until.get(k, 0) > now:
                a["cool"] += 1
            else:
                a["ok"] += 1
        per.append((name, len(keys), a))
        out["total"] += len(keys)
        out["ok"] += a["ok"]; out["cool"] += a["cool"]; out["dead"] += a["dead"]; out["banned"] += a["banned"]
    out["accounts"] = len(ACCOUNTS)
    out["per"] = per
    return out


# ─────────────────────────────────────────────────────────────
# REMOTE POOL: use QuizBot's key pool through its /api/gemini-proxy
# (keys live ONLY in QuizBot; AtlasBot just sends the request).
#   QUIZBOT_URL       = https://<quizbot-host>
#   LMS_API_SECRET    = same secret QuizBot uses for /api/gemini-proxy
#   GEMINI_PROXY_FIRST= 1 (default) proxy first, local keys as fallback
# ─────────────────────────────────────────────────────────────
QUIZBOT_URL = (os.getenv("QUIZBOT_URL") or "").strip().rstrip("/")
PROXY_SECRET = (os.getenv("LMS_API_SECRET") or os.getenv("GEMINI_PROXY_SECRET") or "").strip()
PROXY_ENABLED = bool(QUIZBOT_URL and PROXY_SECRET) and os.getenv("GEMINI_PROXY", "1") != "0"
_proxy_down_until = 0.0
_proxy_fail_streak = 0
PROXY_CONCURRENCY = int(os.getenv("GEMINI_PROXY_CONCURRENCY", "6"))


def proxy_available() -> bool:
    return PROXY_ENABLED and time.time() >= _proxy_down_until


def proxy_mark_ok() -> None:
    global _proxy_fail_streak, _proxy_down_until
    _proxy_fail_streak = 0
    _proxy_down_until = 0.0


def proxy_mark_fail(hard: bool = False) -> None:
    """Circuit breaker: after 2 consecutive failures (or a hard auth/config
    error) skip the proxy for a while and use local keys / next provider."""
    global _proxy_fail_streak, _proxy_down_until
    _proxy_fail_streak += 1
    if hard:
        _proxy_down_until = time.time() + 600
    elif _proxy_fail_streak >= 2:
        _proxy_down_until = time.time() + min(120, 20 * _proxy_fail_streak)


def build_proxy_body(prompt_text: str, image_bytes: Optional[bytes],
                     max_tokens: int = 8192, temperature: float = 0.7) -> dict:
    import base64
    parts = [{"text": prompt_text}]
    if image_bytes:
        parts.append({"inline_data": {"mime_type": "image/jpeg",
                                      "data": base64.b64encode(image_bytes).decode()}})
    return {"secret": PROXY_SECRET, "parts": parts,
            "max_tokens": max_tokens, "temperature": temperature}
