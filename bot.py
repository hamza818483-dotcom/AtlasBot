"""
ATLAS MCQ BOT - Main Telegram Bot (v2.0)
================================================
Features:
- 4-option MCQ type selection (Medical / True-False / Hard / Mixed)
- Image caption with welcome + practice no + ayat, auto-pinned
- Pre-message with the source image before Quiz/Poll/Web Exam
- /send broadcast (admin), /prompt editor (admin), scope-based /start
- Result buttons: Again / New Exam / Mistake / Leaderboard / Back to Image
- All Telegram API + file ops routed via CF Worker proxy (HF blocks direct TG)
================================================
"""

import asyncio
import json
import time
import traceback
import random
import threading
from datetime import datetime, timedelta

import httpx
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    Poll, BotCommand, BotCommandScopeDefault, BotCommandScopeChat,
    InputFile,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler, MessageHandler, CallbackQueryHandler,
    PollAnswerHandler, filters, ContextTypes,
)
from telegram.constants import ParseMode

from config import (
    BOT_TOKEN, OWNER_ID, CF_WORKER_URL, CF_BOT_BASE, CF_FILE_BASE,
    DEFAULT_TIMER, DEFAULT_NEGATIVE_MARK, NEW_PRACTICE_COUNT,
    POLL_DELAY, BD_TZ, AYATS, FEEDBACKS, MOTIVATION_AYATS,
    PROCESSING_MSG, PREMIUM_MSG, LOG_DIR, MIN_TEXT_LEN, NO_INFO_MSG,
    PROMPT_TYPES, USER_COMMANDS, ADMIN_COMMANDS, WELCOME_CAPTION,
    EXAM_BASE_URL, ATLAS_WEBSITE, ATLAS_YOUTUBE,
)
from database import (
    create_user, get_user, update_user,
    permit_user, unpermit_user, get_all_users,
    get_setting, set_setting, get_all_settings,
    get_user_limit, set_user_limit,
    save_mcq, get_mcq, get_user_mcqs,
    save_result, get_practice_no, increment_practice_no,
    add_bookmark,
    get_today_usage, increment_usage, get_usage_report,
    check_access, reset_daily_usage, init_database,
    save_active_quiz, get_active_quiz, remove_active_quiz,
    get_prompt, set_prompt, get_all_prompts,
    save_source_image, get_source_image,
)
from gemini_mcq import mcq_generator
from exam_server import create_exam_link, store_exam

import os

# ============================================
# LOGGING
# ============================================
LOG_FILE = os.path.join(LOG_DIR, f"bot_{datetime.now(BD_TZ).strftime('%Y-%m-%d')}.log")


def log(message, level="INFO"):
    timestamp = datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] [{level}] [BOT] {message}"
    print(log_msg)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_msg + "\n")
    except Exception:
        pass


def log_error(message):
    log(message, "ERROR")
    try:
        with open(os.path.join(LOG_DIR, f"errors_{datetime.now(BD_TZ).strftime('%Y-%m-%d')}.log"), "a") as f:
            f.write(f"[{datetime.now(BD_TZ).strftime('%Y-%m-%d %H:%M:%S')}] {message}\n{traceback.format_exc()}\n{'='*50}\n")
    except Exception:
        pass


# ============================================
# GLOBAL STATE
# ============================================
application = None
_timer_tasks = {}        # {chat_id: asyncio.Task}
_poll_chat_map = {}      # {poll_id: chat_id}

# Pending MCQ source held in memory between "image/text received" and "type chosen".
# key -> {'kind': 'image'|'text', 'image_bytes': bytes|None, 'file_id': str|None,
#         'text': str|None, 'chat_id': int, 'user': dict}
_pending_sources = {}    # {source_key: {...}}

# In-progress /prompt edit sessions: {admin_id: prompt_key}
_prompt_edit_sessions = {}


# ============================================
# CF WORKER PROXY HELPERS
# (HF Space cannot reach api.telegram.org directly — everything via worker)
# ============================================
async def tg_api(method: str, payload: dict = None, files: dict = None):
    """Call Telegram Bot API method through the CF Worker proxy."""
    url = f"{CF_BOT_BASE}{BOT_TOKEN}/{method}"
    timeout = httpx.Timeout(90.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if files:
            resp = await client.post(url, data=payload or {}, files=files)
        else:
            resp = await client.post(url, json=payload or {})
        try:
            return resp.json()
        except Exception:
            return {"ok": False, "raw": resp.text[:300]}


async def tg_download_file(file_id: str) -> bytes:
    """Download a Telegram file's bytes via the worker file proxy."""
    info = await tg_api("getFile", {"file_id": file_id})
    if not info.get("ok"):
        raise Exception(f"getFile failed: {info}")
    file_path = info["result"]["file_path"]
    url = f"{CF_FILE_BASE}{BOT_TOKEN}/{file_path}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        r = await client.get(url)
        if r.status_code != 200:
            raise Exception(f"file download failed: {r.status_code}")
        return r.content


# ============================================
# SETUP
# ============================================
async def setup_bot():
    global application
    log("🚀 Setting up bot application...")
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .base_url(CF_BOT_BASE)
        .base_file_url(CF_FILE_BASE)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .build()
    )
    await register_handlers()
    await set_default_commands()
    asyncio.create_task(daily_reset_scheduler())
    log("✅ Bot setup complete!")


async def register_handlers():
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("all", cmd_all))
    application.add_handler(CommandHandler("bm", cmd_bm))
    # Admin commands
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
    application.add_handler(CommandHandler("send", cmd_send))
    application.add_handler(CommandHandler("prompt", cmd_prompt))
    # Poll answers (quiz engine)
    application.add_handler(PollAnswerHandler(handle_poll_answer))
    # Content
    application.add_handler(MessageHandler(filters.PHOTO, handle_image))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    # Callbacks
    application.add_handler(CallbackQueryHandler(handle_callback))
    log("✅ All handlers registered")


async def set_default_commands():
    """Default scope = USER commands only. Admin gets full list per-chat on /start."""
    user_cmds = [BotCommand(c, d) for c, d in USER_COMMANDS]
    try:
        await application.bot.set_my_commands(user_cmds, scope=BotCommandScopeDefault())
        log("✅ Default (user) commands set")
    except Exception as e:
        log_error(f"Failed to set default commands: {e}")


async def push_admin_commands(chat_id: int):
    """Give the admin the full command list, scoped to their own chat only."""
    admin_cmds = [BotCommand(c, d) for c, d in ADMIN_COMMANDS]
    try:
        await application.bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=chat_id))
        log(f"✅ Admin commands pushed to {chat_id}")
    except Exception as e:
        log_error(f"push_admin_commands error: {e}")


# ============================================
# DAILY RESET SCHEDULER
# ============================================
async def daily_reset_scheduler():
    log("⏰ Daily reset scheduler started")
    while True:
        now = datetime.now(BD_TZ)
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_seconds = (midnight - now).total_seconds()
        log(f"⏰ Next daily reset in {wait_seconds/3600:.1f} hours")
        await asyncio.sleep(wait_seconds)
        log("🔄 Running daily reset...")
        reset_daily_usage()
        log("✅ Daily reset complete!")


# ============================================
# HELPERS
# ============================================
def is_admin(user_id):
    return user_id == OWNER_ID


def get_user_info(update: Update):
    user = update.effective_user
    return {
        'user_id': user.id,
        'first_name': user.first_name or "User",
        'username': user.username or "",
    }


def get_feedback(percentage):
    if percentage >= 90:
        return random.choice(FEEDBACKS['excellent'])
    elif percentage >= 75:
        return random.choice(FEEDBACKS['good'])
    elif percentage >= 50:
        return random.choice(FEEDBACKS['average'])
    else:
        return random.choice(FEEDBACKS['poor'])


def apply_tag_exp(mcqs):
    """Attach global tag/exp to each MCQ (used by poll/quiz formatting)."""
    tag = get_setting('quiz_tag', '')
    exp_text = get_setting('quiz_exp', '')
    if not tag and not exp_text:
        return [dict(m) for m in mcqs]
    result = []
    for mcq in mcqs:
        m = dict(mcq)
        if tag:
            m['_tag'] = tag
        if exp_text:
            m['_exp'] = exp_text
        result.append(m)
    return result


def format_poll_question(mcq, q_num):
    tag = mcq.get('_tag', '')
    q = mcq['question']
    # Telegram polls only render \n\n as a visible break (platform limitation)
    text = f"[{tag}]\n\n{q_num}. {q}" if tag else f"{q_num}. {q}"
    return text[:300]


def format_explanation(mcq):
    exp = mcq.get('explanation', 'ব্যাখ্যা পাওয়া যায়নি')
    suffix = mcq.get('_exp', '')
    text = f"{exp}\n\n📌 {suffix}" if suffix else exp
    return text[:200]


def build_caption(first_name: str, practice_no: int, count: int) -> str:
    """স্বাগতম caption with random motivational ayat."""
    ayat = random.choice(MOTIVATION_AYATS)
    return WELCOME_CAPTION.format(
        first_name=first_name,
        practice_no=str(practice_no).zfill(2),
        count=count,
        ayat=ayat,
    )


def main_buttons(quiz_id: str) -> InlineKeyboardMarkup:
    """The 3 inline buttons attached under the pinned source image."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 New Poll", callback_data=f"poll_{quiz_id}"),
            InlineKeyboardButton("📝 New Quiz", callback_data=f"quiz_{quiz_id}"),
        ],
        [
            InlineKeyboardButton("🌐 Web Exam", url=create_exam_link(quiz_id, get_mcq(quiz_id)['mcqs'] if get_mcq(quiz_id) else [])),
        ],
    ])


def type_choice_buttons(source_key: str) -> InlineKeyboardMarkup:
    """4 MCQ-type options shown right after an image/text is received."""
    rows = [
        [InlineKeyboardButton(label, callback_data=f"gen_{key}_{source_key}")]
        for key, label in PROMPT_TYPES
    ]
    return InlineKeyboardMarkup(rows)


# ============================================
# COMMAND: /start  (scope-aware)
# ============================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    log(f"📱 /start from {user['user_id']} ({user['first_name']})")
    create_user(user['user_id'], user['first_name'], user['username'])

    if is_admin(user['user_id']):
        await push_admin_commands(user['user_id'])

    allowed, usage, limit, is_perm = check_access(user['user_id'])
    status = "✅ Permitted" if is_perm else "🔒 Free"

    welcome = (
        f"Assalamu Alaikum 🌙\n"
        f"ATLAS এ আপনাকে স্বাগতম, dear {user['first_name']}!\n\n"
        f"একটি Image অথবা Text পাঠান —\n"
        f"আমি ৪টি স্টাইলে MCQ Practice Tool বানিয়ে দিবো।\n\n"
        f"📊 আজকের ব্যবহার: {usage}/{limit}\n"
        f"📋 Status: {status}\n\n"
        f"কমান্ড:\n"
        f"/all - আপনার সব তৈরি করা MCQ\n"
        f"/bm - বুকমার্ক করা প্রশ্নের PDF"
    )
    await update.message.reply_text(welcome)


# ============================================
# COMMAND: /all
# ============================================
async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    log(f"📚 /all from {user['user_id']}")
    mcqs_data = get_user_mcqs(user['user_id'])
    if not mcqs_data:
        await update.message.reply_text("📭 আপনার কোনো সংরক্ষিত MCQ নেই।")
        return
    await update.message.reply_text(f"📚 আপনার মোট {len(mcqs_data)} টি MCQ সেট আছে।")
    for i, mcq_data in enumerate(mcqs_data):
        try:
            mcqs = mcq_data['mcqs']
            source_type = mcq_data.get('source_type', 'text')
            quiz_id = mcq_data['quiz_id']
            count = len(mcqs)
            created = mcq_data.get('created_at', 'Unknown')
            text = (
                f"📦 MCQ Set #{i+1}\n"
                f"📝 {count} টি প্রশ্ন\n"
                f"📅 {created[:10] if created else 'Unknown'}\n"
                f"🔄 Type: {source_type}"
            )
            keyboard = [
                [
                    InlineKeyboardButton("📊 Poll", callback_data=f"poll_{quiz_id}"),
                    InlineKeyboardButton("📝 Quiz", callback_data=f"quiz_{quiz_id}"),
                ],
                [InlineKeyboardButton("🌐 Web Exam", url=create_exam_link(quiz_id, mcqs))],
            ]
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            await asyncio.sleep(0.4)
        except Exception as e:
            log_error(f"Error showing MCQ set {i}: {e}")
            continue


# ============================================
# COMMAND: /bm
# ============================================
async def cmd_bm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    log(f"📑 /bm from {user['user_id']}")
    await update.message.reply_text(
        "🔖 বুকমার্ক PDF:\n\n"
        "Web Exam এ গিয়ে প্রশ্ন বুকমার্ক করুন।\n"
        "Exam শেষে 'Solve PDF' বাটন থেকে বুকমার্ক করা প্রশ্নের detail PDF ডাউনলোড করতে পারবেন।"
    )


# ============================================
# ADMIN COMMANDS
# ============================================
async def _require_admin(update: Update) -> bool:
    uid = update.effective_user.id
    if uid != OWNER_ID:
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return False
    return True


async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    report = get_usage_report()
    if not report:
        await update.message.reply_text("📊 কোনো ইউজার ডাটা নেই।")
        return
    text = "📊 *User Usage Report*\n\n"
    for i, row in enumerate(report[:20], 1):
        text += f"{i}. {row['first_name']} - {row['usage']}/{row['limit']} {row['status']}\n"
    if len(report) > 20:
        text += f"\n... আরো {len(report)-20} জন ইউজার"
    text += f"\n\n🔄 Total Users: {len(report)}"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_permit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /permit <user_id> বা /permit remove <user_id>")
        return
    if args[0].lower() == 'remove' and len(args) > 1:
        target_id = int(args[1])
        unpermit_user(target_id)
        await update.message.reply_text(f"❌ User {target_id} permit removed.")
    else:
        target_id = int(args[0])
        permit_user(target_id)
        await update.message.reply_text(f"✅ User {target_id} permitted!")


async def cmd_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /limit <count> বা /limit <user_id> <count>")
        return
    if len(args) == 1:
        count = int(args[0])
        set_setting('daily_limit', count)
        await update.message.reply_text(f"✅ সবার daily limit {count} সেট করা হয়েছে।")
    elif len(args) == 2:
        target_id = int(args[0])
        count = int(args[1])
        set_user_limit(target_id, count)
        await update.message.reply_text(f"✅ User {target_id} এর limit {count} সেট করা হয়েছে।")


async def cmd_free(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /free <count>")
        return
    set_setting('free_limit', int(args[0]))
    await update.message.reply_text(f"✅ Free users {args[0]} বার use করতে পারবে।")


async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /daily <count>")
        return
    set_setting('daily_limit', int(args[0]))
    await update.message.reply_text(f"✅ Permitted users দৈনিক {args[0]} বার use করতে পারবে।")


async def cmd_setneg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /setneg <value>\nExample: /setneg -0.25")
        return
    set_setting('negative_mark', float(args[0]))
    await update.message.reply_text(f"✅ Negative mark {args[0]} সেট করা হয়েছে।")


async def cmd_settimer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /settimer <seconds>\nExample: /settimer 35")
        return
    set_setting('timer_seconds', int(args[0]))
    await update.message.reply_text(f"✅ Quiz timer {args[0]} seconds সেট করা হয়েছে।")


async def cmd_tag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    args = context.args
    if not args:
        current = get_setting('quiz_tag', '')
        if current:
            await update.message.reply_text(f"📌 Current tag: [{current}]\n\nRemove করতে: /tag off")
        else:
            await update.message.reply_text("📌 কোনো tag সেট নেই।\nUsage: /tag ExamName")
        return
    if args[0].lower() == 'off':
        set_setting('quiz_tag', '')
        await update.message.reply_text("✅ Tag remove করা হয়েছে।")
    else:
        tag = ' '.join(args)
        set_setting('quiz_tag', tag)
        await update.message.reply_text(f"✅ Tag সেট: [{tag}]")


async def cmd_exp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    args = context.args
    if not args:
        current = get_setting('quiz_exp', '')
        if current:
            await update.message.reply_text(f"📝 Current exp: {current}\n\nRemove করতে: /exp off")
        else:
            await update.message.reply_text("📝 কোনো exp text সেট নেই।\nUsage: /exp ExamName")
        return
    if args[0].lower() == 'off':
        set_setting('quiz_exp', '')
        await update.message.reply_text("✅ Exp text remove করা হয়েছে।")
    else:
        exp_text = ' '.join(args)
        set_setting('quiz_exp', exp_text)
        await update.message.reply_text(f"✅ Exp text সেট: {exp_text}")


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    error_file = os.path.join(LOG_DIR, f"errors_{datetime.now(BD_TZ).strftime('%Y-%m-%d')}.log")
    try:
        if os.path.exists(error_file):
            with open(error_file, "r") as f:
                lines = f.readlines()
            text = "📋 Recent Errors:\n\n" + "".join(lines[-10:]) if lines else "✅ No errors today!"
            await update.message.reply_text(text[-4000:])
        else:
            await update.message.reply_text("✅ আজ কোনো error নেই!")
    except Exception as e:
        await update.message.reply_text(f"❌ Log read error: {e}")


# ============================================
# COMMAND: /send  (broadcast any text/photo to all users)
# Usage: reply to a message (text or photo) with /send
# ============================================
async def cmd_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return

    reply = update.message.reply_to_message
    inline_text = ' '.join(context.args) if context.args else ''

    if not reply and not inline_text:
        await update.message.reply_text(
            "📢 Broadcast:\n"
            "• কোনো মেসেজ/ছবিতে reply দিয়ে /send লিখুন, অথবা\n"
            "• /send <আপনার টেক্সট>"
        )
        return

    users = get_all_users()
    if not users:
        await update.message.reply_text("❌ কোনো ইউজার নেই।")
        return

    status_msg = await update.message.reply_text(f"📤 {len(users)} জন ইউজারকে পাঠানো হচ্ছে...")

    # Resolve payload
    photo_id = None
    caption = inline_text
    body_text = inline_text
    if reply:
        if reply.photo:
            photo_id = reply.photo[-1].file_id
            caption = reply.caption or inline_text or ''
        elif reply.text:
            body_text = reply.text

    sent, failed = 0, 0
    for u in users:
        uid = u['user_id']
        try:
            if photo_id:
                await application.bot.send_photo(chat_id=uid, photo=photo_id, caption=caption or None)
            else:
                await application.bot.send_message(chat_id=uid, text=body_text)
            sent += 1
        except Exception as e:
            failed += 1
            log_error(f"Broadcast to {uid} failed: {e}")
        await asyncio.sleep(0.05)  # gentle rate limit

    await status_msg.edit_text(f"✅ Broadcast সম্পন্ন!\n📨 Sent: {sent}\n❌ Failed: {failed}")


# ============================================
# COMMAND: /prompt  (admin prompt editor — DB backed)
# ============================================
async def cmd_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    _prompt_edit_sessions.pop(update.effective_user.id, None)  # clear any pending edit
    rows = []
    for key, label in PROMPT_TYPES:
        rows.append([
            InlineKeyboardButton(f"👁 {label}", callback_data=f"pview_{key}"),
            InlineKeyboardButton("✏️ Edit", callback_data=f"pedit_{key}"),
        ])
    await update.message.reply_text(
        "🧠 *Prompt Manager*\n\nএকটি prompt দেখতে 👁 বা পরিবর্তন করতে ✏️ চাপুন।\n"
        "Edit এ গিয়ে নতুন prompt পাঠালেই সাথে সাথে save + apply হবে।",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.MARKDOWN,
    )


# ============================================
# HANDLE IMAGE  → show 4 type options
# ============================================
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    log(f"🖼️ Image from {user['user_id']}")
    allowed, usage, limit, is_perm = check_access(user['user_id'])
    if not allowed:
        await update.message.reply_text(
            f"❌ আপনার আজকের লিমিট ({limit}) শেষ।" if is_perm else PREMIUM_MSG
        )
        return

    try:
        photo = update.message.photo[-1]
        file_id = photo.file_id
        image_bytes = await tg_download_file(file_id)
        if not image_bytes:
            await update.message.reply_text("❌ ইমেজ ডাউনলোড করতে সমস্যা হয়েছে। আবার চেষ্টা করুন।")
            return

        source_key = f"{user['user_id']}_{int(time.time())}"
        _pending_sources[source_key] = {
            'kind': 'image',
            'image_bytes': image_bytes,
            'file_id': file_id,
            'text': None,
            'chat_id': update.effective_chat.id,
            'user': user,
        }
        await update.message.reply_text(
            "🎯 কোন স্টাইলে MCQ বানাবো? নিচ থেকে একটি বেছে নিন 👇",
            reply_markup=type_choice_buttons(source_key),
        )
    except Exception as e:
        log_error(f"Image handler error: {e}")
        await update.message.reply_text(f"❌ একটা সমস্যা হয়েছে। আবার চেষ্টা করুন।\nError: {str(e)[:100]}")


# ============================================
# HANDLE TEXT  → validate length, then show 4 type options
# ============================================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    text = update.message.text or ""

    # If admin is mid prompt-edit, capture this text as the new prompt
    if user['user_id'] == OWNER_ID and user['user_id'] in _prompt_edit_sessions:
        pkey = _prompt_edit_sessions.pop(user['user_id'])
        set_prompt(pkey, text)
        label = dict(PROMPT_TYPES).get(pkey, pkey)
        await update.message.reply_text(
            f"✅ *{label}* prompt আপডেট হয়েছে এবং এখন থেকে apply হবে।\n\n"
            f"📏 দৈর্ঘ্য: {len(text)} chars",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    log(f"📝 Text from {user['user_id']} ({len(text)} chars)")

    # Reject too-short text (1-2 lines / not enough info)
    line_count = len([ln for ln in text.splitlines() if ln.strip()])
    if len(text.strip()) < MIN_TEXT_LEN or line_count <= 1:
        await update.message.reply_text(NO_INFO_MSG)
        return

    allowed, usage, limit, is_perm = check_access(user['user_id'])
    if not allowed:
        await update.message.reply_text(
            f"❌ আপনার আজকের লিমিট ({limit}) শেষ।" if is_perm else PREMIUM_MSG
        )
        return

    source_key = f"{user['user_id']}_{int(time.time())}"
    _pending_sources[source_key] = {
        'kind': 'text',
        'image_bytes': None,
        'file_id': None,
        'text': text,
        'chat_id': update.effective_chat.id,
        'user': user,
    }
    await update.message.reply_text(
        "🎯 কোন স্টাইলে MCQ বানাবো? নিচ থেকে একটি বেছে নিন 👇",
        reply_markup=type_choice_buttons(source_key),
    )


# ============================================
# GENERATE MCQ (after type chosen) → caption + pin + buttons
# ============================================
async def run_generation(query, prompt_key: str, source_key: str):
    src = _pending_sources.get(source_key)
    if not src:
        await query.message.reply_text("⏳ সেশন expire হয়ে গেছে। আবার Image/Text পাঠান।")
        return

    user = src['user']
    chat_id = src['chat_id']

    allowed, usage, limit, is_perm = check_access(user['user_id'])
    if not allowed:
        await query.message.reply_text(
            f"❌ আপনার আজকের লিমিট ({limit}) শেষ।" if is_perm else PREMIUM_MSG
        )
        return

    label = dict(PROMPT_TYPES).get(prompt_key, prompt_key)
    eta = random.randint(5, 12)
    end_time = (datetime.now(BD_TZ) + timedelta(seconds=eta)).strftime("%I:%M %p")
    proc = PROCESSING_MSG.format(
        first_name=user['first_name'], attempt=usage + 1, limit=limit,
        eta=eta, end_time=end_time, style=label,
    )
    try:
        await query.edit_message_text(proc)
    except Exception:
        pass

    prompt_text = get_prompt(prompt_key)

    try:
        if src['kind'] == 'image':
            mcqs, error = await mcq_generator.generate_from_image(src['image_bytes'], prompt_override=prompt_text)
        else:
            mcqs, error = await mcq_generator.generate_from_text(src['text'], prompt_override=prompt_text)

        if error or not mcqs:
            await query.message.reply_text(f"❌ MCQ বানাতে সমস্যা: {error or 'empty'}\nআবার চেষ্টা করুন।")
            return

        quiz_id = save_mcq(user['user_id'], mcqs, src['kind'])
        # Keep the source image file_id tied to this quiz (for pre-msg + back-to-image)
        if src['file_id']:
            save_source_image(quiz_id, src['file_id'], user['user_id'])

        new_usage = increment_usage(user['user_id'])
        practice_no = increment_practice_no(user['user_id'])

        store_exam(quiz_id, mcqs)  # warm exam cache

        caption = build_caption(user['first_name'], practice_no, len(mcqs))
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 New Poll", callback_data=f"poll_{quiz_id}"),
                InlineKeyboardButton("📝 New Quiz", callback_data=f"quiz_{quiz_id}"),
            ],
            [InlineKeyboardButton("🌐 Web Exam", url=create_exam_link(quiz_id, mcqs))],
        ])

        try:
            await query.delete_message()
        except Exception:
            pass

        if src['file_id']:
            sent = await application.bot.send_photo(
                chat_id=chat_id, photo=src['file_id'], caption=caption, reply_markup=kb
            )
            try:
                await application.bot.pin_chat_message(
                    chat_id=chat_id, message_id=sent.message_id, disable_notification=True
                )
            except Exception as e:
                log_error(f"pin failed: {e}")
        else:
            await application.bot.send_message(chat_id=chat_id, text=caption, reply_markup=kb)

        log(f"✅ MCQ generated [{prompt_key}]: {quiz_id} ({len(mcqs)} q), usage={new_usage}")
    except Exception as e:
        log_error(f"run_generation error: {e}")
        await query.message.reply_text(f"❌ একটা সমস্যা হয়েছে।\nError: {str(e)[:100]}")
    finally:
        _pending_sources.pop(source_key, None)


# ============================================
# PRE-MESSAGE (source image + caption) before Quiz/Poll
# ============================================
async def send_pre_message(chat_id: int, quiz_id: str, mode_label: str):
    """Send the source image with caption right before a Quiz/Poll starts."""
    img = get_source_image(quiz_id)
    cap = f"📌 {mode_label} শুরু হচ্ছে...\n\nএই ছবি থেকেই প্রশ্নগুলো তৈরি 👇"
    try:
        if img and img.get('file_id'):
            await application.bot.send_photo(chat_id=chat_id, photo=img['file_id'], caption=cap)
        else:
            await application.bot.send_message(chat_id=chat_id, text=cap)
    except Exception as e:
        log_error(f"send_pre_message error: {e}")


# ============================================
# CALLBACK ROUTER
# ============================================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = query.from_user
    chat_id = query.message.chat_id
    log(f"🔘 Callback: {data} from {user.id}")
    await query.answer()
    try:
        if data.startswith("gen_"):
            # gen_<promptkey>_<sourcekey>
            rest = data[len("gen_"):]
            prompt_key, source_key = rest.split("_", 1)
            await run_generation(query, prompt_key, source_key)

        elif data.startswith("poll_"):
            quiz_id = data.replace("poll_", "")
            await send_pre_message(chat_id, quiz_id, "📊 Poll Practice")
            await handle_poll_solve(query, quiz_id, user)

        elif data.startswith("quiz_"):
            quiz_id = data.replace("quiz_", "")
            await send_pre_message(chat_id, quiz_id, "📝 Quiz Practice")
            await handle_quiz_start(query, quiz_id, user, chat_id)

        elif data.startswith("startquiz_"):
            await send_first_question(query, chat_id)

        elif data.startswith("again_"):
            quiz_id = data.replace("again_", "")
            await send_pre_message(chat_id, quiz_id, "🔄 Again Practice")
            await handle_poll_solve(query, quiz_id, user)

        elif data.startswith("newp_"):
            await handle_new_practice(query, user, 'poll')

        elif data.startswith("newq_"):
            await handle_new_practice(query, user, 'quiz')

        elif data.startswith("retake_"):
            quiz_id = data.replace("retake_", "")
            await send_pre_message(chat_id, quiz_id, "🔄 Retake")
            await handle_quiz_start(query, quiz_id, user, chat_id)

        elif data.startswith("backimg_"):
            quiz_id = data.replace("backimg_", "")
            await send_pre_message(chat_id, quiz_id, "⚡ Back to Main Image")

        # ---- /prompt callbacks ----
        elif data.startswith("pview_"):
            pkey = data.replace("pview_", "")
            ptext = get_prompt(pkey)
            label = dict(PROMPT_TYPES).get(pkey, pkey)
            await query.message.reply_text(f"👁 *{label}*\n\n```\n{ptext[:3500]}\n```", parse_mode=ParseMode.MARKDOWN)

        elif data.startswith("pedit_"):
            pkey = data.replace("pedit_", "")
            if user.id != OWNER_ID:
                return
            _prompt_edit_sessions[user.id] = pkey
            label = dict(PROMPT_TYPES).get(pkey, pkey)
            await query.message.reply_text(
                f"✏️ *{label}* edit মোডে আছেন।\n\nএখন নতুন prompt টেক্সট পাঠান — পাঠালেই save + apply হবে।\n"
                f"বাতিল করতে /prompt আবার দিন।",
                parse_mode=ParseMode.MARKDOWN,
            )

    except Exception as e:
        log_error(f"Callback error: {e}")
        try:
            await query.message.reply_text(f"❌ Error: {str(e)[:100]}")
        except Exception:
            pass


# ============================================
# POLL SOLVE (batch quiz polls)
# ============================================
async def handle_poll_solve(query, quiz_id, user):
    log(f"📊 Poll solve: {quiz_id}")
    mcq_data = get_mcq(quiz_id)
    if not mcq_data:
        await query.message.reply_text("❌ MCQ data পাওয়া যায়নি।")
        return
    mcqs = apply_tag_exp(mcq_data['mcqs'])
    total = len(mcqs)
    timer = int(get_all_settings().get('timer_seconds', DEFAULT_TIMER))
    await query.message.chat.send_message(f"📊 {total} টি Poll পাঠানো হচ্ছে...")
    for i, mcq in enumerate(mcqs):
        try:
            options = mcq['options'][:4]
            correct_id = mcq.get('answer', 0)
            if correct_id >= len(options):
                correct_id = 0
            await query.message.chat.send_poll(
                question=format_poll_question(mcq, i + 1),
                options=options,
                type=Poll.QUIZ,
                correct_option_id=correct_id,
                explanation=format_explanation(mcq),
                is_anonymous=True,
            )
            if i < total - 1:
                await asyncio.sleep(POLL_DELAY)
        except Exception as e:
            log_error(f"Poll {i+1} send error: {e}")
            continue
    await _send_result_buttons(query.message.chat.id, quiz_id, total_only=total)


async def _send_result_buttons(chat_id, quiz_id, total_only=None):
    """Common 'after a practice' button block (poll mode)."""
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Again Practice", callback_data=f"again_{quiz_id}"),
            InlineKeyboardButton("🆕 New Poll (15)", callback_data=f"newp_{quiz_id}"),
        ],
        [InlineKeyboardButton("⚡ Back to Main Image", callback_data=f"backimg_{quiz_id}")],
        [
            InlineKeyboardButton("🌐 ATLAS Website", url=ATLAS_WEBSITE),
            InlineKeyboardButton("▶️ ATLAS YouTube", url=ATLAS_YOUTUBE),
        ],
    ])
    msg = f"✅ Total {total_only} টি poll পাঠানো হয়েছে।" if total_only else "✅ সম্পন্ন!"
    await application.bot.send_message(chat_id=chat_id, text=msg, reply_markup=kb)


# ============================================
# QUIZ ENGINE (one-by-one with timer + auto next)
# ============================================
async def handle_quiz_start(query, quiz_id, user, chat_id):
    log(f"📝 Quiz start: {quiz_id}")
    mcq_data = get_mcq(quiz_id)
    if not mcq_data:
        await query.message.reply_text("❌ MCQ data পাওয়া যায়নি।")
        return
    mcqs = mcq_data['mcqs']
    random.shuffle(mcqs)
    mcqs = apply_tag_exp(mcqs)
    total = len(mcqs)
    settings = get_all_settings()
    timer = int(settings.get('timer_seconds', DEFAULT_TIMER))
    neg_mark = float(settings.get('negative_mark', DEFAULT_NEGATIVE_MARK))
    save_active_quiz(chat_id, {
        'quiz_id': quiz_id, 'mcqs': mcqs, 'current_index': 0, 'answers': {},
        'correct': 0, 'wrong': 0, 'skipped': 0, 'start_time': time.time(),
        'timer': timer, 'neg_mark': neg_mark, 'current_poll_id': None,
    })
    ready = (
        f"📝 *Quiz Ready!*\n\n"
        f"📋 Total: {total}\n⏱️ প্রতি প্রশ্ন: {timer}s\n📊 Negative: {neg_mark}\n\n"
        f"প্রস্তুত? শুরু করুন! 🚀"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Start Quiz", callback_data=f"startquiz_{quiz_id}")]])
    await query.message.chat.send_message(ready, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


async def send_first_question(query, chat_id):
    quiz = get_active_quiz(chat_id)
    if not quiz:
        await query.message.reply_text("❌ Quiz session expired। আবার শুরু করুন।")
        return
    quiz['current_index'] = 0
    await send_quiz_poll(chat_id)


async def send_quiz_poll(chat_id):
    quiz = get_active_quiz(chat_id)
    if not quiz:
        return
    idx = quiz['current_index']
    mcqs = quiz['mcqs']
    if idx >= len(mcqs):
        await end_quiz(chat_id)
        return
    mcq = mcqs[idx]
    timer = quiz['timer']
    options = mcq['options'][:4]
    correct_id = mcq.get('answer', 0)
    if correct_id >= len(options):
        correct_id = 0
    try:
        msg = await application.bot.send_poll(
            chat_id=chat_id,
            question=format_poll_question(mcq, idx + 1),
            options=options,
            type=Poll.QUIZ,
            correct_option_id=correct_id,
            explanation=format_explanation(mcq),
            is_anonymous=False,
            open_period=timer,
        )
        quiz['current_poll_id'] = msg.poll.id
        _poll_chat_map[msg.poll.id] = chat_id
        old = _timer_tasks.pop(chat_id, None)
        if old and not old.done():
            old.cancel()
        _timer_tasks[chat_id] = asyncio.create_task(_quiz_timer_task(chat_id, timer + 1))
        log(f"📊 Quiz poll sent Q{idx+1}/{len(mcqs)} chat={chat_id}")
    except Exception as e:
        log_error(f"Send quiz poll error: {e}")
        quiz['skipped'] += 1
        quiz['current_index'] += 1
        await send_quiz_poll(chat_id)


async def _quiz_timer_task(chat_id, delay):
    await asyncio.sleep(delay)
    quiz = get_active_quiz(chat_id)
    if not quiz:
        return
    log(f"⏱️ Timer expired chat={chat_id}, auto-next")
    quiz['skipped'] += 1
    quiz['current_index'] += 1
    await send_quiz_poll(chat_id)


async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    chat_id = _poll_chat_map.get(answer.poll_id)
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
    if answer.option_ids:
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
    await asyncio.sleep(0.8)
    await send_quiz_poll(chat_id)


async def end_quiz(chat_id):
    quiz = get_active_quiz(chat_id)
    if not quiz:
        return
    total = len(quiz['mcqs'])
    correct = quiz['correct']
    wrong = quiz['wrong']
    skipped = quiz['skipped']
    time_taken = int(time.time() - quiz['start_time'])
    neg_mark = abs(quiz['neg_mark'])
    penalty = wrong * neg_mark
    final_mark = correct - penalty
    percentage = (correct / total * 100) if total > 0 else 0
    feedback = get_feedback(percentage)
    ayat = random.choice(AYATS)
    mins, secs = time_taken // 60, time_taken % 60
    time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
    quiz_id = quiz['quiz_id']

    result_text = (
        f"🎯 *QUIZ RESULT*\n{'━'*22}\n"
        f"📝 Total: {total}\n✅ Right: {correct}\n❌ Wrong: {wrong}\n⏭️ Skipped: {skipped}\n"
        f"⏱️ Time: {time_str}\n{'━'*22}\n"
        f"📊 *Negative Mark:*\n❌ {wrong} × {neg_mark} = -{penalty:.2f}\n"
        f"📊 Final Mark: *{final_mark:.2f}/{total}*\n{'━'*22}\n{feedback}\n\n📖 _{ayat}_"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Again Practice", callback_data=f"retake_{quiz_id}"),
            InlineKeyboardButton("🆕 New Exam (15)", callback_data=f"newq_{quiz_id}"),
        ],
        [InlineKeyboardButton("⚡ Back to Main Image", callback_data=f"backimg_{quiz_id}")],
        [
            InlineKeyboardButton("🌐 ATLAS Website", url=ATLAS_WEBSITE),
            InlineKeyboardButton("▶️ ATLAS YouTube", url=ATLAS_YOUTUBE),
        ],
    ])
    try:
        save_result(
            user_id=chat_id, quiz_name=f"Quiz_{quiz_id[:6]}", total=total,
            right=correct, wrong=wrong, skipped=skipped, time_taken=time_taken,
            mark=final_mark, negative_mark=penalty,
        )
    except Exception as e:
        log_error(f"Save result error: {e}")
    try:
        await application.bot.send_message(
            chat_id=chat_id, text=result_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        log_error(f"Send result error: {e}")
    remove_active_quiz(chat_id)


# ============================================
# NEW PRACTICE  (fully NEW 15 MCQ from the SAME source image)
# ============================================
async def handle_new_practice(query, user, mode='poll'):
    chat_id = query.message.chat_id
    await query.message.chat.send_message("🆕 নতুন ১৫টি বেস্ট MCQ তৈরি হচ্ছে (একই ছবি থেকে)...")
    try:
        mcqs_data = get_user_mcqs(user.id)
        if not mcqs_data:
            await query.message.reply_text("❌ কোনো MCQ পাওয়া যায়নি।")
            return
        latest = mcqs_data[0]
        prev_quiz_id = latest['quiz_id']
        src_img = get_source_image(prev_quiz_id)

        new_mcqs = None
        # If the source image is available, regenerate FRESH MCQs from it
        if src_img and src_img.get('file_id'):
            try:
                img_bytes = await tg_download_file(src_img['file_id'])
                prompt_text = get_prompt('mixed')  # New Exam uses best/mixed by default
                gen, err = await mcq_generator.generate_from_image(
                    img_bytes, prompt_override=prompt_text, count=NEW_PRACTICE_COUNT
                )
                if gen and not err:
                    new_mcqs = gen
            except Exception as e:
                log_error(f"new-practice regen failed, falling back to reshuffle: {e}")

        # Fallback: reshuffle existing pool
        if not new_mcqs:
            pool = list(latest['mcqs'])
            random.shuffle(pool)
            new_mcqs = pool[:NEW_PRACTICE_COUNT]

        new_quiz_id = save_mcq(user.id, new_mcqs, 'newgen')
        if src_img and src_img.get('file_id'):
            save_source_image(new_quiz_id, src_img['file_id'], user.id)
        store_exam(new_quiz_id, new_mcqs)

        # Pre-message with the source image
        await send_pre_message(chat_id, new_quiz_id, "🆕 New Practice")

        if mode == 'quiz':
            settings = get_all_settings()
            timer = int(settings.get('timer_seconds', DEFAULT_TIMER))
            neg_mark = float(settings.get('negative_mark', DEFAULT_NEGATIVE_MARK))
            tagged = apply_tag_exp(new_mcqs)
            random.shuffle(tagged)
            save_active_quiz(chat_id, {
                'quiz_id': new_quiz_id, 'mcqs': tagged, 'current_index': 0, 'answers': {},
                'correct': 0, 'wrong': 0, 'skipped': 0, 'start_time': time.time(),
                'timer': timer, 'neg_mark': neg_mark, 'current_poll_id': None,
            })
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Start Quiz", callback_data=f"startquiz_{new_quiz_id}")]])
            await query.message.chat.send_message(
                f"✅ {len(new_mcqs)} টি নতুন MCQ রেডি!\n\n[▶️ Start Quiz] চাপুন।", reply_markup=kb
            )
        else:
            class _Q:
                message = query.message
            await handle_poll_solve(_Q(), new_quiz_id, user)
    except Exception as e:
        log_error(f"New practice error: {e}")
        await query.message.reply_text(f"❌ Error: {str(e)[:100]}")


# ============================================
# WEBHOOK ROUTE (registered on the Flask app from exam_server)
# ============================================
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
            asyncio.run_coroutine_threadsafe(
                application.process_update(update), _bot_loop
            )
        return PlainTextResponse('OK')

# ============================================
# MAIN
# ============================================
async def main():
    global _bot_loop
    _bot_loop = asyncio.get_event_loop()

    log("=" * 60)
    log("🚀 ATLAS MCQ BOT STARTING (WEBHOOK MODE) v2.0")
    log("=" * 60)

    log("📦 Initializing database...")
    init_database()

    log("🤖 Setting up bot...")
    await setup_bot()

    await application.initialize()
    await application.start()

    webhook_url = f"{CF_WORKER_URL}/webhook/{BOT_TOKEN}"
    try:
        await application.bot.set_webhook(
            url=webhook_url,
            allowed_updates=["message", "callback_query", "poll_answer", "poll"],
            drop_pending_updates=True,
            max_connections=40,
        )
        log(f"✅ Webhook set: {webhook_url}")
    except Exception as e:
        log_error(f"Webhook set failed: {e}")

    # পুরো শেষ অংশ এভাবে replace করো:

    from exam_server import app as fastapi_app
    setup_webhook_route(fastapi_app)

    log("🌐 Starting exam+webhook server on port 7860...")
    import uvicorn
    config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=7860, log_level="warning")
    server = uvicorn.Server(config)
    
    log("✅ Bot is running in webhook mode!")
    await server.serve()  # ← asyncio.Event().wait() বাদ দাও, এটাই block করবে