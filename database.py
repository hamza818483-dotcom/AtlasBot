"""
ATLAS MCQ BOT - Database Module
Supabase CRUD operations for all atlas_ tables
"""

import os
import json
import random
import string
import time
# ============================================
# IN-MEMORY CACHE
# ============================================
_user_cache = {}  # {user_id: user_data}
_settings_cache = {}  # {key: value}
CACHE_TTL = 300  # 5 minutes

def _cache_get(cache, key):
    entry = cache.get(key)
    if entry and (time.time() - entry['t']) < CACHE_TTL:
        return entry['v']
    return None

def _cache_set(cache, key, value):
    cache[key] = {'v': value, 't': time.time()}

def _cache_del(cache, key):
    cache.pop(key, None)
from datetime import datetime, date
from supabase import create_client, Client
from config import (
    SUPABASE_URL, SUPABASE_KEY, BD_TZ, 
    DEFAULT_FREE_LIMIT, DEFAULT_DAILY_LIMIT, DEFAULT_NEGATIVE_MARK,
    DEFAULT_TIMER, LOG_DIR
)

# ============================================
# LOGGING SETUP
# ============================================
LOG_FILE = os.path.join(LOG_DIR, f"db_{datetime.now(BD_TZ).strftime('%Y-%m-%d')}.log")

def log(message, level="INFO"):
    """Log messages with timestamp"""
    timestamp = datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] [{level}] {message}"
    print(log_msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_msg + "\n")

def log_error(message):
    """Log error messages"""
    log(message, "ERROR")

def log_success(message):
    """Log success messages"""
    log(message, "SUCCESS")

# ============================================
# SUPABASE CLIENT INIT
# ============================================
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    log("✅ Supabase client initialized", "SUCCESS")
except Exception as e:
    log_error(f"❌ Supabase client initialization failed: {str(e)}")
    raise

# ============================================
# TABLE CREATION
# ============================================
def create_tables():
    """Create all atlas_ tables if not exists"""
    log("📋 Checking database tables...")
    
    queries = [
        # atlas_users
        """
        CREATE TABLE IF NOT EXISTS atlas_users (
            user_id BIGINT PRIMARY KEY,
            first_name TEXT DEFAULT '',
            username TEXT DEFAULT '',
            is_permitted BOOLEAN DEFAULT false,
            daily_usage INTEGER DEFAULT 0,
            last_reset_date DATE DEFAULT CURRENT_DATE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        
        # atlas_settings
        """
        CREATE TABLE IF NOT EXISTS atlas_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        
        # atlas_limits
        """
        CREATE TABLE IF NOT EXISTS atlas_limits (
            user_id BIGINT PRIMARY KEY REFERENCES atlas_users(user_id) ON DELETE CASCADE,
            custom_limit INTEGER,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        
        # atlas_mcq_store
        """
        CREATE TABLE IF NOT EXISTS atlas_mcq_store (
            quiz_id TEXT PRIMARY KEY,
            user_id BIGINT REFERENCES atlas_users(user_id) ON DELETE CASCADE,
            mcqs JSONB NOT NULL,
            source_type TEXT DEFAULT 'text',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        
        # atlas_results
        """
        CREATE TABLE IF NOT EXISTS atlas_results (
            id SERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES atlas_users(user_id) ON DELETE CASCADE,
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
        
        # atlas_bookmarks
        """
        CREATE TABLE IF NOT EXISTS atlas_bookmarks (
            id SERIAL PRIMARY KEY,
            phone TEXT NOT NULL,
            question_text TEXT DEFAULT '',
            option1 TEXT DEFAULT '',
            option2 TEXT DEFAULT '',
            option3 TEXT DEFAULT '',
            option4 TEXT DEFAULT '',
            option5 TEXT DEFAULT '',
            answer_index INTEGER DEFAULT 1,
            explanation TEXT DEFAULT '',
            exam_name TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            chapter TEXT DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        
        # atlas_usage_logs
        """
        CREATE TABLE IF NOT EXISTS atlas_usage_logs (
            id SERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES atlas_users(user_id) ON DELETE CASCADE,
            usage_date DATE DEFAULT CURRENT_DATE,
            usage_count INTEGER DEFAULT 0,
            UNIQUE(user_id, usage_date)
        );
        """
    ]
    
    try:
        for query in queries:
            supabase.rpc('exec_sql', {'sql': query}).execute()
        log("✅ All tables verified/created", "SUCCESS")
        
        # Insert default settings
        insert_default_settings()
    except Exception as e:
        log_error(f"❌ Table creation error: {str(e)}")
        # Try alternative: direct SQL via REST API
        try:
            for query in queries:
                # Use raw SQL endpoint if available
                supabase.table('atlas_users').select('*').limit(1).execute()
            log("✅ Tables exist - verified", "SUCCESS")
            insert_default_settings()
        except Exception as e2:
            log_error(f"❌ Table verification failed: {str(e2)}")

def insert_default_settings():
    """Insert default settings if not exists"""
    defaults = {
        'free_limit': str(DEFAULT_FREE_LIMIT),
        'daily_limit': str(DEFAULT_DAILY_LIMIT),
        'default_limit': str(DEFAULT_DAILY_LIMIT),
        'negative_mark': str(DEFAULT_NEGATIVE_MARK),
        'timer_seconds': str(DEFAULT_TIMER)
    }
    
    for key, value in defaults.items():
        try:
            set_setting(key, value)
            log(f"⚙️ Default setting: {key} = {value}")
        except Exception as e:
            log_error(f"❌ Setting insert error for {key}: {str(e)}")

# ============================================
# HELPER FUNCTIONS
# ============================================
def generate_quiz_id():
    """Generate random quiz ID"""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

def get_bd_date():
    """Get current date in Bangladesh timezone"""
    return datetime.now(BD_TZ).date()

def get_bd_now():
    """Get current datetime in Bangladesh timezone"""
    return datetime.now(BD_TZ)

# ============================================
# ATLAS_USERS CRUD
# ============================================
def create_user(user_id, first_name="", username=""):
    """Create new user or update existing"""
    log(f"👤 Creating/updating user: {user_id}")
    try:
        # Check if user exists
        existing = get_user(user_id)
        if existing:
            # Update existing user
            supabase.table('atlas_users').update({
                'first_name': first_name or existing.get('first_name', ''),
                'username': username or existing.get('username', '')
            }).eq('user_id', user_id).execute()
            log(f"👤 User updated: {user_id}")
        else:
            # Insert new user
            supabase.table('atlas_users').insert({
                'user_id': user_id,
                'first_name': first_name,
                'username': username,
                'is_permitted': False,
                'daily_usage': 0,
                'last_reset_date': str(get_bd_date())
            }).execute()
            log(f"👤 New user created: {user_id}")
        return True
    except Exception as e:
        log_error(f"❌ User create/update error: {user_id} - {str(e)}")
        return False

def get_user(user_id):
    """Get user by ID"""
    cached = _cache_get(_user_cache, user_id)
    if cached:
        return cached
    try:
        result = supabase.table('atlas_users').select('*').eq('user_id', user_id).execute()
        if result.data and len(result.data) > 0:
            _cache_set(_user_cache, user_id, result.data[0])
            return result.data[0]
        return None
    except Exception as e:
        log_error(f"❌ User fetch error: {user_id} - {str(e)}")
        return None

def update_user(user_id, data):
    """Update user fields"""
    try:
        supabase.table('atlas_users').update(data).eq('user_id', user_id).execute()
        _cache_del(_user_cache, user_id)  # invalidate cache
        return True
    except Exception as e:
        log_error(f"❌ User update error: {user_id} - {str(e)}")
        return False

def is_permitted(user_id):
    """Check if user is permitted"""
    user = get_user(user_id)
    if user:
        return user.get('is_permitted', False)
    return False

def permit_user(user_id):
    """Grant permission to user"""
    log(f"🔓 Permitting user: {user_id}")
    # Ensure user exists
    create_user(user_id)
    return update_user(user_id, {'is_permitted': True})

def unpermit_user(user_id):
    """Remove permission from user"""
    log(f"🔒 Removing permission: {user_id}")
    return update_user(user_id, {'is_permitted': False})

def get_all_users():
    """Get all users sorted by usage"""
    log("📊 Fetching all users")
    try:
        result = supabase.table('atlas_users').select('*').order('daily_usage', desc=True).execute()
        log(f"✅ Fetched {len(result.data)} users")
        return result.data
    except Exception as e:
        log_error(f"❌ All users fetch error: {str(e)}")
        return []

# ============================================
# ATLAS_SETTINGS CRUD
# ============================================
def get_setting(key, default=None):
    """Get a setting value"""
    log(f"⚙️ Getting setting: {key}")
    try:
        result = supabase.table('atlas_settings').select('value').eq('key', key).execute()
        if result.data and len(result.data) > 0:
            value = result.data[0]['value']
            log(f"✅ Setting {key} = {value}")
            return value
        log(f"⚠️ Setting not found: {key}, using default: {default}")
        return default
    except Exception as e:
        log_error(f"❌ Setting fetch error: {key} - {str(e)}")
        return default

def set_setting(key, value):
    """Set a setting value (upsert)"""
    log(f"⚙️ Setting {key} = {value}")
    try:
        # Try update first
        result = supabase.table('atlas_settings').update({
            'value': str(value),
            'updated_at': get_bd_now().isoformat()
        }).eq('key', key).execute()
        
        if not result.data or len(result.data) == 0:
            # Insert if not exists
            supabase.table('atlas_settings').insert({
                'key': key,
                'value': str(value)
            }).execute()
        
        log(f"✅ Setting saved: {key} = {value}")
        return True
    except Exception as e:
        log_error(f"❌ Setting save error: {key} - {str(e)}")
        return False

def get_all_settings():
    """Get all settings as dict"""
    log("⚙️ Fetching all settings")
    settings = {
        'free_limit': DEFAULT_FREE_LIMIT,
        'daily_limit': DEFAULT_DAILY_LIMIT,
        'negative_mark': DEFAULT_NEGATIVE_MARK,
        'timer_seconds': DEFAULT_TIMER
    }
    try:
        result = supabase.table('atlas_settings').select('*').execute()
        for row in result.data:
            key = row['key']
            value = row['value']
            # Convert to appropriate type
            if key in ['free_limit', 'daily_limit', 'default_limit', 'timer_seconds']:
                settings[key] = int(value)
            elif key == 'negative_mark':
                settings[key] = float(value)
            else:
                settings[key] = value
        log(f"✅ Fetched {len(result.data)} settings")
    except Exception as e:
        log_error(f"❌ Settings fetch error: {str(e)}")
    return settings

# ============================================
# ATLAS_LIMITS CRUD
# ============================================
def get_user_limit(user_id):
    """Get user's daily limit"""
    log(f"🔍 Getting limit for user: {user_id}")
    try:
        # Check custom limit
        result = supabase.table('atlas_limits').select('custom_limit').eq('user_id', user_id).execute()
        if result.data and len(result.data) > 0 and result.data[0]['custom_limit']:
            limit = result.data[0]['custom_limit']
            log(f"✅ Custom limit: {user_id} = {limit}")
            return limit
        
        # Return default daily limit
        default = int(get_setting('daily_limit', DEFAULT_DAILY_LIMIT))
        log(f"✅ Default limit: {user_id} = {default}")
        return default
    except Exception as e:
        log_error(f"❌ Limit fetch error: {user_id} - {str(e)}")
        return DEFAULT_DAILY_LIMIT

def set_user_limit(user_id, limit):
    """Set custom limit for user"""
    log(f"📝 Setting custom limit: {user_id} = {limit}")
    try:
        # Upsert
        result = supabase.table('atlas_limits').update({
            'custom_limit': limit,
            'updated_at': get_bd_now().isoformat()
        }).eq('user_id', user_id).execute()
        
        if not result.data or len(result.data) == 0:
            supabase.table('atlas_limits').insert({
                'user_id': user_id,
                'custom_limit': limit
            }).execute()
        
        log(f"✅ Custom limit set: {user_id} = {limit}")
        return True
    except Exception as e:
        log_error(f"❌ Limit set error: {user_id} - {str(e)}")
        return False

# ============================================
# ATLAS_MCQ_STORE CRUD
# ============================================
def save_mcq(user_id, mcqs, source_type='text'):
    """Save generated MCQs"""
    log(f"💾 Saving MCQ: user={user_id}, type={source_type}, count={len(mcqs)}")
    try:
        quiz_id = generate_quiz_id()
        supabase.table('atlas_mcq_store').insert({
            'quiz_id': quiz_id,
            'user_id': user_id,
            'mcqs': json.dumps(mcqs),
            'source_type': source_type,
            'created_at': get_bd_now().isoformat()
        }).execute()
        log(f"✅ MCQ saved: {quiz_id}")
        return quiz_id
    except Exception as e:
        log_error(f"❌ MCQ save error: {str(e)}")
        return None

def get_mcq(quiz_id):
    """Get MCQ by quiz ID"""
    log(f"🔍 Fetching MCQ: {quiz_id}")
    try:
        result = supabase.table('atlas_mcq_store').select('*').eq('quiz_id', quiz_id).execute()
        if result.data and len(result.data) > 0:
            mcq_data = result.data[0]
            mcq_data['mcqs'] = json.loads(mcq_data['mcqs']) if isinstance(mcq_data['mcqs'], str) else mcq_data['mcqs']
            log(f"✅ MCQ fetched: {quiz_id} - {len(mcq_data['mcqs'])} questions")
            return mcq_data
        log(f"⚠️ MCQ not found: {quiz_id}")
        return None
    except Exception as e:
        log_error(f"❌ MCQ fetch error: {quiz_id} - {str(e)}")
        return None

def get_user_mcqs(user_id):
    """Get all MCQs for a user"""
    log(f"📚 Fetching all MCQs for user: {user_id}")
    try:
        result = supabase.table('atlas_mcq_store').select('*').eq('user_id', user_id).order('created_at', desc=True).execute()
        for row in result.data:
            row['mcqs'] = json.loads(row['mcqs']) if isinstance(row['mcqs'], str) else row['mcqs']
        log(f"✅ Fetched {len(result.data)} MCQs for user {user_id}")
        return result.data
    except Exception as e:
        log_error(f"❌ User MCQs fetch error: {user_id} - {str(e)}")
        return []

# ============================================
# ATLAS_RESULTS CRUD
# ============================================
def save_result(user_id, quiz_name, total, right, wrong, skipped, time_taken, mark, negative_mark):
    """Save quiz result"""
    log(f"📊 Saving result: user={user_id}, quiz={quiz_name}, mark={mark}")
    try:
        supabase.table('atlas_results').insert({
            'user_id': user_id,
            'quiz_name': quiz_name,
            'total_questions': total,
            'right_answers': right,
            'wrong_answers': wrong,
            'skipped': skipped,
            'time_taken': time_taken,
            'mark': mark,
            'negative_mark': negative_mark,
            'created_at': get_bd_now().isoformat()
        }).execute()
        log(f"✅ Result saved: {user_id} - {quiz_name}")
        return True
    except Exception as e:
        log_error(f"❌ Result save error: {str(e)}")
        return False

def get_user_results(user_id):
    """Get user's quiz results"""
    log(f"📊 Fetching results for user: {user_id}")
    try:
        result = supabase.table('atlas_results').select('*').eq('user_id', user_id).order('created_at', desc=True).execute()
        log(f"✅ Fetched {len(result.data)} results")
        return result.data
    except Exception as e:
        log_error(f"❌ Results fetch error: {user_id} - {str(e)}")
        return []

# ============================================
# ATLAS_BOOKMARKS CRUD
# ============================================
def add_bookmark(phone, question_data):
    """Add a bookmarked question"""
    log(f"🔖 Adding bookmark for: {phone}")
    try:
        supabase.table('atlas_bookmarks').insert({
            'phone': phone,
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
            'created_at': get_bd_now().isoformat()
        }).execute()
        log(f"✅ Bookmark added: {phone}")
        return True
    except Exception as e:
        log_error(f"❌ Bookmark add error: {phone} - {str(e)}")
        return False

def get_bookmarks(phone):
    """Get all bookmarks for a user"""
    log(f"🔖 Fetching bookmarks for: {phone}")
    try:
        result = supabase.table('atlas_bookmarks').select('*').eq('phone', phone).order('created_at', desc=True).execute()
        log(f"✅ Fetched {len(result.data)} bookmarks")
        return result.data
    except Exception as e:
        log_error(f"❌ Bookmarks fetch error: {phone} - {str(e)}")
        return []

def delete_bookmark(bookmark_id, phone):
    """Delete a specific bookmark"""
    log(f"🗑️ Deleting bookmark: {bookmark_id}")
    try:
        supabase.table('atlas_bookmarks').delete().eq('id', bookmark_id).eq('phone', phone).execute()
        log(f"✅ Bookmark deleted: {bookmark_id}")
        return True
    except Exception as e:
        log_error(f"❌ Bookmark delete error: {bookmark_id} - {str(e)}")
        return False

# ============================================
# ATLAS_USAGE_LOGS CRUD
# ============================================
def get_today_usage(user_id):
    """Get today's usage count"""
    today = get_bd_date()
    log(f"📊 Getting today usage: user={user_id}, date={today}")
    try:
        result = supabase.table('atlas_usage_logs').select('usage_count').eq('user_id', user_id).eq('usage_date', str(today)).execute()
        if result.data and len(result.data) > 0:
            count = result.data[0]['usage_count']
            log(f"✅ Today usage: {user_id} = {count}")
            return count
        log(f"⚠️ No usage today: {user_id}")
        return 0
    except Exception as e:
        log_error(f"❌ Usage fetch error: {user_id} - {str(e)}")
        return 0

def increment_usage(user_id):
    """Increment today's usage"""
    today = str(get_bd_date())
    log(f"📈 Incrementing usage: user={user_id}, date={today}")
    try:
        # Get current count
        result = supabase.table('atlas_usage_logs').select('*').eq('user_id', user_id).eq('usage_date', today).execute()
        
        if result.data and len(result.data) > 0:
            # Update existing
            current = result.data[0]['usage_count']
            supabase.table('atlas_usage_logs').update({
                'usage_count': current + 1
            }).eq('user_id', user_id).eq('usage_date', today).execute()
            new_count = current + 1
        else:
            # Insert new
            supabase.table('atlas_usage_logs').insert({
                'user_id': user_id,
                'usage_date': today,
                'usage_count': 1
            }).execute()
            new_count = 1
        
        # Also update atlas_users
        update_user(user_id, {'daily_usage': new_count})
        
        log(f"✅ Usage incremented: {user_id} = {new_count}")
        return new_count
    except Exception as e:
        log_error(f"❌ Usage increment error: {user_id} - {str(e)}")
        return 0

def get_usage_report():
    """Get usage report for all users (for /info)"""
    log("📊 Generating usage report")
    try:
        # Get all users with usage
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
                'usage': usage,
                'limit': limit,
                'is_permitted': is_perm,
                'status': '✅' if is_perm else '🔒'
            })
        
        # Sort by usage descending
        report.sort(key=lambda x: x['usage'], reverse=True)
        log(f"✅ Usage report generated: {len(report)} users")
        return report
    except Exception as e:
        log_error(f"❌ Usage report error: {str(e)}")
        return []

# ============================================
# DAILY RESET
# ============================================
def reset_daily_usage():
    """Reset daily usage for all users at midnight"""
    log("🔄 Running daily reset...")
    try:
        today = str(get_bd_date())
        # Reset all users' daily_usage to 0
        supabase.table('atlas_users').update({
            'daily_usage': 0,
            'last_reset_date': today
        }).neq('user_id', 0).execute()  # Update all users
        log("✅ Daily reset completed", "SUCCESS")
        return True
    except Exception as e:
        log_error(f"❌ Daily reset error: {str(e)}")
        return False

# ============================================
# ACCESS CONTROL
# ============================================
def check_access(user_id):
    """
    Check if user can use the bot
    Returns: (allowed, current_usage, limit, is_permitted)
    """
    log(f"🔐 Checking access for: {user_id}")
    
    # Ensure user exists
    user = get_user(user_id)
    if not user:
        create_user(user_id)
        user = get_user(user_id)
    
    is_perm = user.get('is_permitted', False)
    current_usage = get_today_usage(user_id)
    
    if is_perm:
        # Permitted user - use daily limit
        limit = get_user_limit(user_id)
        allowed = current_usage < limit
        log(f"✅ Permitted user: {user_id} = {current_usage}/{limit} (allowed={allowed})")
    else:
        # Free user - use free limit
        limit = int(get_setting('free_limit', DEFAULT_FREE_LIMIT))
        allowed = current_usage < limit
        log(f"🔒 Free user: {user_id} = {current_usage}/{limit} (allowed={allowed})")
    
    return allowed, current_usage, limit, is_perm

# ============================================
# QUIZ DATA (For inline quiz)
# ============================================
# In-memory storage for active quizzes
active_quizzes = {}

def save_active_quiz(chat_id, quiz_data):
    """Save active quiz in memory"""
    active_quizzes[chat_id] = quiz_data
    log(f"🎯 Active quiz saved: chat={chat_id}")

def get_active_quiz(chat_id):
    """Get active quiz from memory"""
    return active_quizzes.get(chat_id)

def remove_active_quiz(chat_id):
    """Remove active quiz from memory"""
    if chat_id in active_quizzes:
        del active_quizzes[chat_id]
        log(f"🎯 Active quiz removed: chat={chat_id}")

# ============================================
# INITIALIZATION
# ============================================
def init_database():
    """Initialize database on startup"""
    log("=" * 50)
    log("🚀 INITIALIZING DATABASE")
    log("=" * 50)
    create_tables()
    log("✅ Database initialization complete")
    log("=" * 50)
