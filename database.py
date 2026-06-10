"""
ATLAS MCQ BOT - Database Module (v2.0)
======================================
Supabase CRUD for all atlas_ tables.
New in v2.0:
  - Prompt CRUD (DB-backed, seeds DEFAULT_PROMPTS, editable via /prompt)
  - Source-image storage per quiz (pre-msg, back-to-image, new-exam regen)
  - Practice counter (caption "Today Practice No")
  - Exam result + leaderboard + new-gen tracking (for exam_server)
Existing atlas_ schema preserved; only minimal new columns/tables added.
"""

import os
import json
import random
import string
import time
from datetime import datetime, date

from supabase import create_client, Client
from config import (
    OWNER_ID, SUPABASE_URL, SUPABASE_KEY, BD_TZ,
    DEFAULT_FREE_LIMIT, DEFAULT_DAILY_LIMIT, DEFAULT_NEGATIVE_MARK,
    DEFAULT_TIMER, LOG_DIR, DEFAULT_PROMPTS,
)

# ============================================
# IN-MEMORY CACHE
# ============================================
_user_cache = {}
_settings_cache = {}
_prompt_cache = {}
CACHE_TTL = 300  # 5 min


def _cache_get(cache, key):
    entry = cache.get(key)
    if entry and (time.time() - entry['t']) < CACHE_TTL:
        return entry['v']
    return None


def _cache_set(cache, key, value):
    cache[key] = {'v': value, 't': time.time()}


def _cache_del(cache, key):
    cache.pop(key, None)


# ============================================
# LOGGING
# ============================================
LOG_FILE = os.path.join(LOG_DIR, f"db_{datetime.now(BD_TZ).strftime('%Y-%m-%d')}.log")


def log(message, level="INFO"):
    timestamp = datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] [{level}] {message}"
    print(log_msg)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_msg + "\n")
    except Exception:
        pass


def log_error(message):
    log(message, "ERROR")


def log_success(message):
    log(message, "SUCCESS")


# ============================================
# SUPABASE CLIENT
# ============================================
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    log("✅ Supabase client initialized", "SUCCESS")
except Exception as e:
    log_error(f"❌ Supabase init failed: {str(e)}")
    raise


# ============================================
# TABLE CREATION / MIGRATION
# ============================================
def create_tables():
    """Create/verify all atlas_ tables (idempotent)."""
    log("📋 Checking database tables...")
    queries = [
        """
        CREATE TABLE IF NOT EXISTS atlas_users (
            user_id BIGINT PRIMARY KEY,
            first_name TEXT DEFAULT '',
            username TEXT DEFAULT '',
            is_permitted BOOLEAN DEFAULT false,
            daily_usage INTEGER DEFAULT 0,
            practice_no INTEGER DEFAULT 0,
            last_reset_date DATE DEFAULT CURRENT_DATE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        # Migration for older atlas_users missing practice_no
        "ALTER TABLE atlas_users ADD COLUMN IF NOT EXISTS practice_no INTEGER DEFAULT 0;",
        """
        CREATE TABLE IF NOT EXISTS atlas_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS atlas_prompts (
            prompt_key TEXT PRIMARY KEY,
            prompt_text TEXT NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS atlas_limits (
            user_id BIGINT PRIMARY KEY,
            custom_limit INTEGER,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS atlas_mcq_store (
            quiz_id TEXT PRIMARY KEY,
            user_id BIGINT,
            mcqs JSONB NOT NULL,
            source_type TEXT DEFAULT 'text',
            image_file_id TEXT DEFAULT '',
            is_new_gen BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        # Migrations for older atlas_mcq_store
        "ALTER TABLE atlas_mcq_store ADD COLUMN IF NOT EXISTS image_file_id TEXT DEFAULT '';",
        "ALTER TABLE atlas_mcq_store ADD COLUMN IF NOT EXISTS is_new_gen BOOLEAN DEFAULT false;",
        """
        CREATE TABLE IF NOT EXISTS atlas_results (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            quiz_name TEXT DEFAULT 'MCQ Quiz',
            total_questions INTEGER DEFAULT 0,
            right_answers INTEGER DEFAULT 0,
            wrong_answers INTEGER DEFAULT 0,
            skipped INTEGER DEFAULT 0,
            time_taken INTEGER DEFAULT 0,
            mark FLOAT DEFAULT 0,
            negative_mark FLOAT DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS atlas_leaderboard (
            id SERIAL PRIMARY KEY,
            quiz_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            user_name TEXT DEFAULT 'Student',
            total INTEGER DEFAULT 0,
            correct INTEGER DEFAULT 0,
            wrong INTEGER DEFAULT 0,
            skipped INTEGER DEFAULT 0,
            final_score FLOAT DEFAULT 0,
            time_taken INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(quiz_id, user_id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS atlas_bookmarks (
            id SERIAL PRIMARY KEY,
            phone TEXT NOT NULL,
            question_text TEXT DEFAULT '',
            option1 TEXT DEFAULT '', option2 TEXT DEFAULT '',
            option3 TEXT DEFAULT '', option4 TEXT DEFAULT '',
            option5 TEXT DEFAULT '',
            answer_index INTEGER DEFAULT 1,
            explanation TEXT DEFAULT '',
            exam_name TEXT DEFAULT '', subject TEXT DEFAULT '', chapter TEXT DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS atlas_usage_logs (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            usage_date DATE DEFAULT CURRENT_DATE,
            usage_count INTEGER DEFAULT 0,
            UNIQUE(user_id, usage_date)
        );
        """,
    ]
    try:
        for q in queries:
            try:
                supabase.rpc('exec_sql', {'sql': q}).execute()
            except Exception:
                # exec_sql RPC may not exist; tables are likely pre-created in dashboard.
                pass
        # Verify core table reachable
        supabase.table('atlas_users').select('user_id').limit(1).execute()
        log("✅ Tables verified", "SUCCESS")
    except Exception as e:
        log_error(f"❌ Table verify error: {str(e)}")
    insert_default_settings()
    seed_prompts()


def insert_default_settings():
    defaults = {
        'free_limit': str(DEFAULT_FREE_LIMIT),
        'daily_limit': str(DEFAULT_DAILY_LIMIT),
        'default_limit': str(DEFAULT_DAILY_LIMIT),
        'negative_mark': str(DEFAULT_NEGATIVE_MARK),
        'timer_seconds': str(DEFAULT_TIMER),
    }
    for key, value in defaults.items():
        try:
            existing = supabase.table('atlas_settings').select('key').eq('key', key).execute()
            if not existing.data:
                set_setting(key, value)
                log(f"⚙️ Seeded setting: {key} = {value}")
        except Exception as e:
            log_error(f"❌ Setting seed error {key}: {str(e)}")


def seed_prompts():
    """Insert default prompts only if they don't already exist (never overwrite admin edits)."""
    for key, text in DEFAULT_PROMPTS.items():
        try:
            existing = supabase.table('atlas_prompts').select('prompt_key').eq('prompt_key', key).execute()
            if not existing.data:
                supabase.table('atlas_prompts').insert({'prompt_key': key, 'prompt_text': text}).execute()
                log(f"🧠 Seeded prompt: {key}")
        except Exception as e:
            log_error(f"❌ Prompt seed error {key}: {str(e)}")


# ============================================
# HELPERS
# ============================================
def generate_quiz_id():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))


def get_bd_date():
    return datetime.now(BD_TZ).date()


def get_bd_now():
    return datetime.now(BD_TZ)


# ============================================
# ATLAS_USERS
# ============================================
def create_user(user_id, first_name="", username=""):
    try:
        existing = get_user(user_id)
        if existing:
            supabase.table('atlas_users').update({
                'first_name': first_name or existing.get('first_name', ''),
                'username': username or existing.get('username', ''),
            }).eq('user_id', user_id).execute()
            _cache_del(_user_cache, user_id)
        else:
            supabase.table('atlas_users').insert({
                'user_id': user_id,
                'first_name': first_name,
                'username': username,
                'is_permitted': False,
                'daily_usage': 0,
                'practice_no': 0,
                'last_reset_date': str(get_bd_date()),
            }).execute()
            log(f"👤 New user: {user_id}")
        return True
    except Exception as e:
        log_error(f"❌ User create/update error {user_id}: {str(e)}")
        return False


def get_user(user_id):
    cached = _cache_get(_user_cache, user_id)
    if cached:
        return cached
    try:
        result = supabase.table('atlas_users').select('*').eq('user_id', user_id).execute()
        if result.data:
            _cache_set(_user_cache, user_id, result.data[0])
            return result.data[0]
        return None
    except Exception as e:
        log_error(f"❌ User fetch error {user_id}: {str(e)}")
        return None


def update_user(user_id, data):
    try:
        supabase.table('atlas_users').update(data).eq('user_id', user_id).execute()
        _cache_del(_user_cache, user_id)
        return True
    except Exception as e:
        log_error(f"❌ User update error {user_id}: {str(e)}")
        return False


def is_permitted(user_id):
    user = get_user(user_id)
    return user.get('is_permitted', False) if user else False


def permit_user(user_id):
    create_user(user_id)
    return update_user(user_id, {'is_permitted': True})


def unpermit_user(user_id):
    return update_user(user_id, {'is_permitted': False})


def get_all_users():
    try:
        result = supabase.table('atlas_users').select('*').order('daily_usage', desc=True).execute()
        return result.data
    except Exception as e:
        log_error(f"❌ All users fetch error: {str(e)}")
        return []


# ---- Practice counter (caption "Today Practice No") ----
def get_practice_no(user_id):
    user = get_user(user_id)
    return (user or {}).get('practice_no', 0)


def increment_practice_no(user_id):
    """Increment and return the user's lifetime practice number."""
    try:
        user = get_user(user_id)
        if not user:
            create_user(user_id)
            user = get_user(user_id)
        new_no = (user.get('practice_no', 0) or 0) + 1
        update_user(user_id, {'practice_no': new_no})
        return new_no
    except Exception as e:
        log_error(f"❌ practice_no increment error {user_id}: {str(e)}")
        return 1


# ============================================
# ATLAS_SETTINGS
# ============================================
def get_setting(key, default=None):
    cached = _cache_get(_settings_cache, key)
    if cached is not None:
        return cached
    try:
        result = supabase.table('atlas_settings').select('value').eq('key', key).execute()
        if result.data:
            value = result.data[0]['value']
            _cache_set(_settings_cache, key, value)
            return value
        return default
    except Exception as e:
        log_error(f"❌ Setting fetch error {key}: {str(e)}")
        return default


def set_setting(key, value):
    try:
        result = supabase.table('atlas_settings').update({
            'value': str(value), 'updated_at': get_bd_now().isoformat()
        }).eq('key', key).execute()
        if not result.data:
            supabase.table('atlas_settings').insert({'key': key, 'value': str(value)}).execute()
        _cache_del(_settings_cache, key)
        return True
    except Exception as e:
        log_error(f"❌ Setting save error {key}: {str(e)}")
        return False


def get_all_settings():
    settings = {
        'free_limit': DEFAULT_FREE_LIMIT,
        'daily_limit': DEFAULT_DAILY_LIMIT,
        'negative_mark': DEFAULT_NEGATIVE_MARK,
        'timer_seconds': DEFAULT_TIMER,
    }
    try:
        result = supabase.table('atlas_settings').select('*').execute()
        for row in result.data:
            key, value = row['key'], row['value']
            if key in ['free_limit', 'daily_limit', 'default_limit', 'timer_seconds']:
                settings[key] = int(value)
            elif key == 'negative_mark':
                settings[key] = float(value)
            else:
                settings[key] = value
    except Exception as e:
        log_error(f"❌ Settings fetch error: {str(e)}")
    return settings


# ============================================
# ATLAS_PROMPTS  (DB-backed, editable via /prompt)
# ============================================
def get_prompt(prompt_key):
    """Return prompt text for a key; falls back to DEFAULT_PROMPTS if missing."""
    cached = _cache_get(_prompt_cache, prompt_key)
    if cached is not None:
        return cached
    try:
        result = supabase.table('atlas_prompts').select('prompt_text').eq('prompt_key', prompt_key).execute()
        if result.data:
            text = result.data[0]['prompt_text']
            _cache_set(_prompt_cache, prompt_key, text)
            return text
    except Exception as e:
        log_error(f"❌ Prompt fetch error {prompt_key}: {str(e)}")
    # Fallback
    return DEFAULT_PROMPTS.get(prompt_key, DEFAULT_PROMPTS.get('medical', ''))


def set_prompt(prompt_key, prompt_text):
    """Upsert a prompt; takes effect immediately (cache invalidated)."""
    try:
        result = supabase.table('atlas_prompts').update({
            'prompt_text': prompt_text, 'updated_at': get_bd_now().isoformat()
        }).eq('prompt_key', prompt_key).execute()
        if not result.data:
            supabase.table('atlas_prompts').insert({
                'prompt_key': prompt_key, 'prompt_text': prompt_text
            }).execute()
        _cache_del(_prompt_cache, prompt_key)
        log(f"🧠 Prompt updated: {prompt_key} ({len(prompt_text)} chars)")
        return True
    except Exception as e:
        log_error(f"❌ Prompt save error {prompt_key}: {str(e)}")
        return False


def get_all_prompts():
    """Return {prompt_key: prompt_text} for all keys (DB + default fallback)."""
    out = dict(DEFAULT_PROMPTS)
    try:
        result = supabase.table('atlas_prompts').select('*').execute()
        for row in result.data:
            out[row['prompt_key']] = row['prompt_text']
    except Exception as e:
        log_error(f"❌ All prompts fetch error: {str(e)}")
    return out


# ============================================
# ATLAS_LIMITS
# ============================================
def get_user_limit(user_id):
    try:
        result = supabase.table('atlas_limits').select('custom_limit').eq('user_id', user_id).execute()
        if result.data and result.data[0]['custom_limit']:
            return result.data[0]['custom_limit']
        return int(get_setting('daily_limit', DEFAULT_DAILY_LIMIT))
    except Exception as e:
        log_error(f"❌ Limit fetch error {user_id}: {str(e)}")
        return DEFAULT_DAILY_LIMIT


def set_user_limit(user_id, limit):
    try:
        result = supabase.table('atlas_limits').update({
            'custom_limit': limit, 'updated_at': get_bd_now().isoformat()
        }).eq('user_id', user_id).execute()
        if not result.data:
            supabase.table('atlas_limits').insert({'user_id': user_id, 'custom_limit': limit}).execute()
        return True
    except Exception as e:
        log_error(f"❌ Limit set error {user_id}: {str(e)}")
        return False


# ============================================
# ATLAS_MCQ_STORE
# ============================================
def save_mcq(user_id, mcqs, source_type='text'):
    try:
        quiz_id = generate_quiz_id()
        supabase.table('atlas_mcq_store').insert({
            'quiz_id': quiz_id,
            'user_id': user_id,
            'mcqs': json.dumps(mcqs, ensure_ascii=False),
            'source_type': source_type,
            'is_new_gen': (source_type == 'newgen'),
            'created_at': get_bd_now().isoformat(),
        }).execute()
        log(f"✅ MCQ saved: {quiz_id} ({len(mcqs)} q, {source_type})")
        return quiz_id
    except Exception as e:
        log_error(f"❌ MCQ save error: {str(e)}")
        return None


def get_mcq(quiz_id):
    try:
        result = supabase.table('atlas_mcq_store').select('*').eq('quiz_id', quiz_id).execute()
        if result.data:
            d = result.data[0]
            d['mcqs'] = json.loads(d['mcqs']) if isinstance(d['mcqs'], str) else d['mcqs']
            return d
        return None
    except Exception as e:
        log_error(f"❌ MCQ fetch error {quiz_id}: {str(e)}")
        return None


def get_user_mcqs(user_id):
    try:
        result = supabase.table('atlas_mcq_store').select('*').eq('user_id', user_id).order('created_at', desc=True).execute()
        for row in result.data:
            row['mcqs'] = json.loads(row['mcqs']) if isinstance(row['mcqs'], str) else row['mcqs']
        return result.data
    except Exception as e:
        log_error(f"❌ User MCQs fetch error {user_id}: {str(e)}")
        return []


def is_new_gen(quiz_id):
    d = get_mcq(quiz_id)
    return bool(d.get('is_new_gen')) if d else False


# ---- Source image per quiz (pre-msg, back-to-image, new-exam regen) ----
def save_source_image(quiz_id, file_id, user_id=None):
    """Store the Telegram image file_id that produced this quiz."""
    try:
        supabase.table('atlas_mcq_store').update({'image_file_id': file_id}).eq('quiz_id', quiz_id).execute()
        return True
    except Exception as e:
        log_error(f"❌ save_source_image error {quiz_id}: {str(e)}")
        return False


def get_source_image(quiz_id):
    """Return {'file_id': ...} or None."""
    try:
        result = supabase.table('atlas_mcq_store').select('image_file_id').eq('quiz_id', quiz_id).execute()
        if result.data and result.data[0].get('image_file_id'):
            return {'file_id': result.data[0]['image_file_id']}
        return None
    except Exception as e:
        log_error(f"❌ get_source_image error {quiz_id}: {str(e)}")
        return None


# ============================================
# ATLAS_RESULTS  (bot quiz results)
# ============================================
def save_result(user_id, quiz_name, total, right, wrong, skipped, time_taken, mark, negative_mark):
    try:
        supabase.table('atlas_results').insert({
            'user_id': user_id, 'quiz_name': quiz_name,
            'total_questions': total, 'right_answers': right, 'wrong_answers': wrong,
            'skipped': skipped, 'time_taken': time_taken, 'mark': mark,
            'negative_mark': negative_mark, 'created_at': get_bd_now().isoformat(),
        }).execute()
        return True
    except Exception as e:
        log_error(f"❌ Result save error: {str(e)}")
        return False


def get_user_results(user_id):
    try:
        result = supabase.table('atlas_results').select('*').eq('user_id', user_id).order('created_at', desc=True).execute()
        return result.data
    except Exception as e:
        log_error(f"❌ Results fetch error {user_id}: {str(e)}")
        return []


# ============================================
# ATLAS_LEADERBOARD  (web exam — used by exam_server)
# ============================================
def save_leaderboard_entry(quiz_id, user_id, user_name, total, correct, wrong, skipped, final_score, time_taken):
    """Upsert one leaderboard row; keeps the BEST (highest score, then faster time)."""
    try:
        existing = supabase.table('atlas_leaderboard').select('*').eq('quiz_id', quiz_id).eq('user_id', user_id).execute()
        payload = {
            'quiz_id': quiz_id, 'user_id': user_id, 'user_name': user_name or 'Student',
            'total': total, 'correct': correct, 'wrong': wrong, 'skipped': skipped,
            'final_score': final_score, 'time_taken': time_taken,
        }
        if existing.data:
            old = existing.data[0]
            better = (final_score > old['final_score']) or (
                final_score == old['final_score'] and time_taken < old['time_taken']
            )
            if better:
                supabase.table('atlas_leaderboard').update(payload).eq('id', old['id']).execute()
        else:
            payload['created_at'] = get_bd_now().isoformat()
            supabase.table('atlas_leaderboard').insert(payload).execute()
        return True
    except Exception as e:
        log_error(f"❌ Leaderboard save error {quiz_id}/{user_id}: {str(e)}")
        return False


def get_leaderboard(quiz_id, limit=50):
    try:
        result = (supabase.table('atlas_leaderboard')
                  .select('*').eq('quiz_id', quiz_id)
                  .order('final_score', desc=True)
                  .order('time_taken', desc=False)
                  .limit(limit).execute())
        return result.data
    except Exception as e:
        log_error(f"❌ Leaderboard fetch error {quiz_id}: {str(e)}")
        return []


# ============================================
# ATLAS_BOOKMARKS
# ============================================
def add_bookmark(phone, question_data):
    try:
        supabase.table('atlas_bookmarks').insert({
            'phone': str(phone),
            'question_text': question_data.get('question_text', ''),
            'option1': question_data.get('option1', ''),
            'option2': question_data.get('option2', ''),
            'option3': question_data.get('option3', ''),
            'option4': question_data.get('option4', ''),
            'option5': question_data.get('option5', ''),
            'answer_index': question_data.get('answer_index', 1),
            'explanation': question_data.get('explanation', ''),
            'exam_name': question_data.get('exam_name', ''),
            'subject': question_data.get('subject', ''),
            'chapter': question_data.get('chapter', ''),
            'created_at': get_bd_now().isoformat(),
        }).execute()
        return True
    except Exception as e:
        log_error(f"❌ Bookmark add error {phone}: {str(e)}")
        return False


def get_bookmarks(phone):
    try:
        result = supabase.table('atlas_bookmarks').select('*').eq('phone', str(phone)).order('created_at', desc=True).execute()
        return result.data
    except Exception as e:
        log_error(f"❌ Bookmarks fetch error {phone}: {str(e)}")
        return []


def delete_bookmark(bookmark_id, phone):
    try:
        supabase.table('atlas_bookmarks').delete().eq('id', bookmark_id).eq('phone', str(phone)).execute()
        return True
    except Exception as e:
        log_error(f"❌ Bookmark delete error {bookmark_id}: {str(e)}")
        return False


# ============================================
# ATLAS_USAGE_LOGS
# ============================================
def get_today_usage(user_id):
    today = get_bd_date()
    try:
        result = supabase.table('atlas_usage_logs').select('usage_count').eq('user_id', user_id).eq('usage_date', str(today)).execute()
        if result.data:
            return result.data[0]['usage_count']
        return 0
    except Exception as e:
        log_error(f"❌ Usage fetch error {user_id}: {str(e)}")
        return 0


def increment_usage(user_id):
    today = str(get_bd_date())
    try:
        result = supabase.table('atlas_usage_logs').select('*').eq('user_id', user_id).eq('usage_date', today).execute()
        if result.data:
            current = result.data[0]['usage_count']
            supabase.table('atlas_usage_logs').update({'usage_count': current + 1}).eq('user_id', user_id).eq('usage_date', today).execute()
            new_count = current + 1
        else:
            supabase.table('atlas_usage_logs').insert({'user_id': user_id, 'usage_date': today, 'usage_count': 1}).execute()
            new_count = 1
        update_user(user_id, {'daily_usage': new_count})
        return new_count
    except Exception as e:
        log_error(f"❌ Usage increment error {user_id}: {str(e)}")
        return 0


def get_usage_report():
    try:
        users = get_all_users()
        report = []
        for user in users:
            usage = get_today_usage(user['user_id'])
            limit = get_user_limit(user['user_id'])
            is_perm = user.get('is_permitted', False)
            report.append({
                'user_id': user['user_id'],
                'first_name': user.get('first_name', 'Unknown'),
                'username': user.get('username', ''),
                'usage': usage, 'limit': limit, 'is_permitted': is_perm,
                'status': '✅' if is_perm else '🔒',
            })
        report.sort(key=lambda x: x['usage'], reverse=True)
        return report
    except Exception as e:
        log_error(f"❌ Usage report error: {str(e)}")
        return []


# ============================================
# DAILY RESET
# ============================================
def reset_daily_usage():
    try:
        today = str(get_bd_date())
        supabase.table('atlas_users').update({
            'daily_usage': 0, 'last_reset_date': today
        }).neq('user_id', 0).execute()
        _user_cache.clear()
        log("✅ Daily reset completed", "SUCCESS")
        return True
    except Exception as e:
        log_error(f"❌ Daily reset error: {str(e)}")
        return False


# ============================================
# ACCESS CONTROL
# ============================================
def check_access(user_id):
    """Returns (allowed, current_usage, limit, is_permitted)."""
    if user_id == OWNER_ID:
        return (True, 0, 999999, True)
    user = get_user(user_id)
    if not user:
        create_user(user_id)
        user = get_user(user_id)
    is_perm = user.get('is_permitted', False)
    current_usage = get_today_usage(user_id)
    if is_perm:
        limit = get_user_limit(user_id)
    else:
        limit = int(get_setting('free_limit', DEFAULT_FREE_LIMIT))
    allowed = current_usage < limit
    return allowed, current_usage, limit, is_perm


# ============================================
# ACTIVE QUIZ (in-memory)
# ============================================
active_quizzes = {}


def save_active_quiz(chat_id, quiz_data):
    active_quizzes[chat_id] = quiz_data


def get_active_quiz(chat_id):
    return active_quizzes.get(chat_id)


def remove_active_quiz(chat_id):
    active_quizzes.pop(chat_id, None)


# ============================================
# INIT
# ============================================
def init_database():
    log("=" * 50)
    log("🚀 INITIALIZING DATABASE")
    log("=" * 50)
    create_tables()
    log("✅ Database initialization complete")
    log("=" * 50)
