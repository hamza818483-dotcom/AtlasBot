"""
Shared Supabase client management and common database helpers.

Used by both bot.py and exam_server.py to avoid duplicating client
initialization, backup-mirror logic, and common CRUD operations.
"""

import json
from datetime import datetime
from typing import Optional, Dict

from supabase import create_client, Client

from shared.config import (
    BD_TZ, SUPABASE_URL, SUPABASE_KEY,
    SUPABASE_BACKUP_URL, SUPABASE_BACKUP_KEY,
    FREE_NEW_EXAM_LIMIT, PERMITTED_NEW_EXAM_LIMIT,
    NEGATIVE_MARK,
)

# ── Singleton clients ──
_supabase: Optional[Client] = None
_supabase_backup: Optional[Client] = None


def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase client initialized")
    return _supabase


def get_supabase_backup() -> Optional[Client]:
    global _supabase_backup
    if not SUPABASE_BACKUP_URL or not SUPABASE_BACKUP_KEY:
        return None
    if _supabase_backup is None:
        try:
            _supabase_backup = create_client(SUPABASE_BACKUP_URL, SUPABASE_BACKUP_KEY)
            print("✅ Supabase BACKUP client initialized")
        except Exception as e:
            print(f"⚠️ Supabase backup init failed: {e}")
            return None
    return _supabase_backup


def mirror_insert(table: str, row: Dict) -> None:
    """Best-effort mirror to backup DB. Never raises."""
    try:
        bk = get_supabase_backup()
        if bk:
            bk.table(table).insert(row).execute()
    except Exception as e:
        print(f"Backup mirror({table}) skipped: {e}")


# ── Common DB Helpers ──

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
    """Returns (allowed, used, limit, is_permitted)."""
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
            client.table('users').update(
                {'new_exam_count': 0, 'last_new_exam_reset': today}
            ).eq('user_id', user_id).execute()
            used = 0
        except Exception:
            pass
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
        mirror_insert('results', row)
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
        mirror_insert('bookmarks', row)
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
