"""
ATLAS MCQ BOT - Exam Server (FastAPI)
Version: 3.0

CHANGES FROM v2.4:
  ✅ NEW: জ্ঞানমূলক / অনুধাবনমূলক সৃজনশীল PDF generator (A4, 2-layout, color + shadow box, header)
       Routes: GET /api/creative-pdf/{cache_id}?ctype=knowledge|comprehension
  ✅ NEW: AI-generated creative questions from source image (Gemini, source-only strict)
  ✅ FIX: Premium PDF now justifies content to fill the full A4 page (auto font/gap scaling)
  ✅ FIX: Solve PDF full-page friendly
  ✅ Insufficient-data explanation for creative questions
  ✅ ALL existing v2.4 features preserved 100%
"""

# ============================================================
# SECTION 1: IMPORTS
# ============================================================
import os
import json
import uuid
import base64
import asyncio
import random
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Any
from io import BytesIO

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from supabase import create_client, Client
import httpx
from google import genai
from google.genai import types
from PIL import Image

# ============================================================
# SECTION 2: CONFIGURATION
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GENAI_API_KEY = os.getenv("GEMINI_KEY", "")
CF_WORKER_URL = os.getenv("CF_WORKER_URL", "https://atlas-bot-proxy.hamza818483.workers.dev").rstrip("/")
HF_SPACE_URL = os.getenv("HF_SPACE_URL", "https://hamzahf1-atlasbot.hf.space").rstrip("/")
BASE_URL = os.getenv("BASE_URL", "https://hamzahf1-atlasbot.hf.space").rstrip("/")

# Fallback providers for Creative (জ্ঞানমূলক/অনুধাবনমূলক) generation when Gemini is exhausted
GROQ_KEYS = [k.strip() for k in os.getenv("GROQ_KEY", "").split(",") if k.strip()]
GROQ_MODEL = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
OPENROUTER_KEYS = [k.strip() for k in os.getenv("OPENROUTER_KEY", "").split(",") if k.strip()]
OPENROUTER_QWEN_MODEL = os.getenv("OPENROUTER_QWEN_MODEL", "qwen/qwen2.5-vl-72b-instruct:free")

SUPABASE_URL = "https://wbdyjpjbczfunyhhmtry.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndiZHlqcGpiY3pmdW55aGhtdHJ5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA2OTI5ODAsImV4cCI6MjA5NjI2ODk4MH0.0WR1sgVsl_1XWZfSd0Pwoe6Uxp-2GMTksfseMn5aWjg"

# Backup Supabase (optional mirror — silently skipped if not set)
SUPABASE_BACKUP_URL = os.getenv("SUPABASE_BACKUP_URL", "").rstrip("/")
SUPABASE_BACKUP_KEY = os.getenv("SUPABASE_BACKUP_KEY", "")

SEC_PER_QUESTION = 30
NEGATIVE_MARK = 0.50
FREE_NEW_EXAM_LIMIT = 2
PERMITTED_NEW_EXAM_LIMIT = 20
NEW_PRACTICE_COUNT = 15
CHROMIUM_PATH = os.getenv("CHROMIUM_PATH", "/usr/bin/chromium")

PROMPT_DISPLAY_NAMES = {
    "prompt_1": "🩺 Medical Standard MCQ",
    "prompt_2": "✅ সত্য-মিথ্যার প্রশ্ন",
    "prompt_3": "🔥 কঠিন প্রশ্ন",
    "prompt_mixed": "🎲 Mixed",
}

try:
    BD_TZ = timezone(timedelta(hours=6))
except Exception:
    BD_TZ = datetime.now().astimezone().tzinfo

# ============================================================
# SECTION 3: SUPABASE CLIENT
# ============================================================
supabase: Client = None
supabase_backup: Client = None

def get_supabase() -> Client:
    global supabase
    if supabase is None:
        try:
            supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
            print("✅ Supabase client initialized (Exam Server)")
        except Exception as e:
            print(f"❌ Supabase init failed: {e}")
            raise
    return supabase

def get_supabase_backup() -> Optional[Client]:
    global supabase_backup
    if not SUPABASE_BACKUP_URL or not SUPABASE_BACKUP_KEY:
        return None
    if supabase_backup is None:
        try:
            supabase_backup = create_client(SUPABASE_BACKUP_URL, SUPABASE_BACKUP_KEY)
            print("✅ Supabase BACKUP client initialized (Exam Server)")
        except Exception as e:
            print(f"⚠️ Supabase backup init failed: {e}")
            return None
    return supabase_backup

def _mirror_insert(table: str, row: Dict) -> None:
    """Best-effort mirror to backup DB. Never raises."""
    try:
        bk = get_supabase_backup()
        if bk:
            bk.table(table).insert(row).execute()
    except Exception as e:
        print(f"Backup mirror({table}) skipped: {e}")

# ============================================================
# SECTION 4: GEMINI SETUP
# ============================================================
_exam_genai_client: Optional[genai.Client] = None
GEMINI_KEYS = [k.strip() for k in os.getenv("GEMINI_KEY", "").split(",") if k.strip()]
_exam_key_idx = 0

def setup_gemini():
    global _exam_genai_client
    if GENAI_API_KEY:
        first_key = GENAI_API_KEY.split(",")[0].strip()
        _exam_genai_client = genai.Client(api_key=first_key)
        print(f"✅ Gemini API configured (Exam Server) key_len={len(first_key)}")
    else:
        print("⚠️ GENAI_API_KEY not set! (Exam Server)")

def _rotate_exam_key():
    """Switch the exam Gemini client to the next key (round-robin)."""
    global _exam_genai_client, _exam_key_idx
    if not GEMINI_KEYS:
        return
    _exam_key_idx = (_exam_key_idx + 1) % len(GEMINI_KEYS)
    try:
        _exam_genai_client = genai.Client(api_key=GEMINI_KEYS[_exam_key_idx])
        print(f"🔄 Exam Gemini key rotated -> #{_exam_key_idx+1}")
    except Exception as e:
        print(f"rotate exam key failed: {e}")

def _parse_new_exam_json(response_text: str) -> List[Dict]:
    """Clean + parse + validate MCQ JSON from Gemini for New Exam."""
    txt = (response_text or "").strip()
    for tag in ['```json', '```']:
        if txt.startswith(tag):
            txt = txt[len(tag):]
    if txt.endswith('```'):
        txt = txt[:-3]
    txt = txt.strip()
    try:
        mcqs = json.loads(txt)
    except Exception as e:
        print(f"[_parse_new_exam_json] JSON parse failed: {e}, input[:200]={txt[:200]}")
        return []
    valid = []
    for mcq in mcqs if isinstance(mcqs, list) else []:
        if all(k in mcq for k in ['question', 'options', 'answer']):
            if len(mcq['options']) >= 4:
                mcq['options'] = mcq['options'][:4]
            if isinstance(mcq['answer'], str):
                mcq['answer'] = {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(mcq['answer'].upper(), 0)
            if isinstance(mcq['answer'], int) and 0 <= mcq['answer'] <= 3:
                valid.append(mcq)
    return valid

def _gen_new_exam_mcqs(img: "Image.Image", min_count: int = 10) -> List[Dict]:
    """Generate New Exam MCQs with all-key rotation + one retry if too few.
    Returns [] only if every key/attempt failed."""
    global _exam_genai_client
    if _exam_genai_client is None:
        setup_gemini()
    if _exam_genai_client is None:
        return []
    tries = max(1, len(GEMINI_KEYS))
    best: List[Dict] = []
    for attempt in range(tries):
        try:
            resp = _exam_genai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[PROMPT_NEW_EXAM, img],
                config=types.GenerateContentConfig(
                    temperature=0.7, top_p=0.95, top_k=40,
                    max_output_tokens=8192,
                    thinking_config=types.ThinkingConfig(thinking_budget=1024),
                )
            )
            mcqs = _parse_new_exam_json(resp.text if resp else "")
            if len(mcqs) > len(best):
                best = mcqs
            if len(best) >= min_count:
                return best
            # too few -> retry once on same key with stronger instruction
            resp2 = _exam_genai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[PROMPT_NEW_EXAM + "\n\n🔴 অবশ্যই কমপক্ষে ১৫টি ভিন্ন MCQ বানাও। JSON array তে ১৫+ object থাকতেই হবে।", img],
                config=types.GenerateContentConfig(
                    temperature=0.8, top_p=0.95, top_k=40, max_output_tokens=8192,
                    thinking_config=types.ThinkingConfig(thinking_budget=1024),
                )
            )
            mcqs2 = _parse_new_exam_json(resp2.text if resp2 else "")
            if len(mcqs2) > len(best):
                best = mcqs2
            if len(best) >= min_count:
                return best
        except Exception as e:
            print(f"New exam gen attempt {attempt+1} failed: {e}")
            _rotate_exam_key()
    return best

# ============================================================
# SECTION 5: IN-MEMORY EXAM STORE + REHYDRATE
# ============================================================
exam_store: Dict[str, Dict] = {}

def store_exam(quiz_id: str, mcqs: List[Dict], topic: str = "", page: int = 1,
               tag: str = "", image_file_id: str = "", is_new_gen: bool = False,
               src_cache_id: str = None, chat_id: int = None, message_id: int = None,
               prompt_type: str = "prompt_1") -> str:
    exam_store[quiz_id] = {
        "mcqs": mcqs, "topic": topic, "page": page, "tag": tag,
        "image_file_id": image_file_id, "is_new_gen": is_new_gen,
        "regen_count": 0, "src_cache_id": src_cache_id or quiz_id,
        "chat_id": chat_id, "message_id": message_id,
        "prompt_type": prompt_type,
        "created_at": datetime.now(BD_TZ).isoformat(),
    }
    print(f"📦 Exam stored: {quiz_id} ({len(mcqs)} questions)")
    return quiz_id

def _get_exam(cache_id: str) -> Optional[Dict]:
    if cache_id in exam_store:
        return exam_store[cache_id]
    try:
        client = get_supabase()
        result = client.table('mcqs').select('*').eq('quiz_id', cache_id).execute()
        if result.data and len(result.data) > 0:
            row = result.data[0]
            mcqs = json.loads(row['mcqs']) if isinstance(row['mcqs'], str) else row['mcqs']
            store_exam(
                quiz_id=cache_id, mcqs=mcqs,
                topic=PROMPT_DISPLAY_NAMES.get(row.get('prompt_type', 'prompt_1'), 'ATLAS Special MCQ'),
                page=1, tag="",
                image_file_id=row.get('image_file_id', ''),
                is_new_gen=False, src_cache_id=cache_id,
                chat_id=row.get('chat_id'),
                message_id=row.get('message_id'),
                prompt_type=row.get('prompt_type', 'prompt_1')
            )
            return exam_store[cache_id]
    except Exception as e:
        print(f"Exam rehydrate failed: {e}")
    return None

# ============================================================
# SECTION 6: DATABASE HELPERS
# ============================================================

def get_user_data(user_id: int) -> Optional[Dict]:
    try:
        client = get_supabase()
        result = client.table('users').select('*').eq('user_id', user_id).execute()
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        print(f"get_user_data error: {e}")
        return None

def check_new_exam_limit(user_id: int) -> tuple:
    if not user_id or user_id == 0:
        return True, 0, FREE_NEW_EXAM_LIMIT, False
    user = get_user_data(user_id)
    if not user:
        return True, 0, FREE_NEW_EXAM_LIMIT, False
    is_perm = user.get('is_permitted', False)
    used = user.get('new_exam_count', 0) or 0
    limit = PERMITTED_NEW_EXAM_LIMIT if is_perm else FREE_NEW_EXAM_LIMIT
    last_reset = user.get('last_new_exam_reset', '')
    today = datetime.now(BD_TZ).strftime('%Y-%m-%d')
    if last_reset != today:
        try:
            client = get_supabase()
            client.table('users').update({'new_exam_count': 0, 'last_new_exam_reset': today}).eq('user_id', user_id).execute()
            used = 0
        except Exception as e:
            print(f"[check_new_exam_limit] daily reset failed for user {user_id}: {e}")
    return used < limit, used, limit, is_perm

def increment_new_exam_count(user_id: int) -> int:
    if not user_id or user_id == 0:
        return 0
    try:
        user = get_user_data(user_id)
        if user:
            new_count = (user.get('new_exam_count', 0) or 0) + 1
            client = get_supabase()
            client.table('users').update({
                'new_exam_count': new_count,
                'last_new_exam_reset': datetime.now(BD_TZ).strftime('%Y-%m-%d')
            }).eq('user_id', user_id).execute()
            return new_count
        return 0
    except Exception as e:
        print(f"increment_new_exam_count error: {e}")
        return 0

def save_result_to_db(user_id: int, cache_id: str, user_name: str, topic: str,
                      page: int, total: int, correct: int, wrong: int,
                      skipped: int, time_taken: int) -> bool:
    try:
        neg = wrong * NEGATIVE_MARK
        final_score = correct - neg
        row = {
            'user_id': user_id, 'quiz_id': cache_id,
            'quiz_name': topic or f'Exam_{cache_id[:6]}',
            'total': total, 'correct': correct, 'wrong': wrong,
            'skipped': skipped, 'time_taken': time_taken,
            'mark': final_score, 'negative_mark': neg,
            'created_at': datetime.now(BD_TZ).isoformat()
        }
        client = get_supabase()
        client.table('results').insert(row).execute()
        _mirror_insert('results', row)
        return True
    except Exception as e:
        print(f"save_result error: {e}")
        return False

def save_bookmark_to_db(user_id: int, cache_id: str, question_index: int,
                        question_data: Dict, topic: str = '', page: int = 0) -> tuple:
    try:
        now = datetime.now(BD_TZ)
        row = {
            'user_id': user_id, 'cache_id': cache_id,
            'question_index': question_index,
            'question_data': json.dumps(question_data, ensure_ascii=False),
            'topic': topic, 'page': page,
            'created_at': int(now.timestamp())
        }
        client = get_supabase()
        try:
            client.table('bookmarks').insert(row).execute()
        except Exception as e1:
            if "22P02" in str(e1) or "invalid input syntax" in str(e1):
                row['created_at'] = now.isoformat()
                client.table('bookmarks').insert(row).execute()
            else:
                raise e1
        _mirror_insert('bookmarks', row)
        return True, ""
    except Exception as e:
        err = str(e)
        print(f"save_bookmark error: {err}")
        if "row-level security" in err.lower() or "rls" in err.lower() or "policy" in err.lower() or "42501" in err:
            return False, "RLS_BLOCKED"
        return False, err

def delete_bookmark_from_db(user_id: int, cache_id: str, question_index: int) -> bool:
    try:
        client = get_supabase()
        client.table('bookmarks').delete()\
            .eq('user_id', user_id).eq('cache_id', cache_id)\
            .eq('question_index', question_index).execute()
        return True
    except Exception as e:
        print(f"delete_bookmark error: {e}")
        return False

# ============================================================
# SECTION 7: TELEGRAM API HELPERS
# ============================================================

async def _tg_file_bytes(file_id: str) -> Optional[bytes]:
    print(f"[tg-file] start, file_id={file_id[:20]}..., BOT_TOKEN_set={bool(BOT_TOKEN)}, CF_WORKER_URL={CF_WORKER_URL}")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{CF_WORKER_URL}/tg-file", params={"file_id": file_id}, headers={"X-Bot-Token": BOT_TOKEN})
            print(f"[tg-file] CF Worker /tg-file status={r.status_code}, content_len={len(r.content) if r.content else 0}")
            if r.status_code == 200 and r.content:
                return r.content
    except Exception as e:
        print(f"[tg-file] CF Worker fetch EXCEPTION: {type(e).__name__}: {e}")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            file_resp = await client.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                params={"file_id": file_id}
            )
            print(f"[tg-file] getFile status={file_resp.status_code}")
            file_data = file_resp.json()
            print(f"[tg-file] getFile response ok={file_data.get('ok')}, full={file_data if not file_data.get('ok') else '(ok)'}")
            if file_data.get('ok'):
                file_path = file_data['result']['file_path']
                img_resp = await client.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}")
                print(f"[tg-file] file download status={img_resp.status_code}, content_len={len(img_resp.content) if img_resp.content else 0}")
                if img_resp.status_code == 200:
                    return img_resp.content
    except Exception as e:
        print(f"[tg-file] Direct Telegram fetch EXCEPTION: {type(e).__name__}: {e}")
    print(f"[tg-file] FAILED — returning None for file_id={file_id[:20]}...")
    return None

async def _tg_send_message(chat_id: int, text: str, reply_to: int = None,
                           parse_mode: str = None) -> Optional[Dict]:
    if not chat_id:
        return None
    payload = {"chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
        payload["allow_sending_without_reply"] = True
    if parse_mode:
        payload["parse_mode"] = parse_mode
    url = f"{CF_WORKER_URL}/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(url, json=payload)
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        print(f"_tg_send_message error: {e}")
    return None

async def _send_web_challenge_comparison(receiver_id: int, sender_id: int, cache_id: str,
                                          r_correct: int, r_wrong: int, r_total: int, r_time: int):
    try:
        client = get_supabase()
        sr = client.table('results').select('*').eq('user_id', sender_id).eq('quiz_id', cache_id).order('created_at', desc=True).limit(1).execute()
        if not sr.data:
            return
        s = sr.data[0]
        s_correct, s_wrong, s_total = s.get('correct', 0), s.get('wrong', 0), s.get('total', 0)
        s_mark, s_time = s.get('mark', 0), s.get('time_taken', 0)
        r_neg = r_wrong * NEGATIVE_MARK
        r_mark = r_correct - r_neg
        try:
            si = client.table('users').select('first_name').eq('user_id', sender_id).limit(1).execute()
            sender_name = si.data[0]['first_name'] if si.data else f"User#{sender_id}"
        except Exception as e:
            print(f"[challenge] sender name lookup failed for {sender_id}: {e}")
            sender_name = f"User#{sender_id}"
        try:
            ri = client.table('users').select('first_name').eq('user_id', receiver_id).limit(1).execute()
            recv_name = ri.data[0]['first_name'] if ri.data else f"User#{receiver_id}"
        except Exception as e:
            print(f"[challenge] receiver name lookup failed for {receiver_id}: {e}")
            recv_name = f"User#{receiver_id}"
        s_pct = round(s_correct / s_total * 100) if s_total else 0
        r_pct = round(r_correct / r_total * 100) if r_total else 0
        if r_mark > s_mark:
            verdict = f"🏆 {recv_name} জিতেছে!"
        elif s_mark > r_mark:
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
        await _tg_send_message(receiver_id, comp, parse_mode="HTML")
        await _tg_send_message(sender_id, comp, parse_mode="HTML")
    except Exception as e:
        print(f"Web challenge comparison error: {e}")

# ============================================================
# SECTION 8: AYATS & MOTIVATION
# ============================================================
_AYATS = [
    '🌙 "فَإِنَّ مَعَ الْعُسْرِ يُسْرًا"\n"নিশ্চয়ই কষ্টের সাথেই স্বস্তি আছে।"\n[সূরা আশ-শারহ ৯৪:৫]',
    '🌙 "لَا يُكَلِّفُ اللَّهُ نَفْسًا إِلَّا وُسْعَهَا"\n"আল্লাহ কাউকে তার সাধ্যের বাইরে বোঝা দেন না।"\n[সূরা বাকারা ২:২৮৬]',
    '🌙 "إِنَّ اللَّهَ مَعَ الصَّابِرِينَ"\n"নিশ্চয়ই আল্লাহ ধৈর্যশীলদের সাথে আছেন।"\n[সূরা বাকারা ২:১৫৩]',
    '🌙 "وَمَن يَتَوَكَّلْ عَلَى اللَّهِ فَهُوَ حَسْبُهُ"\n"যে আল্লাহর উপর ভরসা করে, তার জন্য তিনিই যথেষ্ট।"\n[সূরা তালাক ৬৫:৩]',
    '🌙 "وَقُل رَّبِّ زِدْنِي عِلْمًا"\n"হে আমার রব! আমার জ্ঞান বৃদ্ধি করে দিন।"\n[সূরা ত্বহা ২০:১১৪]',
    '🌙 "وَأَن لَّيْسَ لِلْإِنسَانِ إِلَّا مَا سَعَىٰ"\n"মানুষ তার চেষ্টার ফল ছাড়া কিছুই পায় না।"\n[সূরা নাজম ৫৩:৩৯]',
    '🌙 "إِن يَنصُرْكُمُ اللَّهُ فَلَا غَالِبَ لَكُمْ"\n"আল্লাহ যদি সাহায্য করেন, কেউ পরাজিত করতে পারবে না।"\n[সূরা আলে ইমরান ৩:১৬০]',
    '🌙 "لَا تَقْنَطُوا مِن رَّحْمَةِ اللَّهِ"\n"আল্লাহর রহমত থেকে নিরাশ হয়ো না।"\n[সূরা যুমার ৩৯:৫৩]',
    '🌙 "أَلَا بِذِكْرِ اللَّهِ تَطْمَئِنُّ الْقُلُوبُ"\n"আল্লাহর স্মরণেই হৃদয় প্রশান্ত হয়।"\n[সূরা রাদ ১৩:২৮]',
    '🌙 "إِنَّ اللَّهَ يُحِبُّ الْمُحْسِنِينَ"\n"নিশ্চয়ই আল্লাহ সৎকর্মশীলদের ভালোবাসেন।"\n[সূরা বাকারা ২:১৯৫]',
]

def _pick_feedback(correct: int, total: int) -> tuple:
    pct = (correct / total * 100) if total else 0
    if pct >= 80:
        msg = random.choice(['🏆 অসাধারণ! অনেক ভালো করেছো!', '🌟 দারুণ! তুমি সত্যিই প্রস্তুত!', '💪 বাহ! চমৎকার ফলাফল!'])
    elif pct >= 60:
        msg = random.choice(['✅ মোটামুটি ভালো! চেষ্টা চালিয়ে যাও!', '👍 ভালো হয়েছে! আরেকটু উন্নতি করতে পারবে!'])
    elif pct >= 40:
        msg = random.choice(['📚 আরো পড়তে হবে! হাল ছেড়ো না!', '💭 ঠিক আছে, নিয়মিত চর্চা করো!'])
    else:
        msg = random.choice(['💪 পড়া হয়নি! আবার পড়ে practice করো!', '🌱 শুরুটা কঠিনই হয়! লেগে থাকো!'])
    return msg, random.choice(_AYATS)

# ============================================================
# SECTION 9: FASTAPI APP
# ============================================================
app = FastAPI(title="ATLAS Exam Server", version="3.0")

# ============================================================
# SECTION 10: PROMPTS
# ============================================================
PROMPT_NEW_EXAM = """MCQ TYPE: Standard Easy
-সর্বনিম্ন ১৫ টি Mcq বানাতে হবে
-হাইলাইটেড টেক্সট priority পাবে
-টপিকের নাম/হেডলাইন/পেইজ থেকে MCQ না
-প্রশ্ন: ছোট, ১-২ লাইন
-অপশন: ৪টি, তথ্য দ্বারা পরিপূর্ণ, হ্যাঁ/না টাইপ না
-উত্তর: 0-3 index
-ব্যাখ্যা: Bengali, max 200 char
-Image এ যে ভাষায় content আছে সেই ভাষাতেই MCQ বানাবে
-JSON only: [{"question":"...","options":["A) ...","B) ...","C) ...","D) ..."],"answer":0,"explanation":"..."}]"""

# ── Creative (Srijonshil) prompts ──
PROMPT_KNOWLEDGE = """তুমি একজন অভিজ্ঞ বাংলাদেশি শিক্ষক। নিচের Image এর তথ্য থেকে "জ্ঞানমূলক প্রশ্ন" তৈরি করো (বাংলাদেশের সৃজনশীল পদ্ধতি অনুযায়ী)।

জ্ঞানমূলক প্রশ্নের শর্ত:
- সংজ্ঞা, নাম, সূত্র, সাল/তারিখ, আবিষ্কারক, উপাদান, শ্রেণিবিভাগ, বৈশিষ্ট্য, ধাপের নাম থেকে প্রশ্ন
- প্রশ্ন এক লাইনে, উত্তর এক লাইনে (সরাসরি তথ্যভিত্তিক)
- শুধুমাত্র Image-এর তথ্য ব্যবহার করবে, নিজে থেকে কিছু বানাবে না

কঠোর নিয়ম:
- যতগুলো সম্ভব জ্ঞানমূলক প্রশ্ন বানাও যেন কোনো গুরুত্বপূর্ণ তথ্য মিস না হয় (সর্বনিম্ন ৫, সর্বোচ্চ ৩০)
- Quality > Quantity, হাবিজাবি/আন্দাজী প্রশ্ন একদম নিষেধ
- তথ্য Image-এ না থাকলে প্রশ্ন বানাবে না

যদি Image-এ এত কম তথ্য থাকে যে ২টি অর্থপূর্ণ জ্ঞানমূলক প্রশ্নও বানানো যায় না, তবে JSON এ "error" key দাও:
{"error":"কেন পারছ না তা বাংলায় ব্যাখ্যা করো (কোন তথ্য নেই/পড়া যাচ্ছে না ইত্যাদি)"}

অন্যথায় JSON only:
{"items":[{"question":"...","answer":"..."}]}"""

PROMPT_COMPREHENSION = """তুমি একজন অভিজ্ঞ বাংলাদেশি শিক্ষক। নিচের Image এর তথ্য থেকে "অনুধাবনমূলক প্রশ্ন" তৈরি করো (বাংলাদেশের সৃজনশীল পদ্ধতি অনুযায়ী)।

অনুধাবনমূলক প্রশ্নের শর্ত (নিচের যেকোনো সম্পর্ক থাকতে হবে):
- কারণ → ফলাফল, গঠন → কাজ, প্রক্রিয়া → ফল, বৈশিষ্ট্য → গুরুত্ব, ঘটনা → প্রভাব, তুলনা, ব্যাখ্যাযোগ্য ধারণা

উত্তরের গঠন (২-৫ লাইন):
- ১ম লাইন: জ্ঞানমূলক (সংজ্ঞা/পরিচিতি)
- এরপর ১ লাইন গ্যাপ রেখে ২-৪ লাইন অনুধাবন/ব্যাখ্যা
- উত্তরে "\\n\\n" দিয়ে জ্ঞানমূলক অংশ ও ব্যাখ্যা আলাদা করবে

কঠোর নিয়ম:
- শুধুমাত্র Image-এর তথ্য (source-only ১০০%), নিজে থেকে কিছু বানাবে না
- যতগুলো সম্ভব অনুধাবনমূলক প্রশ্ন (সর্বনিম্ন ৫, সর্বোচ্চ ২৫), Quality > Quantity

যদি Image-এ সম্পর্ক/ব্যাখ্যার সুযোগসহ যথেষ্ট তথ্য না থাকে (২টি অর্থপূর্ণ প্রশ্নও নয়), তবে:
{"error":"কেন পারছ না বাংলায় ব্যাখ্যা করো"}

অন্যথায় JSON only:
{"items":[{"question":"...","answer":"...\\n\\n..."}]}"""

# ── Fallback prompts: guaranteed output (used only if primary attempt fails) ──
PROMPT_KNOWLEDGE_FALLBACK = """তুমি একজন অভিজ্ঞ বাংলাদেশি শিক্ষক। নিচের Image দেখে কমপক্ষে ৩টি "জ্ঞানমূলক প্রশ্ন" বানাও।
- Image-এ যা-ই থাকুক (টেক্সট, ছবি, ডায়াগ্রাম, সংখ্যা, শব্দ) তা থেকেই প্রশ্ন বানানোর চেষ্টা করো
- প্রশ্ন ও উত্তর ছোট ও সরাসরি রাখো
- একদম কিছু না থাকলেও Image-এ দৃশ্যমান বিষয়/বস্তু/শব্দ নিয়ে general knowledge প্রশ্ন বানাও
JSON only: {"items":[{"question":"...","answer":"..."}]}"""

PROMPT_COMPREHENSION_FALLBACK = """তুমি একজন অভিজ্ঞ বাংলাদেশি শিক্ষক। নিচের Image দেখে কমপক্ষে ৩টি "অনুধাবনমূলক প্রশ্ন" বানাও।
- Image-এ যা-ই থাকুক, তার সাথে সম্পর্কিত একটি ব্যাখ্যামূলক প্রশ্ন-উত্তর বানাও
- উত্তর ২-৩ লাইনে, প্রথম লাইনে সংজ্ঞা/পরিচিতি, পরের লাইনে ব্যাখ্যা ("\\n\\n" দিয়ে আলাদা)
- একদম কিছু না থাকলেও Image-এর বিষয়বস্তু নিয়ে general অনুধাবন প্রশ্ন বানাও
JSON only: {"items":[{"question":"...","answer":"...\\n\\n..."}]}"""

# ============================================================
# SECTION 11: API ROUTES
# ============================================================

@app.get("/health")
async def health():
    return PlainTextResponse("OK")

@app.get("/exam/{cache_id}", response_class=HTMLResponse)
async def serve_exam(cache_id: str, uid: int = 0, name: str = "", challenger: int = 0):
    data = _get_exam(cache_id)
    if not data:
        return HTMLResponse(_not_found_html(), status_code=404)
    user_name = (name or "").strip()
    return HTMLResponse(generate_exam_html(cache_id, data, uid, user_name, challenger))

@app.get("/api/exam/{cache_id}")
async def api_exam(cache_id: str):
    data = _get_exam(cache_id)
    if not data:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {
        "mcqs": data["mcqs"],
        "topic": data.get("topic", "ATLAS Exam"),
        "page": data.get("page", 1),
        "tag": data.get("tag", ""),
        "image_file_id": data.get("image_file_id", ""),
        "is_new_gen": data.get("is_new_gen", False),
        "chat_id": data.get("chat_id"),
        "message_id": data.get("message_id"),
        "prompt_type": data.get("prompt_type", "prompt_1"),
    }

@app.get("/api/tg-image/{file_id}")
async def api_tg_image(file_id: str):
    raw = await _tg_file_bytes(file_id)
    if not raw:
        return PlainTextResponse("image unavailable", status_code=404)
    ct = "image/jpeg"
    if raw[:8].startswith(b"\x89PNG"):
        ct = "image/png"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        ct = "image/webp"
    return Response(content=raw, media_type=ct, headers={"Cache-Control": "public, max-age=3600"})

@app.post("/api/exam/result")
async def api_result(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad_json"}, status_code=400)
    cache_id = body.get("cache_id", "")
    user_id = int(body.get("user_id", 0) or 0)
    user_name = (body.get("user_name") or "Student").strip()[:40]
    correct = int(body.get("correct", 0))
    wrong = int(body.get("wrong", 0))
    skipped = int(body.get("skipped", 0))
    time_taken = int(body.get("time_taken", 0))
    challenger_id = int(body.get("challenger_id", 0) or 0)
    total = correct + wrong + skipped
    data = _get_exam(cache_id)
    topic = data.get("topic", "ATLAS Exam") if data else "ATLAS Exam"
    page = data.get("page", 1) if data else 1
    try:
        save_result_to_db(user_id, cache_id, user_name, topic, page, total, correct, wrong, skipped, time_taken)
    except Exception as e:
        print(f"Save result warning: {e}")
    if challenger_id and user_id and challenger_id != user_id:
        asyncio.create_task(_send_web_challenge_comparison(
            user_id, challenger_id, cache_id, correct, wrong, total, time_taken))
    motivation, ayat = _pick_feedback(correct, total)
    return {"motivation": motivation, "ayat": ayat}

@app.post("/api/bookmark")
async def api_bookmark_add(request: Request):
    try:
        body = await request.json()
        user_id = int(body.get("user_id", 0) or 0)
        cache_id = body.get("cache_id", "")
        question_index = body.get("question_index", 0)
        question_data = body.get("question_data", {})
        topic = body.get("topic", "")
        page = body.get("page", 0)
        if not user_id:
            print(f"[bookmark] WARN: user_id=0, skipping save")
            return {"success": False, "message": "user_id missing"}
        ok, err = save_bookmark_to_db(user_id, cache_id, question_index, question_data, topic, page)
        print(f"[bookmark] save user={user_id} cache={cache_id[:8]} qi={question_index} ok={ok} err={err}")
        if not ok and err == "RLS_BLOCKED":
            return {"success": False, "message": "RLS blocked — Supabase SQL Editor এ গিয়ে রান করুন: ALTER TABLE bookmarks DISABLE ROW LEVEL SECURITY;"}
        return {"success": ok, "message": err if not ok else ""}
    except Exception as e:
        print(f"[bookmark] ERROR: {e}")
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)

@app.delete("/api/bookmark")
async def api_bookmark_del(request: Request):
    try:
        body = await request.json()
        user_id = int(body.get("user_id", 0) or 0)
        cache_id = body.get("cache_id", "")
        question_index = body.get("question_index", 0)
        ok = delete_bookmark_from_db(user_id, cache_id, question_index)
        return {"success": ok}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)

@app.post("/api/new-exam")
async def api_new_exam(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad_json"}, status_code=400)
    cache_id = body.get("cache_id", "")
    user_id = int(body.get("user_id", 0) or 0)
    allowed, used, limit, is_perm = check_new_exam_limit(user_id)
    if not allowed:
        return JSONResponse({
            "ok": False, "error": "limit_reached",
            "message": f"❌ আজকের New Exam লিমিট শেষ ({used}/{limit})। আগামীকাল আবার চেষ্টা করুন।"
        })
    src = _get_exam(cache_id)
    if not src:
        return JSONResponse({"ok": False, "error": "not_found", "message": "Exam পাওয়া যায়নি।"})
    src_id = src.get("src_cache_id", cache_id)
    src_entry = _get_exam(src_id) or src
    regen_count = src_entry.get("regen_count", 0)
    if regen_count >= 3:
        return JSONResponse({
            "ok": False, "error": "page_limit_reached",
            "message": "❌ এই Page-এর জন্য New Exam সর্বোচ্চ ৩ বার নেওয়া হয়েছে।"
        })
    file_id = src.get("image_file_id", "")
    if not file_id:
        return JSONResponse({"ok": False, "error": "no_image", "message": "এই এক্সামে কোনো source image নেই।"})
    img_bytes = await _tg_file_bytes(file_id)
    if not img_bytes:
        return JSONResponse({"ok": False, "error": "image_fail", "message": "ছবি লোড করা যায়নি।"})
    if _exam_genai_client is None:
        setup_gemini()
    if _exam_genai_client is None:
        return JSONResponse({"ok": False, "error": "no_key", "message": "Gemini API key সেট নেই।"})
    try:
        img = Image.open(BytesIO(img_bytes))
        valid_mcqs = _gen_new_exam_mcqs(img, min_count=10)
        if len(valid_mcqs) < 5:
            return JSONResponse({"ok": False, "error": "empty", "message": "যথেষ্ট প্রশ্ন পাওয়া যায়নি। একটু পরে আবার চেষ্টা করুন।"})
        new_mcqs = valid_mcqs[:NEW_PRACTICE_COUNT]
    except Exception as e:
        print(f"Gemini generation error: {e}")
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": "gen_fail", "message": "প্রশ্ন তৈরি ব্যর্থ হয়েছে। একটু পরে আবার চেষ্টা করুন।"})
    new_id = uuid.uuid4().hex[:16]
    try:
        client = get_supabase()
        row = {
            'quiz_id': new_id, 'user_id': user_id,
            'mcqs': json.dumps(new_mcqs, ensure_ascii=False),
            'source_type': 'web_exam',
            'prompt_type': src.get('prompt_type', 'prompt_1'),
            'image_file_id': file_id,
            'chat_id': src.get('chat_id'),
            'message_id': src.get('message_id'),
            'created_at': datetime.now(BD_TZ).isoformat()
        }
        client.table('mcqs').insert(row).execute()
        _mirror_insert('mcqs', row)
    except Exception as e:
        print(f"Save new exam error: {e}")
    store_exam(
        quiz_id=new_id, mcqs=new_mcqs,
        topic=src.get("topic", ""), page=src.get("page", 1),
        tag=src.get("tag", ""), image_file_id=file_id,
        is_new_gen=True, src_cache_id=src.get("src_cache_id", cache_id),
        chat_id=src.get("chat_id"), message_id=src.get("message_id"),
        prompt_type=src.get("prompt_type", "prompt_1")
    )
    if src_id in exam_store:
        exam_store[src_id]["regen_count"] = regen_count + 1
    increment_new_exam_count(user_id)
    return {"ok": True, "new_cache_id": new_id, "count": len(new_mcqs)}

@app.get("/api/leaderboard/{cache_id}")
async def api_leaderboard(cache_id: str):
    return {"disabled": True, "data": []}

@app.post("/api/solve-pdf")
async def api_solve_pdf(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad_json"}, status_code=400)
    cache_id = body.get("cache_id", "")
    answers = body.get("answers", {}) or {}
    data = _get_exam(cache_id)
    if not data:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if cache_id in exam_store:
        exam_store[cache_id]["last_answers"] = answers
    html = generate_solve_pdf_html(data, answers)
    try:
        pdf_bytes = await _render_pdf(html)
    except Exception as e:
        print(f"PDF render error: {e}")
        traceback.print_exc()
        return JSONResponse({"ok": False, "message": "PDF তৈরি ব্যর্থ হয়েছে।"}, status_code=500)
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    return {"ok": True, "pdf_b64": b64, "filename": f"ATLAS_Solve_{cache_id[:8]}.pdf"}

@app.post("/api/save-answers")
async def api_save_answers(request: Request):
    """Store answers server-side so /api/solve-pdf-direct can render instantly via a plain link."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False}, status_code=400)
    cache_id = body.get("cache_id", "")
    answers = body.get("answers", {}) or {}
    if cache_id in exam_store:
        exam_store[cache_id]["last_answers"] = answers
        asyncio.ensure_future(_precache_solve_pdf(cache_id))
        return {"ok": True}
    return JSONResponse({"ok": False})

async def _precache_solve_pdf(cache_id: str):
    """Pre-render Solve PDF in background so it's instant when user clicks."""
    try:
        data = _get_exam(cache_id)
        if not data:
            return
        answers = data.get("last_answers", {}) or {}
        html = generate_solve_pdf_html(data, answers)
        pdf_bytes = await _render_pdf(html)
        if cache_id in exam_store:
            exam_store[cache_id]["cached_solve_pdf"] = pdf_bytes
            print(f"[solve-pdf] pre-cached for {cache_id[:8]} ({len(pdf_bytes)} bytes)")
    except Exception as e:
        print(f"[solve-pdf] pre-cache error: {e}")

@app.get("/api/solve-pdf-direct/{cache_id}")
async def api_solve_pdf_direct(cache_id: str):
    """Instant PDF link (like Premium PDF) — serves pre-cached PDF when available."""
    data = _get_exam(cache_id)
    if not data:
        return JSONResponse({"ok": False, "message": "Exam পাওয়া যায়নি।"}, status_code=404)
    cached = data.get("cached_solve_pdf")
    if cached:
        print(f"[solve-pdf] serving cached for {cache_id[:8]}")
        return Response(
            content=cached,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="ATLAS_Solve_{cache_id[:8]}.pdf"'}
        )
    answers = data.get("last_answers", {}) or {}
    html = generate_solve_pdf_html(data, answers)
    try:
        pdf_bytes = await _render_pdf(html)
    except Exception as e:
        print(f"Solve PDF direct render error: {e}")
        traceback.print_exc()
        return JSONResponse({"ok": False, "message": "PDF তৈরি ব্যর্থ হয়েছে।"}, status_code=500)
    if cache_id in exam_store:
        exam_store[cache_id]["cached_solve_pdf"] = pdf_bytes
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="ATLAS_Solve_{cache_id[:8]}.pdf"'}
    )

# ============================================================
# SECTION 11.1: Back to Source API
# ============================================================
@app.post("/api/back-to-source")
async def api_back_to_source(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)
    cache_id = body.get("cache_id", "")
    user_id = int(body.get("user_id", 0) or 0)
    if not user_id:
        return JSONResponse({"ok": False, "message": "User ID প্রয়োজন।"})
    data = _get_exam(cache_id)
    if not data:
        return JSONResponse({"ok": False, "message": "Source পাওয়া যায়নি।"})
    chat_id = data.get("chat_id") or user_id
    message_id = data.get("message_id")
    if not message_id:
        return JSONResponse({"ok": False, "message": "Pinned message reference নেই।"})
    text = "📌 Back to Source\n\n↑ উপরের message-এ tap করলেই আপনার মূল MCQ source-এ চলে যাবেন।"
    res = await _tg_send_message(chat_id, text, reply_to=message_id)
    if res and res.get("ok"):
        return {"ok": True, "message": "✅ Telegram-এ message পাঠানো হয়েছে।"}
    return JSONResponse({"ok": False, "message": "Telegram message পাঠানো যায়নি।"})

# ============================================================
# SECTION 11.2: Premium PDF API — POST
# ============================================================
@app.post("/api/premium-pdf")
async def api_premium_pdf_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)
    cache_id = body.get("cache_id", "")
    header_label = body.get("header_label", "")
    return await _do_premium_pdf(cache_id, header_label)

# ============================================================
# SECTION 11.3: Premium PDF API — GET (bot.py)
# ============================================================
@app.get("/api/premium-pdf/{cache_id}")
async def api_premium_pdf_get(cache_id: str):
    try:
        data = _get_exam(cache_id)
        if not data:
            print(f"[premium-pdf] exam not found: {cache_id}")
            return JSONResponse({"ok": False, "message": "Exam পাওয়া যায়নি।"}, status_code=404)
        mcqs = data.get("mcqs", [])
        if not mcqs:
            print(f"[premium-pdf] no mcqs for {cache_id}")
            return JSONResponse({"ok": False, "message": "কোনো MCQ নেই।"})
        header_label = "ATLAS Practice Sheet"
        html = generate_premium_pdf_html(mcqs, header_label)
        print(f"[premium-pdf] html built ({len(html)} chars), rendering PDF...")
        try:
            pdf_bytes = await _render_pdf(html)
        except Exception as e:
            print(f"[premium-pdf] render error: {e}")
            traceback.print_exc()
            return JSONResponse({"ok": False, "message": f"PDF তৈরি ব্যর্থ হয়েছে: {str(e)[:120]}"}, status_code=500)
        print(f"[premium-pdf] success, pdf size={len(pdf_bytes)}")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="ATLAS_Practice_Sheet_{cache_id[:8]}.pdf"'}
        )
    except Exception as e:
        print(f"[premium-pdf] UNHANDLED EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()
        return JSONResponse({"ok": False, "message": f"অপ্রত্যাশিত সমস্যা: {str(e)[:120]}"}, status_code=500)

async def _do_premium_pdf(cache_id: str, header_label: str = "") -> JSONResponse:
    data = _get_exam(cache_id)
    if not data:
        return JSONResponse({"ok": False, "message": "Exam পাওয়া যায়নি।"}, status_code=404)
    mcqs = data.get("mcqs", [])
    if not mcqs:
        return JSONResponse({"ok": False, "message": "কোনো MCQ নেই।"})
    if not header_label:
        header_label = "ATLAS Practice Sheet"
    html = generate_premium_pdf_html(mcqs, header_label)
    try:
        pdf_bytes = await _render_pdf(html)
    except Exception as e:
        print(f"Premium PDF render error: {e}")
        traceback.print_exc()
        return JSONResponse({"ok": False, "message": "PDF তৈরি ব্যর্থ হয়েছে।"}, status_code=500)
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    return JSONResponse({"ok": True, "pdf_b64": b64, "filename": f"ATLAS_Practice_Sheet_{cache_id[:8]}.pdf"})

# ============================================================
# SECTION 11.3B: Bookmark PDF API — POST (bot.py /bm command)
# ============================================================
@app.post("/api/bookmark-pdf")
async def api_bookmark_pdf(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)
    mcqs = body.get("mcqs", [])
    if not mcqs:
        return JSONResponse({"ok": False, "message": "কোনো Bookmark MCQ নেই।"}, status_code=400)
    header_label = body.get("header_label", "ATLAS Bookmark Practice Sheet")
    html = generate_premium_pdf_html(mcqs, header_label)
    try:
        pdf_bytes = await _render_pdf(html)
    except Exception as e:
        print(f"Bookmark PDF render error: {e}")
        traceback.print_exc()
        return JSONResponse({"ok": False, "message": f"PDF তৈরি ব্যর্থ হয়েছে: {str(e)[:120]}"}, status_code=500)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="ATLAS_Bookmark_Sheet.pdf"'}
    )

# ============================================================
# SECTION 11.4: CREATIVE PDF API (জ্ঞানমূলক / অনুধাবনমূলক)
# GET /api/creative-pdf/{cache_id}?ctype=knowledge|comprehension
# Returns raw PDF, or JSON {ok:false, reason:"..."} when data insufficient
# ============================================================
def _b64_data_url(image_bytes: bytes) -> str:
    mime = "image/jpeg"
    if image_bytes[:8].startswith(b"\x89PNG"):
        mime = "image/png"
    elif image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        mime = "image/webp"
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"

async def _call_creative_fallback(prompt: str, img_bytes: bytes) -> Optional[dict]:
    """Groq/OpenRouter fallback when Gemini is exhausted. Returns parsed JSON dict or None."""
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": _b64_data_url(img_bytes)}},
    ]
    chains = [
        ("https://api.groq.com/openai/v1", GROQ_KEYS, GROQ_MODEL, {}),
        ("https://openrouter.ai/api/v1", OPENROUTER_KEYS, OPENROUTER_QWEN_MODEL,
         {"HTTP-Referer": HF_SPACE_URL, "X-Title": "ATLAS MCQ Bot"}),
    ]
    for base_url, keys, model, extra_headers in chains:
        for k in keys:
            try:
                headers = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
                headers.update(extra_headers)
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": content}],
                    "temperature": 0.6, "max_tokens": 8192,
                }
                async with httpx.AsyncClient(timeout=120) as client:
                    r = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
                    if r.status_code != 200:
                        continue
                    txt = r.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    for tag in ('```json', '```'):
                        if txt.startswith(tag):
                            txt = txt[len(tag):]
                    if txt.endswith('```'):
                        txt = txt[:-3]
                    return json.loads(txt.strip())
            except Exception as e:
                print(f"[creative-fallback] {base_url} error: {e}")
                continue
    return None

async def _generate_creative_items(img_bytes: bytes, ctype: str) -> Dict:
    """Returns {'ok':True,'items':[...]} or {'ok':False,'reason':str}.
    Tries primary (strict source-only) prompt first, then a lenient
    fallback prompt so a PDF can (almost) always be produced."""
    if _exam_genai_client is None:
        setup_gemini()
    if _exam_genai_client is None:
        return {"ok": False, "reason": "Gemini API key সেট নেই।"}
    prompt = PROMPT_KNOWLEDGE if ctype == "knowledge" else PROMPT_COMPREHENSION
    fallback_prompt = PROMPT_KNOWLEDGE_FALLBACK if ctype == "knowledge" else PROMPT_COMPREHENSION_FALLBACK

    def _call(p: str):
        img = Image.open(BytesIO(img_bytes))
        resp = _exam_genai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[p, img],
            config=types.GenerateContentConfig(
                temperature=0.6, top_p=0.95, top_k=40,
                max_output_tokens=8192,
                thinking_config=types.ThinkingConfig(thinking_budget=1024),
            )
        )
        txt = (resp.text or "").strip()
        for tag in ['```json', '```']:
            if txt.startswith(tag):
                txt = txt[len(tag):]
        if txt.endswith('```'):
            txt = txt[:-3]
        return json.loads(txt.strip())

    last_reason = "তথ্য অপর্যাপ্ত।"
    for p in (prompt, fallback_prompt):
        try:
            obj = _call(p)
            if isinstance(obj, dict) and obj.get("error"):
                last_reason = str(obj.get("error"))[:300]
                continue
            items = obj.get("items", []) if isinstance(obj, dict) else (obj if isinstance(obj, list) else [])
            clean = [it for it in items if it.get("question") and it.get("answer")]
            if len(clean) >= 2:
                return {"ok": True, "items": clean}
            if len(clean) >= 1:
                last_reason = "শুধুমাত্র সীমিত প্রশ্ন পাওয়া গেছে।"
        except json.JSONDecodeError:
            last_reason = "AI সঠিক ফরম্যাটে উত্তর দেয়নি।"
        except Exception as e:
            print(f"creative gen error: {e}")
            traceback.print_exc()
            last_reason = f"প্রশ্ন তৈরিতে সমস্যা: {str(e)[:80]}"

    # Gemini exhausted/failed both prompts — try Groq/OpenRouter fallback
    print("[creative-pdf] Gemini failed, trying Groq/OpenRouter fallback...")
    for p in (prompt, fallback_prompt):
        try:
            obj = await _call_creative_fallback(p, img_bytes)
            if obj is None:
                continue
            if isinstance(obj, dict) and obj.get("error"):
                last_reason = str(obj.get("error"))[:300]
                continue
            items = obj.get("items", []) if isinstance(obj, dict) else (obj if isinstance(obj, list) else [])
            clean = [it for it in items if it.get("question") and it.get("answer")]
            if len(clean) >= 2:
                print(f"[creative-pdf] fallback succeeded with {len(clean)} items")
                return {"ok": True, "items": clean}
            if len(clean) >= 1:
                last_reason = "শুধুমাত্র সীমিত প্রশ্ন পাওয়া গেছে।"
        except Exception as e:
            print(f"[creative-pdf] fallback error: {e}")
            continue

    return {"ok": False, "reason": last_reason}

@app.get("/api/creative-pdf/{cache_id}")
async def api_creative_pdf(cache_id: str, ctype: str = "knowledge"):
    try:
        if ctype not in ("knowledge", "comprehension"):
            ctype = "knowledge"
        data = _get_exam(cache_id)
        if not data:
            print(f"[creative-pdf] exam not found: {cache_id}")
            return JSONResponse({"ok": False, "reason": "Source পাওয়া যায়নি।"}, status_code=404)
        file_id = data.get("image_file_id", "")
        if not file_id:
            print(f"[creative-pdf] no image_file_id for {cache_id}")
            return JSONResponse({"ok": False, "reason": "এই সেটে কোনো source image নেই (শুধু Image থেকে সৃজনশীল প্রশ্ন বানানো যায়)।"})
        img_bytes = await _tg_file_bytes(file_id)
        if not img_bytes:
            print(f"[creative-pdf] _tg_file_bytes failed for file_id={file_id}")
            return JSONResponse({"ok": False, "reason": "ছবি লোড করা যায়নি।"})
        print(f"[creative-pdf] img_bytes OK ({len(img_bytes)} bytes), generating items, ctype={ctype}")
        res = await _generate_creative_items(img_bytes, ctype)
        if not res.get("ok"):
            print(f"[creative-pdf] _generate_creative_items failed: {res.get('reason')}")
            return JSONResponse({"ok": False, "reason": res.get("reason", "তথ্য অপর্যাপ্ত।")})
        print(f"[creative-pdf] got {len(res['items'])} items, building PDF html...")
        header = "জ্ঞানমূলক প্রশ্ন [ATLAS]" if ctype == "knowledge" else "অনুধাবনমূলক প্রশ্ন [ATLAS]"
        html = generate_creative_pdf_html(res["items"], header, ctype)
        try:
            pdf_bytes = await _render_pdf(html)
        except Exception as e:
            print(f"[creative-pdf] PDF render error: {e}")
            traceback.print_exc()
            return JSONResponse({"ok": False, "reason": f"PDF তৈরি ব্যর্থ হয়েছে: {str(e)[:120]}"}, status_code=500)
        fname = ("ATLAS_Gyanmulok_" if ctype == "knowledge" else "ATLAS_Onudhabonmulok_") + cache_id[:8] + ".pdf"
        print(f"[creative-pdf] success, pdf size={len(pdf_bytes)}")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'}
        )
    except Exception as e:
        print(f"[creative-pdf] UNHANDLED EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()
        return JSONResponse({"ok": False, "reason": f"অপ্রত্যাশিত সমস্যা: {str(e)[:120]}"}, status_code=500)

# ============================================================
# SECTION 11.5: PDF RENDERER (Playwright / Chromium)
# ============================================================
async def _render_pdf(html: str) -> bytes:
    print(f"[PDF] _render_pdf called, html_len={len(html)}, CHROMIUM_PATH={CHROMIUM_PATH}")
    try:
        from playwright.async_api import async_playwright
        print("[PDF] playwright import OK")
        flags = ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                 "--single-process", "--headless=new"]
        async with async_playwright() as p:
            print("[PDF] launching chromium...")
            browser = await p.chromium.launch(executable_path=CHROMIUM_PATH, args=flags)
            print("[PDF] chromium launched OK")
            try:
                page = await browser.new_page()
                print("[PDF] new_page OK, setting content...")
                await page.set_content(html, wait_until="networkidle")
                print("[PDF] set_content OK")
                await page.wait_for_timeout(600)
                pdf = await page.pdf(
                    format="A4", print_background=True,
                    margin={"top": "0mm", "bottom": "0mm", "left": "0mm", "right": "0mm"}
                )
                print(f"[PDF] pdf rendered OK, size={len(pdf)} bytes")
                return pdf
            finally:
                await browser.close()
                print("[PDF] browser closed")
    except ImportError as ie:
        print(f"[PDF] playwright ImportError: {ie} — falling back to subprocess Chromium")
        import subprocess, tempfile, os as _os
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode='w', encoding='utf-8') as f:
            f.write(html)
            html_path = f.name
        pdf_path = html_path.replace(".html", ".pdf")
        try:
            print(f"[PDF] running subprocess chromium: {CHROMIUM_PATH}")
            subprocess.run([
                CHROMIUM_PATH, "--headless", "--no-sandbox",
                "--disable-gpu", "--disable-dev-shm-usage",
                f"--print-to-pdf={pdf_path}", html_path
            ], timeout=60, check=True)
            print("[PDF] subprocess chromium done")
            with open(pdf_path, 'rb') as f:
                return f.read()
        except Exception as se:
            print(f"[PDF] subprocess chromium FAILED: {se}")
            traceback.print_exc()
            raise
        finally:
            try: _os.unlink(html_path)
            except OSError: pass
            try: _os.unlink(pdf_path)
            except OSError: pass
    except Exception as e:
        print(f"[PDF] UNEXPECTED ERROR in _render_pdf: {type(e).__name__}: {e}")
        traceback.print_exc()
        raise

# ============================================================
# SECTION 12: HTML GENERATORS
# ============================================================

def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _not_found_html() -> str:
    return """<!DOCTYPE html>
<html lang="bn"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{background:#0A0D1E;color:#E8EAFF;font-family:'Noto Sans Bengali',sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;margin:0;}h2{color:#F87171;}p{color:#7A82C8;}</style>
</head><body><div><h2>⚠️ এক্সাম পাওয়া যায়নি</h2><p>লিংকটি মেয়াদোত্তীর্ণ বা ভুল হতে পারে।</p></div></body></html>"""

def generate_solve_pdf_html(data: Dict, answers: Dict) -> str:
    """ATLAS Solve Sheet — same A4 2-column theme as Premium PDF, with explanations + correct/wrong marking."""
    mcqs = data["mcqs"]
    topic = data.get("topic", "ATLAS Exam")
    labels = ["a", "b", "c", "d"]

    def strip_prefix(opt: str, oi: int) -> str:
        clean = opt
        for pfx in [f"{labels[oi].upper()}) ", f"{labels[oi].upper()})", f"({labels[oi]}) ",
                    f"({labels[oi]})", f"{labels[oi]}) ", f"{labels[oi]})"]:
            if clean.startswith(pfx):
                return clean[len(pfx):].strip()
        return clean

    # Build per-question render data (answered or not, all questions included)
    items = []
    for i, q in enumerate(mcqs):
        correct_idx = q.get("answer", 0)
        if isinstance(correct_idx, str):
            correct_idx = {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(correct_idx.upper(), 0)
        user_answer = answers.get(str(i))
        user_idx = int(user_answer) if (user_answer is not None and user_answer != -1) else -1
        items.append({"q": q, "num": i + 1, "correct_idx": correct_idx, "user_idx": user_idx})

    pages_html = ""
    PER_PAGE = 8  # explanations make cards taller than plain Premium PDF
    total_pages = max(1, (len(items) + PER_PAGE - 1) // PER_PAGE)

    for p_i, page_idx in enumerate(range(0, len(items), PER_PAGE)):
        chunk = items[page_idx:page_idx + PER_PAGE]
        n = len(chunk)
        if n <= 4:
            qfs, ofs, gap = 12.5, 12, "12px"
        elif n <= 6:
            qfs, ofs, gap = 11.5, 11, "10px"
        else:
            qfs, ofs, gap = 10.5, 10, "8px"

        half = (n + 1) // 2
        left_col = chunk[:half]
        right_col = chunk[half:]

        def render_q(it):
            q = it["q"]
            num = it["num"]
            correct_idx = it["correct_idx"]
            user_idx = it["user_idx"]
            q_text = _esc(q.get("question", ""))
            opts = q.get("options", [])[:4]
            opts_html = ""
            for oi, opt in enumerate(opts):
                clean = _esc(strip_prefix(opt, oi))
                cls = ""
                mark = ""
                if oi == correct_idx:
                    cls = "correct"
                    mark = " ✅"
                elif oi == user_idx and user_idx != correct_idx:
                    cls = "wrong"
                    mark = " ❌"
                opts_html += f'<div class="opt-line {cls}">({labels[oi]}) {clean}{mark}</div>'
            exp = q.get("explanation", "")
            exp_html = f'<div class="exp-box"><span class="exp-hd">📋 ব্যাখ্যা:</span> {_esc(exp)}</div>' if exp else ""
            return (f'<div class="q-block" style="margin-bottom:{gap};">'
                    f'<div class="q-text"><span class="q-num">{num}.</span> {q_text}</div>'
                    f'<div class="opts-grid">{opts_html}</div>{exp_html}</div>')

        left_html = "".join(render_q(it) for it in left_col)
        right_html = "".join(render_q(it) for it in right_col)

        pb = 'page-break-after:always;' if (p_i + 1) < total_pages else ''
        pages_html += f'''
        <div class="page" style="{pb}font-size:{qfs}px;">
            <div class="header-bar">📋 ATLAS Solve Sheet — {_esc(topic)}</div>
            <div class="columns">
                <div class="col">{left_html}</div>
                <div class="divider-v"></div>
                <div class="col">{right_html}</div>
            </div>
            <div class="footer">🌐 atlascourses.com · ▶ youtube.com/@atlasprep</div>
        </div>'''

    return f'''<!DOCTYPE html>
<html lang="bn"><head><meta charset="UTF-8">
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Bengali:wght@400;600;700&family=Inter:wght@400;600;700&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{font-family:'Noto Sans Bengali','Inter',sans-serif;color:#111;background:#fff;line-height:1.5;}}
.page{{padding:10mm 9mm;min-height:297mm;display:flex;flex-direction:column;}}
.header-bar{{background:linear-gradient(135deg,#d4f1f9,#cfe9ff);border:1.5px solid #9cc8db;border-radius:8px;text-align:center;padding:9px 0;margin-bottom:16px;font-weight:700;font-size:15px;color:#0a3d5c;font-family:'Inter','Noto Sans Bengali',sans-serif;letter-spacing:.3px;}}
.columns{{display:flex;gap:0;flex:1;}}
.col{{flex:1;min-width:0;padding:0 10px;}}
.divider-v{{width:1.5px;background:linear-gradient(#bbb,#ddd,#bbb);margin:0 2px;}}
.q-block{{page-break-inside:avoid;}}
.q-text{{font-weight:600;line-height:1.5;margin-bottom:4px;color:#15233a;}}
.q-num{{font-weight:800;color:#0a3d5c;}}
.opts-grid{{padding-left:8px;}}
.opt-line{{font-size:0.92em;line-height:1.45;white-space:normal;color:#222;padding:1px 4px;border-radius:4px;}}
.opt-line.correct{{background:rgba(34,212,122,0.18);color:#0a6b3a;font-weight:700;}}
.opt-line.wrong{{background:rgba(248,113,113,0.18);color:#a3201a;font-weight:700;}}
.exp-box{{margin-top:3px;margin-left:8px;padding:4px 8px;background:#f0f4fb;border-left:2.5px solid #5A5FE0;border-radius:0 6px 6px 0;font-size:0.85em;color:#333;}}
.exp-hd{{color:#5A5FE0;font-weight:700;}}
.footer{{text-align:center;font-size:9.5px;color:#777;margin-top:10px;padding-top:5px;border-top:1px solid #ddd;}}
@page{{size:A4;margin:0;}}
</style></head><body>{pages_html}</body></html>'''

def generate_premium_pdf_html(mcqs: List[Dict], header_label: str = "ATLAS Practice Sheet") -> str:
    """ATLAS Practice Sheet — 2-column, full A4 page fill (auto density per page)."""
    labels = ["a", "b", "c", "d"]

    def strip_prefix(opt: str, oi: int) -> str:
        clean = opt
        for pfx in [f"{labels[oi].upper()}) ", f"{labels[oi].upper()})", f"({labels[oi]}) ",
                    f"({labels[oi]})", f"{labels[oi]}) ", f"{labels[oi]})"]:
            if clean.startswith(pfx):
                return clean[len(pfx):].strip()
        return clean

    pages_html = ""
    PER_PAGE = 10
    total_pages = max(1, (len(mcqs) + PER_PAGE - 1) // PER_PAGE)

    for p_i, page_idx in enumerate(range(0, len(mcqs), PER_PAGE)):
        chunk = mcqs[page_idx:page_idx + PER_PAGE]
        n = len(chunk)
        # Auto density: fewer questions on a page -> bigger fonts/gaps to fill A4
        if n <= 4:
            qfs, ofs, gap, qgap = 15.5, 14.5, "26px", "20px"
        elif n <= 6:
            qfs, ofs, gap, qgap = 14, 13, "20px", "16px"
        elif n <= 8:
            qfs, ofs, gap, qgap = 12.5, 12, "15px", "13px"
        else:
            qfs, ofs, gap, qgap = 11.5, 11, "11px", "11px"

        half = (n + 1) // 2
        left_col = chunk[:half]
        right_col = chunk[half:]

        def render_q(q, num):
            q_text = _esc(q.get("question", ""))
            opts = q.get("options", [])[:4]
            opts_html = ""
            for oi, opt in enumerate(opts):
                clean = _esc(strip_prefix(opt, oi))
                opts_html += f'<div class="opt-line">({labels[oi]}) {clean}</div>'
            return (f'<div class="q-block" style="margin-bottom:{qgap};">'
                    f'<div class="q-text"><span class="q-num">{num}.</span> {q_text}</div>'
                    f'<div class="opts-grid" style="gap:4px {gap};">{opts_html}</div></div>')

        left_html = "".join(render_q(q, page_idx + i + 1) for i, q in enumerate(left_col))
        right_html = "".join(render_q(q, page_idx + half + i + 1) for i, q in enumerate(right_col))

        ans_cells_q = "".join(f'<td>{page_idx + i + 1}</td>' for i in range(n))
        ans_cells_a = ""
        for q in chunk:
            idx = q.get("answer", 0)
            if isinstance(idx, str):
                idx = {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(idx.upper(), 0)
            ans_cells_a += f'<td>{"abcd"[idx] if 0 <= idx <= 3 else "a"}</td>'

        pb = 'page-break-after:always;' if (p_i + 1) < total_pages else ''
        pages_html += f'''
        <div class="page" style="{pb}font-size:{qfs}px;">
            <div class="header-bar">{_esc(header_label)}</div>
            <div class="columns">
                <div class="col">{left_html}</div>
                <div class="divider-v"></div>
                <div class="col">{right_html}</div>
            </div>
            <div class="answer-section">
                <div class="answer-title">সঠিক উত্তর যাচাই কর :)</div>
                <table class="answer-table">
                    <tr class="th-row"><th>প্রশ্ন</th>{ans_cells_q}</tr>
                    <tr class="td-row"><th>উত্তর</th>{ans_cells_a}</tr>
                </table>
            </div>
            <div class="footer">🌐 atlascourses.com · ▶ youtube.com/@atlasprep</div>
        </div>'''

    return f'''<!DOCTYPE html>
<html lang="bn"><head><meta charset="UTF-8">
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Bengali:wght@400;600;700&family=Inter:wght@400;600;700&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{font-family:'Noto Sans Bengali','Inter',sans-serif;color:#111;background:#fff;line-height:1.55;}}
.page{{padding:10mm 9mm;min-height:297mm;display:flex;flex-direction:column;}}
.header-bar{{background:linear-gradient(135deg,#d4f1f9,#cfe9ff);border:1.5px solid #9cc8db;border-radius:8px;text-align:center;padding:9px 0;margin-bottom:16px;font-weight:700;font-size:16px;color:#0a3d5c;font-family:'Inter','Noto Sans Bengali',sans-serif;letter-spacing:.3px;}}
.columns{{display:flex;gap:0;flex:1;}}
.col{{flex:1;min-width:0;padding:0 10px;}}
.divider-v{{width:1.5px;background:linear-gradient(#bbb,#ddd,#bbb);margin:0 2px;}}
.q-block{{page-break-inside:avoid;}}
.q-text{{font-weight:600;line-height:1.55;margin-bottom:5px;color:#15233a;}}
.q-num{{font-weight:800;color:#0a3d5c;}}
.opts-grid{{display:grid;grid-template-columns:1fr 1fr;padding-left:10px;}}
.opt-line{{font-size:0.92em;line-height:1.5;white-space:normal;color:#222;}}
.answer-section{{margin-top:auto;padding-top:14px;page-break-inside:avoid;}}
.answer-title{{text-align:center;font-weight:700;font-size:14px;margin-bottom:7px;color:#0a3d5c;}}
.answer-table{{width:100%;border-collapse:collapse;border:1.5px solid #333;}}
.answer-table th,.answer-table td{{border:1px solid #555;text-align:center;padding:6px 4px;font-size:12px;}}
.answer-table .th-row th,.answer-table .td-row th{{background:#e8eef5;font-weight:700;}}
.answer-table .td-row td{{font-weight:800;font-family:'Inter',sans-serif;color:#0a3d5c;}}
.footer{{text-align:center;font-size:9.5px;color:#777;margin-top:10px;padding-top:5px;border-top:1px solid #ddd;}}
@page{{size:A4;margin:0;}}
</style></head><body>{pages_html}</body></html>'''


def generate_creative_pdf_html(items: List[Dict], header_label: str, ctype: str) -> str:
    """জ্ঞানমূলক / অনুধাবনমূলক — A4, 2-column, premium color, answer in shadow box."""
    # Color theme per type
    if ctype == "knowledge":
        accent, accent2, ans_bg, ans_border = "#1565c0", "#42a5f5", "#e8f3ff", "#90caf9"
    else:
        accent, accent2, ans_bg, ans_border = "#6a1b9a", "#ab47bc", "#f6ebfb", "#ce93d8"

    def render_item(it, num):
        q = _esc(it.get("question", ""))
        a = _esc(it.get("answer", "")).replace("\n", "<br>")
        return (f'<div class="qa-block">'
                f'<div class="qa-q"><span class="qa-num">{num}.</span> {q}</div>'
                f'<div class="qa-a"><span class="qa-a-lbl">উত্তর:</span> {a}</div>'
                f'</div>')

    n = len(items)
    half = (n + 1) // 2
    left = "".join(render_item(it, i + 1) for i, it in enumerate(items[:half]))
    right = "".join(render_item(it, half + i + 1) for i, it in enumerate(items[half:]))

    # Single flowing layout across pages using CSS columns (auto-balances + fills page)
    all_items = "".join(render_item(it, i + 1) for i, it in enumerate(items))

    return f'''<!DOCTYPE html>
<html lang="bn"><head><meta charset="UTF-8">
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Bengali:wght@400;600;700;800&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{font-family:'Noto Sans Bengali',sans-serif;color:#1a1a1a;background:#fff;line-height:1.6;font-size:12.5px;}}
.header-bar{{background:linear-gradient(135deg,{accent},{accent2});color:#fff;text-align:center;padding:12px 0;font-weight:800;font-size:18px;letter-spacing:.4px;margin-bottom:14px;border-radius:0 0 10px 10px;box-shadow:0 3px 8px rgba(0,0,0,0.18);}}
.content{{column-count:2;column-gap:18px;column-rule:1.5px solid #ddd;padding:0 10mm;}}
.qa-block{{break-inside:avoid;margin-bottom:14px;}}
.qa-q{{font-weight:700;color:#10243a;margin-bottom:6px;line-height:1.55;}}
.qa-num{{font-weight:800;color:{accent};}}
.qa-a{{background:{ans_bg};border:1px solid {ans_border};border-left:4px solid {accent};border-radius:8px;padding:9px 11px;font-size:0.95em;color:#243; box-shadow:0 2px 5px rgba(0,0,0,0.08);line-height:1.6;}}
.qa-a-lbl{{color:{accent};font-weight:800;margin-right:3px;}}
.footer{{text-align:center;font-size:9.5px;color:#888;margin-top:10px;padding:8px 0 0;border-top:1px solid #e3e3e3;}}
@page{{size:A4;margin:8mm 0 10mm;}}
</style></head><body>
<div class="header-bar">{_esc(header_label)}</div>
<div class="content">{all_items}</div>
<div class="footer">🌐 atlascourses.com · ▶ youtube.com/@atlasprep · ATLAS Premium Sheet</div>
</body></html>'''


# ============================================================
# SECTION 12.1: EXAM HTML (preserved from v2.4 — unchanged behavior)
# ============================================================
def generate_exam_html(cache_id: str, data: Dict, uid: int = 0, name: str = "", challenger: int = 0) -> str:
    mcqs = data["mcqs"]
    total = len(mcqs)
    topic = data.get("topic", "ATLAS Exam")
    tag = data.get("tag", "")
    page = data.get("page", 1)
    image_file_id = data.get("image_file_id", "")
    is_new_gen = data.get("is_new_gen", False)
    chat_id = data.get("chat_id", "")
    message_id = data.get("message_id", "")
    prompt_type = data.get("prompt_type", "prompt_1")
    prompt_display = PROMPT_DISPLAY_NAMES.get(prompt_type, "📋 ATLAS Special MCQ")

    cfg = {
        "cacheId": cache_id, "userId": uid, "total": total, "topic": topic,
        "tag": tag, "page": page, "imageFileId": image_file_id, "isNewGen": is_new_gen,
        "mcqs": mcqs, "negPerWrong": NEGATIVE_MARK, "secPerQ": SEC_PER_QUESTION,
        "hasSource": bool(chat_id and message_id), "promptDisplay": prompt_display,
        "hfSpaceUrl": HF_SPACE_URL,
        "challengerId": challenger,
        "websiteUrl": "https://atlascourses.com",
        "youtubeUrl": "https://www.youtube.com/@atlasprep",
        "whatsappUrl": "https://wa.me/8801999681290",
        "groupsUrl": "https://t.me/MediAtlas/4221",
    }
    cfg_json = json.dumps(cfg, ensure_ascii=True)
    user_name_safe = (name or "").replace("'", "\\'").replace('"', '\\"')
    return _EXAM_HTML_TEMPLATE.replace("__CFG_JSON__", cfg_json).replace("__USER_NAME_SAFE__", user_name_safe)

_EXAM_HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>ATLAS Special Exam</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Bengali:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
:root{
    --bg:#0A0D1E;--card-bg:#12162E;--card-hover:#1A1E3A;
    --border:#2A2E4A;--divider:#1E2240;
    --accent:#5A5FE0;--accent-light:rgba(90,95,224,0.12);--accent-glow:rgba(90,95,224,0.4);
    --atlas-bg:#0D1033;--atlas-card:#151A3E;--atlas-border:#3A3F6E;
    --atlas-text:#A8AEFF;--atlas-glow:rgba(90,95,224,0.2);--atlas-glow-strong:rgba(90,95,224,0.6);
    --text:#E8EAFF;--text-secondary:#7A82C8;--text-tertiary:#4A5080;
    --success:#22D47A;--success-light:rgba(34,212,122,0.15);--success-glow:rgba(34,212,122,0.3);
    --error:#F87171;--error-light:rgba(248,113,113,0.15);--warning:#FBBF24;
    --option-bg:#1A1E3A;--option-border:#2A2E4A;
    --btn-bg:#1A1E3A;--overlay:rgba(0,0,0,0.82);
    --shadow-sm:0 1px 6px rgba(0,0,0,0.3);--shadow-md:0 4px 20px rgba(0,0,0,0.5);
    --radius:12px;--radius-sm:8px;--radius-full:999px;
}
body.light-theme{
    --bg:#F5F6FB;--card-bg:#FFFFFF;--card-hover:#EFF1FA;
    --border:#D8DCEF;--divider:#E5E8F5;
    --accent:#4A4FD0;--accent-light:rgba(74,79,208,0.10);--accent-glow:rgba(74,79,208,0.28);
    --atlas-bg:#FFFFFF;--atlas-card:#F0F1FB;--atlas-border:#C9CDEE;
    --atlas-text:#3A3FB5;--atlas-glow:rgba(74,79,208,0.12);--atlas-glow-strong:rgba(74,79,208,0.35);
    --text:#1A1D38;--text-secondary:#5A5F85;--text-tertiary:#9094B8;
    --success:#16A360;--success-light:rgba(22,163,96,0.12);--success-glow:rgba(22,163,96,0.25);
    --error:#DC2626;--error-light:rgba(220,38,38,0.10);--warning:#D97706;
    --option-bg:#F7F8FD;--option-border:#D8DCEF;
    --btn-bg:#F0F1FB;--overlay:rgba(0,0,0,0.55);
    --shadow-sm:0 1px 6px rgba(0,0,0,0.06);--shadow-md:0 4px 20px rgba(0,0,0,0.10);
}
.theme-toggle{position:absolute;right:14px;top:10px;width:34px;height:34px;border-radius:50%;background:var(--option-bg);border:1.5px solid var(--border);color:var(--text);font-size:16px;display:flex;align-items:center;justify-content:center;cursor:pointer;z-index:60;transition:.2s;}
.theme-toggle:active{transform:scale(0.9);}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Noto Sans Bengali','Inter',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-tap-highlight-color:transparent;-webkit-user-select:none;user-select:none;}
.mode{display:none;}.mode.active{display:block;animation:fadeIn .25s ease;}
@keyframes fadeIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
.wrap{max-width:600px;margin:0 auto;padding:16px 14px 90px;}
.header{position:sticky;top:0;z-index:50;background:var(--atlas-bg);border-bottom:1px solid var(--atlas-border);padding:10px 16px;display:flex;align-items:center;gap:8px;min-height:52px;}
.brand{font-size:18px;font-weight:900;background:linear-gradient(135deg,#818CF8,#C4B5FD,#F472B6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;flex-shrink:0;}
.header-sub{font-size:11px;color:var(--text-secondary);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.exam-hdr{display:none;position:sticky;top:52px;z-index:49;background:var(--atlas-bg);border-bottom:1px solid var(--atlas-border);padding:7px 14px 6px;gap:8px;flex-wrap:wrap;align-items:center;}
.exam-hdr.visible{display:flex;}
.hdr-timer{font-size:19px;font-weight:900;letter-spacing:1px;font-variant-numeric:tabular-nums;color:var(--success);}
.hdr-timer.warn{color:var(--warning);}.hdr-timer.danger{color:var(--error);animation:blink .5s infinite;}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.hdr-meta{font-size:10.5px;color:var(--text-secondary);flex:1;text-align:center;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.hdr-prog{font-size:11px;color:var(--text-secondary);font-weight:700;}
.hdr-bar-wrap{width:100%;height:3px;background:rgba(255,255,255,0.08);border-radius:2px;overflow:hidden;}
.hdr-bar-fill{height:100%;background:linear-gradient(90deg,var(--success),var(--accent));transition:width .5s;}
.hero{text-align:center;padding:20px 14px 14px;}
.hero-img{display:none;}
.hero-src-img{width:100%;max-height:300px;object-fit:contain;background:var(--card-bg);border:1px solid var(--border);border-radius:var(--radius);margin-bottom:14px;display:block;}
.hero-title{font-size:18px;font-weight:800;color:var(--accent);margin-bottom:4px;}
.hero-prompt{font-size:13px;color:var(--text-secondary);margin-bottom:12px;font-weight:600;}
.hero-stats{display:flex;justify-content:center;gap:8px;flex-wrap:wrap;}
.stat-box{background:var(--card-bg);border:1px solid var(--border);border-radius:var(--radius-sm);padding:12px 16px;text-align:center;min-width:76px;}
.stat-val{font-size:18px;font-weight:800;color:var(--accent);}.stat-lbl{font-size:10px;color:var(--text-secondary);margin-top:2px;}
.info-card{background:var(--card-bg);border:1px solid var(--border);border-radius:var(--radius);padding:14px;margin:12px 0;}
.info-row{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--divider);font-size:13px;}
.info-row:last-child{border-bottom:none;}.info-lbl{color:var(--text-secondary);}.info-val{font-weight:600;}
.timer2-card{background:var(--card-bg);border:1.5px solid var(--border);border-radius:var(--radius);padding:16px;margin:12px 0;}
.timer2-title{font-size:13px;font-weight:700;color:var(--text);margin-bottom:12px;text-align:center;}
.timer2-opts{display:flex;gap:10px;}
.timer2-btn{flex:1;padding:11px 0;border-radius:var(--radius-sm);border:1.5px solid var(--border);background:var(--option-bg);color:var(--text-secondary);font-size:14px;font-weight:700;cursor:pointer;transition:all .2s;font-family:'Noto Sans Bengali','Inter',sans-serif;position:relative;overflow:hidden;}
.timer2-btn::after{content:'';position:absolute;inset:0;background:rgba(255,255,255,0.06);opacity:0;transition:.2s;}
.timer2-btn:active::after{opacity:1;}
.timer2-btn.selected-yes{background:linear-gradient(135deg,#7c3aed,#5A5FE0);border-color:#7c3aed;color:#fff;box-shadow:0 0 14px rgba(124,58,237,0.45);transform:scale(1.03);}
.timer2-btn.selected-no{background:linear-gradient(135deg,#22D47A,#16a34a);border-color:#22D47A;color:#fff;box-shadow:0 0 14px rgba(34,212,122,0.35);transform:scale(1.03);}
.timer2-note{font-size:11px;color:var(--error);margin-top:8px;text-align:center;display:none;}
.timer2-note.show{display:block;}
.btn-start{width:100%;padding:15px;background:linear-gradient(135deg,var(--accent),#7c3aed);color:#fff;border:none;border-radius:var(--radius-sm);font-size:15px;font-weight:700;cursor:pointer;margin-top:10px;font-family:'Noto Sans Bengali','Inter',sans-serif;position:relative;overflow:hidden;box-shadow:0 4px 20px var(--accent-glow);transition:transform .15s,box-shadow .15s;}
.btn-start:active{transform:scale(0.97);}
.btn-start::before{content:'';position:absolute;top:-50%;left:-60%;width:40%;height:200%;background:rgba(255,255,255,0.15);transform:skewX(-20deg);animation:shimmer 2.5s infinite;}
@keyframes shimmer{0%{left:-60%}100%{left:120%}}
.hall-area{padding:12px 14px 100px;max-width:600px;margin:0 auto;}
.mcq-card{background:var(--card-bg);border:1px solid var(--border);border-radius:var(--radius);padding:16px;margin-bottom:12px;box-shadow:var(--shadow-sm);transition:border-color .3s;}
.mcq-card.done{border-color:var(--atlas-border);opacity:.9;}
.mcq-card.current{border-color:var(--accent);box-shadow:0 0 12px var(--accent-glow);}
.q-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;}
.q-num{font-size:11px;color:var(--accent);font-weight:700;background:var(--accent-light);padding:3px 9px;border-radius:var(--radius-full);}
.bm-btn{background:none;border:1px solid var(--border);font-size:14px;cursor:pointer;padding:4px 7px;border-radius:6px;transition:.2s;color:var(--text-secondary);}
.bm-btn.active{color:var(--warning);border-color:var(--warning);}
.q-text{font-size:14px;font-weight:600;margin-bottom:10px;line-height:1.7;}
.opt{display:flex;align-items:center;gap:10px;padding:10px 12px;margin-bottom:6px;background:var(--option-bg);border:1.5px solid var(--option-border);border-radius:var(--radius-sm);cursor:pointer;transition:all .18s;font-size:13px;}
.opt>span:nth-child(2){flex:1;}
.opt-icon{flex-shrink:0;margin-left:auto;}
.opt:hover:not(.locked){border-color:var(--accent);background:rgba(90,95,224,0.1);}
.opt.locked{cursor:default;pointer-events:none;}
.opt.correct-r{background:var(--success-light)!important;border-color:var(--success)!important;}
.opt.wrong-r{background:var(--error-light)!important;border-color:var(--error)!important;}
.opt.dim{opacity:.32;}
.opt-radio{width:20px;height:20px;border-radius:50%;border:2px solid var(--text-tertiary);display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:11px;font-weight:700;transition:all .18s;}
.opt.correct-r .opt-radio{border-color:var(--success);background:var(--success);color:#fff;}
.opt.wrong-r .opt-radio{border-color:var(--error);background:var(--error);color:#fff;}
.submit-fixed{position:fixed;bottom:0;left:0;right:0;width:100%;background:linear-gradient(135deg,#22D47A,#16a34a);color:#fff;border:none;padding:16px;font-size:16px;font-weight:700;cursor:pointer;z-index:200;box-shadow:0 -4px 24px rgba(34,212,122,0.5);font-family:'Noto Sans Bengali','Inter',sans-serif;display:none;overflow:hidden;}
.submit-fixed::before{content:'';position:absolute;top:-50%;left:-60%;width:40%;height:200%;background:rgba(255,255,255,0.12);transform:skewX(-20deg);animation:shimmer 3s infinite;}
.submit-fixed:active{transform:scale(0.98);}
.confirm-overlay{position:fixed;inset:0;background:var(--overlay);z-index:300;display:none;align-items:center;justify-content:center;padding:20px;}
.confirm-overlay.open{display:flex;animation:fadeIn .2s ease;}
.confirm-popup{background:var(--card-bg);border:1.5px solid var(--accent);border-radius:var(--radius);padding:24px 20px;max-width:380px;width:100%;text-align:center;box-shadow:0 8px 40px rgba(0,0,0,0.7),0 0 30px var(--accent-glow);}
.confirm-icon{font-size:36px;margin-bottom:12px;}
.confirm-title{font-size:17px;font-weight:800;color:var(--text);margin-bottom:8px;}
.confirm-body{font-size:13px;color:var(--text-secondary);margin-bottom:20px;line-height:1.6;}
.confirm-btns{display:flex;gap:10px;}
.confirm-btn{flex:1;padding:12px;border-radius:var(--radius-sm);border:none;font-size:14px;font-weight:700;cursor:pointer;font-family:'Noto Sans Bengali','Inter',sans-serif;transition:all .18s;}
.confirm-btn.yes{background:linear-gradient(135deg,var(--success),#16a34a);color:#fff;box-shadow:0 0 14px rgba(34,212,122,0.35);}
.confirm-btn.no{background:var(--option-bg);color:var(--text-secondary);border:1.5px solid var(--border);}
.confirm-btn:active{transform:scale(0.96);}
.nav-fab{position:fixed;right:0;top:50%;transform:translateY(-50%);width:38px;height:38px;background:var(--accent);border:none;border-radius:10px 0 0 10px;color:#fff;font-size:15px;cursor:pointer;z-index:101;display:none;align-items:center;justify-content:center;box-shadow:0 4px 12px var(--accent-glow);}
.nav-overlay{position:fixed;inset:0;background:var(--overlay);z-index:200;display:none;align-items:flex-end;}
.nav-overlay.open{display:flex;}
.nav-popup{width:100%;max-width:520px;margin:0 auto;background:var(--card-bg);border-radius:var(--radius) var(--radius) 0 0;padding:16px;border:1px solid var(--border);max-height:80vh;overflow-y:auto;}
.nav-title{font-size:14px;font-weight:700;color:var(--accent);margin-bottom:12px;text-align:center;}
.nav-grid{display:grid;grid-template-columns:repeat(8,1fr);gap:5px;}
@media(max-width:480px){.nav-grid{grid-template-columns:repeat(6,1fr);}}
.nav-num{aspect-ratio:1;border-radius:6px;background:var(--option-bg);border:1px solid var(--border);color:var(--text-secondary);font-size:11px;font-weight:600;display:flex;align-items:center;justify-content:center;cursor:pointer;}
.nav-num.ok{background:var(--success);color:#fff;border-color:var(--success);}
.nav-num.bad{background:var(--error);color:#fff;border-color:var(--error);}
.nav-close{display:block;margin:12px auto 0;padding:8px 20px;background:var(--accent);color:#fff;border:none;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;}
.result-score-card{background:linear-gradient(140deg,var(--atlas-bg),var(--atlas-card));border:1.5px solid var(--atlas-border);border-radius:var(--radius);padding:24px 18px;text-align:center;margin-bottom:14px;box-shadow:var(--shadow-md),0 0 24px var(--atlas-glow);}
.result-name{font-size:15px;font-weight:800;color:#fff;margin-bottom:10px;}
.result-big{font-size:52px;font-weight:900;color:var(--atlas-text);text-shadow:0 0 20px var(--atlas-glow-strong);}
.result-denom{font-size:52px;font-weight:900;color:var(--atlas-text);opacity:.75;}
.result-total-lbl{font-size:12px;color:var(--atlas-text);opacity:.6;margin-top:4px;}
.timer2-result{background:rgba(248,113,113,0.12);border:1px solid var(--error);border-radius:var(--radius-sm);padding:10px 14px;margin-top:10px;font-size:13px;color:var(--error);font-weight:600;}
.result-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px;}
.result-stat{background:var(--card-bg);border:1px solid var(--border);border-radius:var(--radius-sm);padding:12px 8px;text-align:center;}
.r-val{font-size:20px;font-weight:800;}.r-lbl{font-size:10px;color:var(--text-secondary);margin-top:2px;}
.c-val{color:var(--success);}.w-val{color:var(--error);}.s-val{color:var(--warning);}
.info-row2{background:var(--card-bg);border:1px solid var(--border);border-radius:var(--radius-sm);padding:11px 14px;margin-bottom:8px;display:flex;justify-content:space-between;font-size:13px;}
.mot-box{background:var(--card-bg);border:1px solid var(--accent-light);border-radius:var(--radius-sm);padding:12px 14px;margin-bottom:14px;}
.mot-grade{font-size:13px;font-weight:700;color:var(--success);margin-bottom:12px;line-height:1.6;}
.mot-divider{height:1px;background:var(--divider);margin:10px 0;}
.ayat-text{font-size:11px;color:var(--text-secondary);line-height:1.6;}
.result-btns{display:flex;flex-direction:column;gap:8px;margin-bottom:14px;}
.result-btn{padding:12px;border-radius:var(--radius-sm);border:1px solid var(--border);background:var(--btn-bg);color:var(--text);font-size:13px;font-weight:700;cursor:pointer;transition:all .2s;font-family:'Noto Sans Bengali','Inter',sans-serif;width:100%;text-align:center;}
.result-btn:hover{border-color:var(--accent);color:var(--accent);}.result-btn:active{opacity:0.8;}
.result-btn.primary{background:var(--accent);color:#fff;border-color:var(--accent);}
.result-btn.danger{background:#c0392b;color:#fff;border-color:#c0392b;}
.result-btn.success{background:var(--success);color:#fff;border-color:var(--success);}
.result-btn.purple{background:#7c3aed;color:#fff;border-color:#7c3aed;}
.divider{height:1px;background:var(--divider);margin:14px 0;}
.section-title{font-size:13px;font-weight:700;color:var(--text-secondary);margin-bottom:10px;}
.filter-bar{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap;}
.filter-btn{padding:6px 14px;border-radius:20px;border:1px solid var(--border);background:var(--card-bg);color:var(--text-secondary);cursor:pointer;font-size:11px;font-weight:600;transition:.2s;}
.filter-btn.active{background:var(--accent);color:#fff;border-color:var(--accent);}
.q-result-badge{font-size:11px;font-weight:700;padding:3px 9px;border-radius:var(--radius-full);margin-bottom:7px;display:inline-block;}
.q-result-badge.correct{background:var(--success-light);color:var(--success);}
.q-result-badge.wrong{background:var(--error-light);color:var(--error);}
.q-result-badge.skip{background:rgba(251,191,36,0.15);color:var(--warning);}
.exp-box{background:var(--card-hover);border-left:3px solid var(--accent);padding:10px 12px;margin-top:8px;border-radius:0 6px 6px 0;font-size:12px;line-height:1.5;}
.exp-hd{color:var(--accent);font-weight:700;font-size:11px;margin-bottom:3px;}
.loading{text-align:center;padding:60px 20px;color:var(--text-secondary);}
.spin{font-size:40px;animation:spin 1s linear infinite;display:block;margin-bottom:14px;}
@keyframes spin{to{transform:rotate(360deg)}}
.toast{position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:var(--card-bg);border:1px solid var(--border);color:var(--text);padding:10px 18px;border-radius:var(--radius-full);font-size:13px;font-weight:600;z-index:400;opacity:0;transition:opacity .3s;white-space:nowrap;pointer-events:none;}
.toast.show{opacity:1;}
</style>
</head>
<body>
<header class="header">
    <div class="brand">ATLAS</div>
    <div class="header-sub" id="headerSub">Special Exam</div>
    <button class="theme-toggle" id="themeToggle" onclick="toggleTheme()">🌙</button>
</header>
<div class="exam-hdr" id="examHdr">
    <span class="hdr-timer" id="hdrTimer">00:00</span>
    <span class="hdr-meta" id="hdrMeta">—</span>
    <span class="hdr-prog" id="hdrProg">0/0 (0%)</span>
    <div class="hdr-bar-wrap"><div class="hdr-bar-fill" id="hdrBarFill" style="width:0%"></div></div>
</div>
<div class="mode active" id="modeLoading">
    <div class="loading"><span class="spin">⏳</span><div>লোড হচ্ছে...</div></div>
</div>
<div class="mode" id="modePreExam">
    <div class="wrap">
        <div class="hero">
            <img id="preSrcImg" class="hero-src-img" src="" alt="" style="display:none;">
            <div class="hero-title" id="preTitle">—</div>
            <div class="hero-prompt" id="prePrompt">—</div>
            <div class="hero-stats">
                <div class="stat-box"><div class="stat-val" id="preQCount">—</div><div class="stat-lbl">📋 প্রশ্ন</div></div>
                <div class="stat-box"><div class="stat-val" id="preTimerDisp">—</div><div class="stat-lbl">⏱️ মোট সময়</div></div>
                <div class="stat-box"><div class="stat-val" id="prePage">—</div><div class="stat-lbl">📄 পেজ</div></div>
            </div>
        </div>
        <div class="info-card">
            <div class="info-row"><span class="info-lbl">Topic</span><span class="info-val" id="preTopic">—</span></div>
            <div class="info-row"><span class="info-lbl">Type</span><span class="info-val" id="preType">ATLAS Special MCQ</span></div>
            <div class="info-row"><span class="info-lbl">Negative</span><span class="info-val">প্রতি ভুলে -0.50</span></div>
            <div class="info-row"><span class="info-lbl">Timer</span><span class="info-val" id="preTimerRule">—</span></div>
        </div>
        <div class="timer2-card">
            <div class="timer2-title">⏱️ আপনি কি সেকেন্ড টাইমার?</div>
            <div class="timer2-opts">
                <button class="timer2-btn" id="t2yes" onclick="selectTimer2(true)">✅ হ্যাঁ</button>
                <button class="timer2-btn selected-no" id="t2no" onclick="selectTimer2(false)">❌ না</button>
            </div>
            <div class="timer2-note" id="t2note">⚠️ সেকেন্ড টাইমার হলে result এ আরও ৩% মার্ক কাটা যাবে!</div>
        </div>
        <button class="btn-start" onclick="startHall()">▶️ এক্সাম শুরু করুন</button>
    </div>
</div>
<div class="mode" id="modeHall">
    <div class="hall-area" id="hallArea"></div>
    <button class="submit-fixed" id="submitBtn" onclick="openConfirm()">📤 Submit করুন</button>
    <button class="nav-fab" id="navFab" onclick="openNav()">📋</button>
</div>
<div class="mode" id="modeResult">
    <div class="wrap" id="resultWrap"></div>
</div>
<div class="confirm-overlay" id="confirmOverlay">
    <div class="confirm-popup">
        <div class="confirm-icon">📋</div>
        <div class="confirm-title">সাবমিট করবেন?</div>
        <div class="confirm-body" id="confirmBody">একবার সাবমিট করলে আর পরিবর্তন করা যাবে না।</div>
        <div class="confirm-btns">
            <button class="confirm-btn no" onclick="closeConfirm()">↩️ Back</button>
            <button class="confirm-btn yes" onclick="doConfirmSubmit()">✅ Confirm</button>
        </div>
    </div>
</div>
<div class="nav-overlay" id="navOverlay" onclick="closeNav()">
    <div class="nav-popup" onclick="event.stopPropagation()">
        <div class="nav-title">📋 প্রশ্ন অগ্রগতি</div>
        <div class="nav-grid" id="navGrid"></div>
        <div style="text-align:center;margin-top:8px;font-size:10px;color:var(--text-secondary)" id="navStats"></div>
        <button class="nav-close" onclick="closeNav()">✕ বন্ধ</button>
    </div>
</div>
<div class="toast" id="toast"></div>
<script>
const CFG = __CFG_JSON__;
const LABELS = ['A','B','C','D'];
let questions = [], origQs = [];
let userAnswers = {}, bookmarks = {};
let submitted = false;
let totalSec = 30, totalLeft = 30, totalTimer = null;
let isSecondTimer = false;
let USER_NAME = '__USER_NAME_SAFE__';
const USER_ID = (CFG && CFG.userId !== undefined && CFG.userId !== null) ? Number(CFG.userId) : 0;

function init() {
    try {
        if (!CFG || !Array.isArray(CFG.mcqs) || CFG.mcqs.length === 0) {
            document.getElementById('modeLoading').innerHTML =
                '<div class="loading"><div style="font-size:40px;margin-bottom:14px">⚠️</div>' +
                '<div>এক্সাম পাওয়া যায়নি বা ফাঁকা।</div>' +
                '<div style="margin-top:10px;font-size:11px">পেজ refresh করুন বা bot-এ ফিরে যান।</div></div>';
            return;
        }
        questions = [...CFG.mcqs];
        origQs = [...CFG.mcqs];
        totalSec = Math.max(30, Math.ceil(CFG.total * CFG.secPerQ));
        totalLeft = totalSec;
        if (!USER_NAME) {
            try { const s = localStorage.getItem('atlas_name'); if (s) USER_NAME = s; } catch(e){}
        }
        document.getElementById('headerSub').textContent = CFG.topic || 'Special Exam';
        document.getElementById('preTitle').textContent = '📝 ' + (CFG.topic || 'ATLAS Special Exam');
        document.getElementById('prePrompt').textContent = CFG.promptDisplay || '';
        document.getElementById('preQCount').textContent = CFG.total;
        document.getElementById('preTopic').textContent = CFG.topic || '—';
        document.getElementById('preType').textContent = CFG.promptDisplay || 'ATLAS Special MCQ';
        document.getElementById('prePage').textContent = 'Page No: ' + (CFG.page || 1);
        document.getElementById('preTimerDisp').textContent = zp(Math.floor(totalSec/60)) + ':' + zp(totalSec%60);
        document.getElementById('preTimerRule').textContent = 'মোট ' + Math.ceil(CFG.total/2) + ' মিনিট • শেষ হলে auto submit';
        if (CFG.imageFileId) {
            const preImg = document.getElementById('preSrcImg');
            if (preImg) {
                preImg.src = CFG.hfSpaceUrl + '/api/tg-image/' + encodeURIComponent(CFG.imageFileId);
                preImg.style.display = 'block';
                preImg.onerror = () => { preImg.style.display = 'none'; };
            }
        }
        setMode('PreExam');
    } catch(err) {
        document.getElementById('modeLoading').innerHTML =
            '<div class="loading"><div style="font-size:40px;margin-bottom:14px">⚠️</div>' +
            '<div>লোড করতে সমস্যা হয়েছে।</div>' +
            '<div style="margin-top:10px;font-size:10px;color:#7A82C8">' + (err.message||'Unknown') + '</div></div>';
    }
}

function selectTimer2(val) {
    isSecondTimer = val;
    const yBtn = document.getElementById('t2yes');
    const nBtn = document.getElementById('t2no');
    const note = document.getElementById('t2note');
    if (val) {
        yBtn.className = 'timer2-btn selected-yes';
        nBtn.className = 'timer2-btn';
        note.className = 'timer2-note show';
    } else {
        yBtn.className = 'timer2-btn';
        nBtn.className = 'timer2-btn selected-no';
        note.className = 'timer2-note';
    }
}

function startHall() {
    if (!USER_NAME) USER_NAME = 'Student';
    userAnswers = {}; bookmarks = {}; submitted = false; totalLeft = totalSec;
    let html = '';
    questions.forEach((q, qi) => {
        const tagPrefix = q._tag ? '['+q._tag+']\n\n' : '';
        html += '<div class="mcq-card'+(qi===0?' current':'')+'" id="qCard'+qi+'">'
            + '<div class="q-head"><span class="q-num">প্রশ্ন '+(qi+1)+'/'+questions.length+'</span>'
            + '<button class="bm-btn" id="bmBtn'+qi+'" onclick="toggleBm('+qi+')">🔖</button></div>'
            + '<div class="q-text">'+(tagPrefix+q.question).replace(/\n/g,'<br>')+'</div>';
        q.options.forEach((opt, oi) => {
            html += '<div class="opt" id="opt'+qi+'_'+oi+'" onclick="pickOpt('+qi+','+oi+')">'
                + '<span class="opt-radio">'+LABELS[oi]+'</span><span style="flex:1">'+opt+'</span></div>';
        });
        html += '</div>';
    });
    document.getElementById('hallArea').innerHTML = html;
    document.getElementById('submitBtn').style.display = 'block';
    document.getElementById('navFab').style.display = 'flex';
    document.getElementById('examHdr').classList.add('visible');
    document.getElementById('hdrMeta').textContent = (CFG.topic||'') + ' • Page ' + (CFG.page||1);
    setMode('Hall');
    window.scrollTo({top:0,behavior:'instant'});
    updateHdrTimer(); clearInterval(totalTimer);
    totalTimer = setInterval(() => {
        totalLeft--; updateHdrTimer();
        if (totalLeft <= 0) { stopAll(); doSubmit(); }
    }, 1000);
    updateHdrProg();
}

function updateHdrTimer() {
    const el = document.getElementById('hdrTimer'); if (!el) return;
    const t = Math.max(0, totalLeft);
    el.textContent = zp(Math.floor(t/60)) + ':' + zp(t%60);
    el.className = 'hdr-timer' + (t<=15?' danger':t<=60?' warn':'');
}

function updateHdrProg() {
    const answered = Object.keys(userAnswers).length;
    const total = questions.length;
    const pct = total ? Math.round(answered/total*100) : 0;
    document.getElementById('hdrProg').textContent = answered+'/'+total+' ('+pct+'%)';
    document.getElementById('hdrBarFill').style.width = pct+'%';
}

function pickOpt(qi, oi) {
    if (submitted || userAnswers[qi] !== undefined) return;
    userAnswers[qi] = oi;
    lockQuestion(qi, oi);
    updateHdrProg();
    setTimeout(() => {
        const nextIdx = findNextUnanswered(qi);
        if (nextIdx !== -1) {
            const nextCard = document.getElementById('qCard' + nextIdx);
            if (nextCard) {
                document.querySelectorAll('.mcq-card.current').forEach(c => c.classList.remove('current'));
                nextCard.classList.add('current');
                nextCard.scrollIntoView({behavior:'smooth', block:'center'});
            }
        }
    }, 100);
}

function findNextUnanswered(fromIdx) {
    for (let i = fromIdx + 1; i < questions.length; i++) {
        if (userAnswers[i] === undefined) return i;
    }
    return -1;
}

function lockQuestion(qi, chosen) {
    const card = document.getElementById('qCard'+qi); if (card) card.classList.add('done');
    questions[qi].options.forEach((_, oi) => {
        const el = document.getElementById('opt'+qi+'_'+oi); if (!el) return;
        el.classList.add('locked');
        if (oi === chosen) {
            el.style.borderColor = 'var(--accent)';
            el.style.background = 'rgba(90,95,224,0.18)';
            if (!el.querySelector('.opt-icon'))
                el.insertAdjacentHTML('beforeend','<span class="opt-icon" style="margin-left:auto;flex-shrink:0;">🔒</span>');
        } else el.classList.add('dim');
    });
}

function toggleBm(qi) {
    if(!USER_ID){showToast('⚠️ বুকমার্ক সেভ করতে Bot থেকে Web Exam খুলুন');return;}
    bookmarks[qi] = !bookmarks[qi];
    const btn = document.getElementById('bmBtn'+qi);
    if (btn) btn.className = 'bm-btn'+(bookmarks[qi]?' active':'');
    const btnR = document.getElementById('bmBtnR'+qi);
    if (btnR) btnR.className = 'bm-btn'+(bookmarks[qi]?' active':'');
    showToast(bookmarks[qi]?'🔖 বুকমার্ক করা হয়েছে':'🔖 বুকমার্ক রিমুভ');
    fetch('/api/bookmark',{
        method: bookmarks[qi]?'POST':'DELETE',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({user_id:USER_ID,cache_id:CFG.cacheId,question_index:qi,question_data:questions[qi],topic:CFG.topic||'',page:CFG.page||0})
    }).then(function(r){return r.json();}).then(function(d){
        if(!d.success){showToast('⚠️ '+(d.message||'বুকমার্ক সেভ ব্যর্থ'));bookmarks[qi]=!bookmarks[qi];
            if(btn)btn.className='bm-btn'+(bookmarks[qi]?' active':'');
            if(btnR)btnR.className='bm-btn'+(bookmarks[qi]?' active':'');
        }
    }).catch(function(){showToast('⚠️ নেটওয়ার্ক ত্রুটি — আবার চেষ্টা করুন');});
}

function openNav() {
    let html='';
    questions.forEach((q,i)=>{
        let cls='nav-num';
        if (userAnswers[i]!==undefined){
            const ci = typeof q.answer==='string'?({'A':0,'B':1,'C':2,'D':3}[q.answer.toUpperCase()]||0):(q.answer||0);
            cls += userAnswers[i]===ci?' ok':' bad';
        }
        html+='<div class="'+cls+'" onclick="goToQ('+i+')">'+(i+1)+'</div>';
    });
    document.getElementById('navGrid').innerHTML=html;
    const ans=Object.keys(userAnswers).length;
    document.getElementById('navStats').textContent='✅ '+ans+' উত্তর | ⬜ '+(questions.length-ans)+' বাকি';
    document.getElementById('navOverlay').classList.add('open');
}
function closeNav(){ document.getElementById('navOverlay').classList.remove('open'); }
function goToQ(qi){
    closeNav();
    const el=document.getElementById('qCard'+qi);
    if(el) el.scrollIntoView({behavior:'smooth',block:'center'});
}

function openConfirm() {
    if (submitted) return;
    const un = questions.filter((_,i)=>userAnswers[i]===undefined).length;
    document.getElementById('confirmBody').textContent =
        un > 0
        ? un+'টি প্রশ্নের উত্তর দেওয়া হয়নি। একবার submit করলে আর পরিবর্তন করা যাবে না।'
        : 'সব প্রশ্নের উত্তর দিয়েছেন। একবার submit করলে আর পরিবর্তন করা যাবে না।';
    document.getElementById('confirmOverlay').classList.add('open');
}
function closeConfirm(){ document.getElementById('confirmOverlay').classList.remove('open'); }

function doConfirmSubmit() {
    closeConfirm();
    stopAll(); doSubmit();
}

function stopAll() { clearInterval(totalTimer); }

function doSubmit() {
    if (submitted) return;
    submitted = true; stopAll();
    document.getElementById('submitBtn').style.display='none';
    document.getElementById('navFab').style.display='none';
    document.getElementById('examHdr').classList.remove('visible');
    const timeTaken = Math.max(0, totalSec - totalLeft);
    let correct=0, wrong=0, skipped=0;
    questions.forEach((q,i)=>{
        const ci = typeof q.answer==='string'?({'A':0,'B':1,'C':2,'D':3}[q.answer.toUpperCase()]||0):(q.answer||0);
        const ua = userAnswers[i];
        if(ua===undefined) skipped++;
        else if(ua===ci) correct++;
        else wrong++;
    });
    const neg = parseFloat((wrong*CFG.negPerWrong).toFixed(2));
    let fin = parseFloat((correct-neg).toFixed(2));
    const pct = questions.length ? Math.round(correct/questions.length*100) : 0;
    const mins = Math.floor(timeTaken/60), secs = timeTaken%60;
    let timer2Deduction = 0;
    let finAfterTimer2 = fin;
    let timer2Neg = 0;
    let timer2Main = correct;
    if (isSecondTimer) {
        timer2Neg = parseFloat((wrong*0.25).toFixed(2));
        timer2Main = parseFloat((correct - timer2Neg).toFixed(2));
        timer2Deduction = parseFloat((questions.length * 0.03).toFixed(2));
        finAfterTimer2 = parseFloat((timer2Main - timer2Deduction).toFixed(2));
    }
    _saveAnswerSnapshot();
    fetch('/api/save-answers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cache_id:CFG.cacheId,answers:lastAnswers})}).catch(()=>{});
    renderResult(correct,wrong,skipped,timeTaken,fin,neg,pct,mins,secs,'','',timer2Deduction,finAfterTimer2);
    fetch('/api/exam/result',{
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({cache_id:CFG.cacheId,user_id:USER_ID,user_name:USER_NAME,correct,wrong,skipped,time_taken:timeTaken,challenger_id:CFG.challengerId||0})
    }).then(r=>r.json()).then(d=>{
        const motBox = document.getElementById('motBox');
        if (motBox && (d.motivation || d.ayat)) {
            let mh = '<div class="mot-grade">'+(d.motivation||'')+'</div><div class="mot-divider"></div>';
            if (d.ayat) mh += '<div class="ayat-text">📖 '+d.ayat+'</div>';
            motBox.innerHTML = mh;
            motBox.style.display = 'block';
        }
    }).catch(()=>{});
}

function renderResult(correct,wrong,skipped,timeTaken,fin,neg,pct,mins,secs,motivation,ayat,timer2Ded,finFinal) {
    let html='';
    html+='<div class="result-score-card">';
    html+='<div class="result-name">📝 '+CFG.topic+'</div>';
    html+='<div><span class="result-big">'+finFinal.toFixed(2)+'</span><span class="result-denom">/'+questions.length+'</span></div>';
    html+='<div class="result-total-lbl">Final Score ('+pct+'%)</div>';
    if (isSecondTimer) {
        html+='<div class="timer2-result">⏱️ আপনি সেকেন্ড টাইমার হওয়ায় ৩% মার্ক কেটে:<br>Final Result: '+finFinal.toFixed(2)+'/'+questions.length+'</div>';
    }
    html+='</div>';
    html+='<div class="result-grid">';
    html+='<div class="result-stat"><div class="r-val c-val">✅ '+correct+'</div><div class="r-lbl">সঠিক</div></div>';
    html+='<div class="result-stat"><div class="r-val w-val">❌ '+wrong+'</div><div class="r-lbl">ভুল</div></div>';
    html+='<div class="result-stat"><div class="r-val s-val">⏭️ '+skipped+'</div><div class="r-lbl">স্কিপ</div></div>';
    html+='</div>';
    if (isSecondTimer) {
        const t2neg = parseFloat((wrong*0.25).toFixed(2));
        html+='<div class="info-row2"><span>📊 Negative</span><span style="color:var(--error)">-'+t2neg.toFixed(2)+' ('+wrong+'×0.25)</span></div>';
        html+='<div class="info-row2"><span>⏱️ 2nd Timer (-3% of '+questions.length+')</span><span style="color:var(--error)">-'+timer2Ded.toFixed(2)+'</span></div>';
    } else {
        html+='<div class="info-row2"><span>📊 Negative</span><span style="color:var(--error)">-'+neg.toFixed(2)+' ('+wrong+'×'+CFG.negPerWrong+')</span></div>';
    }
    html+='<div class="info-row2"><span>🏆 Final Score</span><span style="color:var(--accent);font-weight:700">'+finFinal.toFixed(2)+'/'+questions.length+' ('+pct+'%)</span></div>';
    html+='<div class="info-row2" style="margin-bottom:14px"><span>⏱️ সময়</span><span>'+mins+'m '+secs+'s</span></div>';
    if(motivation||ayat){
        html+='<div class="mot-box" id="motBox"><div class="mot-grade">'+motivation+'</div><div class="mot-divider"></div>';
        if(ayat) html+='<div class="ayat-text">📖 '+ayat+'</div>';
        html+='</div>';
    } else {
        html+='<div class="mot-box" id="motBox" style="display:none"></div>';
    }
    html+='<div class="result-btns">';
    html+='<button class="result-btn primary" onclick="practiceAgain()">🔄 Practice Again</button>';
    html+='<button class="result-btn success" onclick="solvePDF()">📄 Solve PDF</button>';
    html+='<button class="result-btn primary" id="newExamBtn" onclick="startNewExam()">🆕 New Exam</button>';
    if(CFG.hasSource) html+='<button class="result-btn" id="backSrcBtn" onclick="backToSource()">↩️ Back to Source</button>';
    html+='<button class="result-btn danger" onclick="mistakePractice()">❌ Mistake Practice (Only Wrong) - ('+wrong+'/'+questions.length+')</button>';
    html+='<button class="result-btn purple" onclick="specialPractice()">🔥 Special Practice (Wrong+Skip) - ('+(wrong+skipped)+'/'+questions.length+')</button>';
    html+='<button class="result-btn" onclick="openLink(CFG.websiteUrl)">🌐 ATLAS Website</button>';
    html+='<button class="result-btn" onclick="openLink(CFG.youtubeUrl)">▶️ YouTube Channel</button>';
    html+='<button class="result-btn" onclick="openLink(CFG.whatsappUrl)">💬 WhatsApp</button>';
    html+='<button class="result-btn" onclick="openLink(CFG.groupsUrl)">📢 Groups+Channels</button>';
    html+='</div>';
    html+='<div class="divider"></div>';
    html+='<div class="section-title">📋 Solve Sheet</div>';
    html+='<div class="filter-bar">';
    html+='<button class="filter-btn active" onclick="filt(event,\'all\')">সব</button>';
    html+='<button class="filter-btn" onclick="filt(event,\'correct\')">✅ সঠিক</button>';
    html+='<button class="filter-btn" onclick="filt(event,\'wrong\')">❌ ভুল</button>';
    html+='<button class="filter-btn" onclick="filt(event,\'skip\')">⏭️ স্কিপ</button>';
    html+='</div><div id="solveSheet">'+buildSolve('all')+'</div>';
    document.getElementById('resultWrap').innerHTML=html;
    setMode('Result');
    // instant jump to top of result page (no smooth delay)
    window.scrollTo(0, 0);
    requestAnimationFrame(()=>{ window.scrollTo(0, 0); });
}

function buildSolve(filter) {
    let html='';
    questions.forEach((q,i)=>{
        const ci=typeof q.answer==='string'?({'A':0,'B':1,'C':2,'D':3}[q.answer.toUpperCase()]||0):(q.answer||0);
        const ua=userAnswers[i];
        const st=ua===undefined?'skip':ua===ci?'correct':'wrong';
        if(filter!=='all'&&st!==filter) return;
        const stL=st==='correct'?'✅ সঠিক':st==='wrong'?'❌ ভুল':'⏭️ স্কিপ';
        const bmActive = bookmarks[i] ? ' active' : '';
        html+='<div class="mcq-card" data-status="'+st+'">';
        html+='<div class="q-head"><span class="q-result-badge '+st+'">'+stL+'</span>'
            + '<button class="bm-btn'+bmActive+'" id="bmBtnR'+i+'" onclick="toggleBm('+i+')">🔖</button></div>';
        html+='<div class="q-head"><span class="q-num">প্রশ্ন '+(i+1)+'/'+questions.length+'</span></div>';
        const tagPrefix = q._tag ? '['+q._tag+']\n\n' : '';
        html+='<div class="q-text">'+(tagPrefix+q.question).replace(/\n/g,'<br>')+'</div>';
        q.options.forEach((opt,oi)=>{
            let cls='opt locked',icon='';
            if(oi===ci){cls+=' correct-r';icon='<span class="opt-icon">✅</span>';}
            else if(oi===ua&&ua!==ci){cls+=' wrong-r';icon='<span class="opt-icon">❌</span>';}
            else cls+=' dim';
            html+='<div class="'+cls+'"><span class="opt-radio">'+LABELS[oi]+'</span><span>'+opt+'</span>'+icon+'</div>';
        });
        if(q.explanation) {
            const expSuffix = q._exp ? ('\n\n📌 '+q._exp) : '';
            html+='<div class="exp-box"><div class="exp-hd">📋 ব্যাখ্যা</div>'+(q.explanation+expSuffix).replace(/\n/g,'<br>')+'</div>';
        }
        html+='</div>';
    });
    return html||'<div style="color:var(--text-secondary);text-align:center;padding:20px">কোনো প্রশ্ন নেই</div>';
}

function filt(e,t){
    document.querySelectorAll('.filter-btn').forEach(b=>b.classList.remove('active'));
    e.target.classList.add('active');
    document.getElementById('solveSheet').innerHTML=buildSolve(t);
}

let lastAnswers={};
function _saveAnswerSnapshot(){
    lastAnswers={};
    for(var k in userAnswers){ if(userAnswers.hasOwnProperty(k)) lastAnswers[Number(k)]=userAnswers[k]; }
}

function _getCorrectIdx(q){
    if(typeof q.answer==='string') return {'A':0,'B':1,'C':2,'D':3}[q.answer.toUpperCase()]||0;
    return (typeof q.answer==='number')?q.answer:0;
}

function practiceAgain(){
    questions=[...origQs];
    totalSec=Math.max(30,Math.ceil(questions.length*CFG.secPerQ));
    userAnswers={};bookmarks={};submitted=false;
    startHall();
}

function mistakePractice(){
    const wrongQs=origQs.filter(function(q,i){
        var ci=_getCorrectIdx(q);
        var ua=lastAnswers[i];
        return ua!==undefined&&ua!==null&&ua!==ci;
    });
    if(!wrongQs.length){showToast('🎉 কোনো ভুল নেই!');return;}
    _startPractice(wrongQs);
}

function specialPractice(){
    const filtered=origQs.filter(function(q,i){
        var ci=_getCorrectIdx(q);
        var ua=lastAnswers[i];
        return ua===undefined||ua===null||ua!==ci;
    });
    if(!filtered.length){showToast('✅ সব সঠিক ছিল!');return;}
    _startPractice(filtered);
}

function _startPractice(filtered){
    questions=filtered;
    origQs=[...filtered];
    totalSec=Math.max(30,Math.ceil(questions.length*CFG.secPerQ));
    userAnswers={};bookmarks={};submitted=false;
    lastAnswers={};
    startHall();
}

function startNewExam(){
    var ov=document.getElementById('newExamOverlay');
    if(!ov){
        var d=document.createElement('div');
        d.id='newExamOverlay';
        d.style.cssText='position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(10,10,30,0.95);z-index:9999;display:flex;align-items:center;justify-content:center;';
        d.innerHTML='<div style="text-align:center;max-width:360px;padding:30px;background:var(--card-bg,#1a1a3e);border-radius:20px;border:1px solid var(--accent,#5a5fe0)">'
            +'<div style="font-size:40px;margin-bottom:12px">🧠</div>'
            +'<div style="font-size:18px;font-weight:700;color:var(--accent,#5a5fe0);margin-bottom:8px">নতুন MCQ তৈরি হচ্ছে...</div>'
            +'<div id="neProgressLabel" style="font-size:13px;color:var(--text-secondary,#aaa);margin-bottom:14px">AI থেকে প্রশ্ন জেনারেট করা হচ্ছে</div>'
            +'<div style="background:rgba(90,95,224,0.15);border-radius:10px;height:18px;overflow:hidden;margin-bottom:8px">'
            +'<div id="neBarFill" style="height:100%;width:0%;background:linear-gradient(90deg,#5a5fe0,#7b61ff);border-radius:10px;transition:width 0.5s"></div></div>'
            +'<div id="neTimerLabel" style="font-size:12px;color:var(--text-secondary,#aaa);margin-bottom:16px">⏱️ 0 সেকেন্ড</div>'
            +'<button id="neStartBtn" disabled onclick="neGoToExam()" style="width:100%;padding:14px;border:none;border-radius:12px;font-size:16px;font-weight:700;cursor:pointer;'
            +'background:rgba(90,95,224,0.3);color:rgba(255,255,255,0.4);pointer-events:none">🚀 Start Exam</button>'
            +'</div>';
        document.body.appendChild(d);
        ov=d;
    }
    ov.style.display='flex';
    var elapsed=0, neReady=false, neUrl='';
    var neTimer=setInterval(function(){
        elapsed++;
        var el=document.getElementById('neTimerLabel');
        if(el) el.textContent='⏱️ '+elapsed+' সেকেন্ড';
        var bar=document.getElementById('neBarFill');
        if(bar&&!neReady){
            var pct=Math.min(90,Math.round((elapsed/60)*90));
            bar.style.width=pct+'%';
        }
        var lbl=document.getElementById('neProgressLabel');
        if(lbl&&!neReady){
            var msgs=['AI থেকে প্রশ্ন জেনারেট করা হচ্ছে','MCQ ফরম্যাট চেক হচ্ছে','অপশন ভেরিফাই হচ্ছে','প্রায় শেষ হয়ে আসছে'];
            lbl.textContent=msgs[Math.min(Math.floor(elapsed/8),msgs.length-1)];
        }
    },1000);
    var ctrl=new AbortController();
    var tid=setTimeout(function(){ctrl.abort();},120000);
    fetch('/api/new-exam',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({cache_id:CFG.cacheId,user_id:USER_ID}),
        signal:ctrl.signal
    }).then(function(r){
        if(!r.ok) throw new Error('HTTP '+r.status);
        return r.json();
    }).then(function(d){
        clearTimeout(tid);
        if(d.ok&&d.new_cache_id){
            neReady=true;
            neUrl='/exam/'+d.new_cache_id+'?uid='+USER_ID+'&name='+encodeURIComponent(USER_NAME);
            var bar=document.getElementById('neBarFill');
            if(bar) bar.style.width='100%';
            var lbl=document.getElementById('neProgressLabel');
            if(lbl){lbl.textContent='✅ '+(d.count||'')+ ' টি MCQ তৈরি হয়েছে!';lbl.style.color='#4ade80';}
            var btn=document.getElementById('neStartBtn');
            if(btn){btn.disabled=false;btn.style.background='linear-gradient(135deg,#22c55e,#16a34a)';btn.style.color='#fff';btn.style.pointerEvents='auto';btn.style.boxShadow='0 0 20px rgba(34,197,94,0.4)';}
            clearInterval(neTimer);
            window._neUrl=neUrl;
        } else {
            clearInterval(neTimer);
            showToast(d.message||'❌ তৈরি করা যায়নি।',4000);
            ov.style.display='none';
        }
    }).catch(function(e){
        clearTimeout(tid);clearInterval(neTimer);
        var msg=e.name==='AbortError'?'⏱️ Timeout — আবার চেষ্টা করো':'❌ Network error';
        showToast(msg,4000);
        ov.style.display='none';
    });
}
function neGoToExam(){
    if(window._neUrl) window.location.href=window._neUrl;
}

function backToSource(){
    const btn=document.getElementById('backSrcBtn');
    if(btn){btn.disabled=true;btn.textContent='⏳ পাঠানো হচ্ছে...';}
    fetch('/api/back-to-source',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({cache_id:CFG.cacheId,user_id:USER_ID})
    }).then(r=>r.json()).then(d=>{
        if(d.ok){
            showToast('✅ Telegram-এ পাঠানো হয়েছে। Bot-এ গিয়ে message-এ tap করুন।');
            if(btn){btn.textContent='✅ Telegram-এ চেক করুন';setTimeout(()=>{btn.disabled=false;btn.textContent='↩️ Back to Source';},4000);}
        }else{
            showToast(d.message||'❌ Error');
            if(btn){btn.disabled=false;btn.textContent='↩️ Back to Source';}
        }
    }).catch(()=>{
        showToast('❌ Network error');
        if(btn){btn.disabled=false;btn.textContent='↩️ Back to Source';}
    });
}

function solvePDF(){
    var btn=document.querySelector('.result-btn.success');
    if(btn){btn.disabled=true;btn.textContent='⏳ PDF তৈরি হচ্ছে...';}
    fetch('/api/solve-pdf-direct/'+CFG.cacheId).then(function(r){
        if(!r.ok) throw new Error('HTTP '+r.status);
        return r.blob();
    }).then(function(blob){
        var url=URL.createObjectURL(blob);
        window.open(url,'_blank');
        if(btn){btn.disabled=false;btn.textContent='📄 Solve PDF';}
    }).catch(function(){
        window.open('/api/solve-pdf-direct/'+CFG.cacheId,'_blank');
        if(btn){btn.disabled=false;btn.textContent='📄 Solve PDF';}
    });
}

function openLink(url){ if(url) window.open(url,'_blank'); }
function zp(n){return String(n).padStart(2,'0');}
function setMode(m){
    document.querySelectorAll('.mode').forEach(el=>el.classList.remove('active'));
    document.getElementById('mode'+m).classList.add('active');
}
function showToast(msg,dur){
    dur=dur||2800;
    const t=document.getElementById('toast');
    t.textContent=msg;t.classList.add('show');
    clearTimeout(t._t);t._t=setTimeout(()=>t.classList.remove('show'),dur);
}
function toggleTheme() {
    const isLight = document.body.classList.toggle('light-theme');
    document.getElementById('themeToggle').textContent = isLight ? '☀️' : '🌙';
    try { localStorage.setItem('atlas_theme', isLight ? 'light' : 'dark'); } catch(e){}
}
function applyStoredTheme() {
    let saved = 'dark';
    try { saved = localStorage.getItem('atlas_theme') || 'dark'; } catch(e){}
    if (saved === 'light') {
        document.body.classList.add('light-theme');
        const t = document.getElementById('themeToggle');
        if (t) t.textContent = '☀️';
    }
}
applyStoredTheme();
init();
</script>
</body>
</html>'''

# ============================================================
# SECTION 13: STARTUP
# ============================================================
def _ensure_supabase_bookmarks_table():
    try:
        client = get_supabase()
        client.table('bookmarks').select('id').limit(1).execute()
        print("[startup] bookmarks table OK (SELECT works)")
    except Exception as e:
        err = str(e)
        print(f"[startup] bookmarks table check failed: {err}")
        print("[startup] ⚠️ If bookmarks don't save, run in Supabase SQL Editor:")
        print("""  CREATE TABLE IF NOT EXISTS bookmarks (
    id BIGSERIAL PRIMARY KEY, user_id BIGINT, cache_id TEXT,
    question_index INTEGER, question_data TEXT, topic TEXT, page INTEGER, created_at TEXT
  );
  CREATE INDEX IF NOT EXISTS idx_bookmarks_user ON bookmarks(user_id);
  ALTER TABLE bookmarks DISABLE ROW LEVEL SECURITY;""")
    try:
        test_row = {
            'user_id': -1, 'cache_id': '_rls_test_', 'question_index': -1,
            'question_data': '{}', 'topic': '', 'page': 0,
            'created_at': int(datetime.now(BD_TZ).timestamp())
        }
        client = get_supabase()
        client.table('bookmarks').insert(test_row).execute()
        client.table('bookmarks').delete().eq('user_id', -1).eq('cache_id', '_rls_test_').execute()
        print("[startup] bookmarks INSERT/DELETE OK (RLS not blocking)")
    except Exception as e:
        err = str(e)
        print(f"[startup] ⚠️ bookmarks WRITE BLOCKED: {err}")
        if "row-level security" in err.lower() or "rls" in err.lower() or "policy" in err.lower() or "42501" in err:
            print("[startup] 🔴 RLS is ENABLED on bookmarks table! Bookmarks will NOT save!")
            print("[startup] 🔴 FIX: Run in Supabase SQL Editor: ALTER TABLE bookmarks DISABLE ROW LEVEL SECURITY;")
        else:
            print("[startup] 🔴 Bookmark writes failing for unknown reason — check Supabase permissions")

@app.on_event("startup")
async def startup():
    print("=" * 60)
    print("🌐 ATLAS Exam Server v3.0 Starting...")
    print("=" * 60)
    setup_gemini()
    get_supabase_backup()
    _ensure_supabase_bookmarks_table()
    print("✅ Exam Server Ready! (Creative PDF + Full-page Premium PDF)")
    print("=" * 60)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "7860"))
    uvicorn.run(app, host="0.0.0.0", port=port)