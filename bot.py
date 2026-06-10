"""
ATLAS MCQ BOT - Main Telegram Bot
All handlers, commands, quiz engine, poll sender
"""

import asyncio
import json
import time
import traceback
import random
import threading
import uuid
from datetime import datetime, timedelta
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    Poll, BotCommand, BotCommandScopeDefault
)
from telegram.ext import (
    Application, ApplicationBuilder,
    CommandHandler, MessageHandler, CallbackQueryHandler,
    PollAnswerHandler, filters, ContextTypes
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

from config import (
    BOT_TOKEN, OWNER_ID, BASE_URL, CF_WORKER_URL,
    DEFAULT_TIMER, DEFAULT_FREE_LIMIT, DEFAULT_DAILY_LIMIT,
    DEFAULT_NEGATIVE_MARK, MAX_MCQ, MIN_MCQ, NEW_PRACTICE_COUNT,
    POLL_DELAY, BD_TZ, AYATS, FEEDBACKS,
    PROCESSING_MSG, PREMIUM_MSG, LOG_DIR
)
from database import (
    create_user, get_user, update_user, is_permitted,
    permit_user, unpermit_user, get_all_users,
    get_setting, set_setting, get_all_settings,
    get_user_limit, set_user_limit,
    save_mcq, get_mcq, get_user_mcqs,
    save_result, get_user_results,
    add_bookmark, get_bookmarks, delete_bookmark,
    get_today_usage, increment_usage, get_usage_report,
    check_access, reset_daily_usage, init_database,
    save_active_quiz, get_active_quiz, remove_active_quiz
)
from gemini_mcq import mcq_generator, download_image, get_file_url
from exam_server import create_exam_link

import os

# ============================================
# LOGGING
# ============================================
LOG_FILE = os.path.join(LOG_DIR, f"bot_{datetime.now(BD_TZ).strftime('%Y-%m-%d')}.log")

def log(message, level="INFO"):
    timestamp = datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] [{level}] [BOT] {message}"
    print(log_msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_msg + "\n")

def log_error(message):
    log(message, "ERROR")
    with open(os.path.join(LOG_DIR, f"errors_{datetime.now(BD_TZ).strftime('%Y-%m-%d')}.log"), "a") as f:
        f.write(f"[{datetime.now(BD_TZ).strftime('%Y-%m-%d %H:%M:%S')}] {message}\n{traceback.format_exc()}\n{'='*50}\n")

# ============================================
# BOT APPLICATION + QUIZ STATE
# ============================================
application = None
_timer_tasks = {}    # {chat_id: asyncio.Task}
_poll_chat_map = {}  # {poll_id: chat_id}

# ============================================
# SETUP
# ============================================
async def setup_bot():
    global application
    log("🚀 Setting up bot application...")
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .base_url("https://atlas-bot-proxy.hamza818483.workers.dev/bot")
        .base_file_url("https://atlas-bot-proxy.hamza818483.workers.dev/file/bot")
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .build()
    )
    await register_handlers()
    await set_bot_commands()
    asyncio.create_task(daily_reset_scheduler())
    log("✅ Bot setup complete!")

async def register_handlers():
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("all", cmd_all))
    application.add_handler(CommandHandler("bm", cmd_bm))
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
    application.add_handler(PollAnswerHandler(handle_poll_answer))
    application.add_handler(MessageHandler(filters.PHOTO, handle_image))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CallbackQueryHandler(handle_callback))
    log("✅ All handlers registered")

async def set_bot_commands():
    commands = [
        BotCommand("start", "শুরু করুন"),
        BotCommand("all", "আপনার সব তৈরি MCQ দেখুন"),
        BotCommand("bm", "বুকমার্ক PDF ডাউনলোড"),
        BotCommand("info", "ইউজার রিপোর্ট (এডমিন)"),
        BotCommand("permit", "ইউজার পারমিট (এডমিন)"),
        BotCommand("limit", "ডেইলি লিমিট সেট (এডমিন)"),
        BotCommand("free", "ফ্রি ট্রাই সেট (এডমিন)"),
        BotCommand("daily", "পারমিটেড লিমিট (এডমিন)"),
        BotCommand("setneg", "নেগেটিভ মার্ক (এডমিন)"),
        BotCommand("settimer", "টাইমার সেট (এডমিন)"),
        BotCommand("tag", "Quiz/Poll tag সেট (এডমিন)"),
        BotCommand("exp", "Explanation suffix সেট (এডমিন)"),
        BotCommand("log", "এরর লগ (এডমিন)"),
    ]
    try:
        await application.bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        log("✅ Bot commands set")
    except Exception as e:
        log_error(f"Failed to set commands: {e}")

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
        'username': user.username or ""
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
    """Apply global tag/exp to MCQ list"""
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

def format_poll_question(mcq, q_num):
    """Format poll question with tag"""
    tag = mcq.get('_tag', '')
    q = mcq['question']
    text = f"[{tag}]\n\n{q_num}. {q}" if tag else f"{q_num}. {q}"
    return text[:300]

def format_explanation(mcq):
    """Format explanation with exp suffix"""
    exp = mcq.get('explanation', 'ব্যাখ্যা পাওয়া যায়নি')
    suffix = mcq.get('_exp', '')
    text = f"{exp}\n\n📌 {suffix}" if suffix else exp
    return text[:200]

# ============================================
# COMMAND: /start
# ============================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    log(f"📱 /start from {user['user_id']} ({user['first_name']})")
    create_user(user['user_id'], user['first_name'], user['username'])
    allowed, usage, limit, is_perm = check_access(user['user_id'])
    status = "✅ Permitted" if is_perm else "🔒 Free"
    welcome = f"""
Assalamu Alaikum 🌙
Atlas এ আপনাকে স্বাগতম, dear {user['first_name']}!

একটি Image অথবা Text পাঠান —
আমি সাথে সাথে MCQ বানিয়ে দিবো।

📊 আজকের ব্যবহার: {usage}/{limit}
📋 Status: {status}

কমান্ডসমূহ:
/all - আপনার সব তৈরি করা MCQ দেখুন
/bm - বুকমার্ক করা MCQ এর PDF ডাউনলোড
"""
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
    await update.message.reply_text(f"📚 আপনার মোট {len(mcqs_data)} টি MCQ সেট আছে। লোড হচ্ছে...")
    for i, mcq_data in enumerate(mcqs_data):
        try:
            mcqs = mcq_data['mcqs']
            source_type = mcq_data.get('source_type', 'text')
            quiz_id = mcq_data['quiz_id']
            count = len(mcqs)
            created = mcq_data.get('created_at', 'Unknown')
            text = f"📦 MCQ Set #{i+1}\n📝 {count} টি প্রশ্ন\n📅 {created[:10] if created else 'Unknown'}\n🔄 Type: {source_type}"
            keyboard = [
                [
                    InlineKeyboardButton("📊 Poll Solve", callback_data=f"poll_{quiz_id}"),
                    InlineKeyboardButton("📝 Quiz Solve", callback_data=f"quiz_{quiz_id}"),
                ],
                [
                    InlineKeyboardButton("🌐 Website Exam", url=f"https://hamzaHF1-atlasbot.hf.space/exam/{quiz_id}")
                ]
            ]
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            await asyncio.sleep(0.5)
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
        "🔖 বুকমার্ক PDF ফিচার শীঘ্রই আসছে!\n\n"
        "Website Exam এ গিয়ে প্রশ্ন বুকমার্ক করতে পারবেন।\n"
        "তারপর /bm দিয়ে PDF ডাউনলোড করতে পারবেন।"
    )

# ============================================
# COMMAND: /info (Admin)
# ============================================
async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
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

# ============================================
# COMMAND: /permit (Admin)
# ============================================
async def cmd_permit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
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

# ============================================
# COMMAND: /limit (Admin)
# ============================================
async def cmd_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
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

# ============================================
# COMMAND: /free (Admin)
# ============================================
async def cmd_free(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /free <count>")
        return
    count = int(args[0])
    set_setting('free_limit', count)
    await update.message.reply_text(f"✅ Free users {count} বার use করতে পারবে।")

# ============================================
# COMMAND: /daily (Admin)
# ============================================
async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /daily <count>")
        return
    count = int(args[0])
    set_setting('daily_limit', count)
    await update.message.reply_text(f"✅ Permitted users দৈনিক {count} বার use করতে পারবে।")

# ============================================
# COMMAND: /setneg (Admin)
# ============================================
async def cmd_setneg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /setneg <value>\nExample: /setneg -0.50")
        return
    value = float(args[0])
    set_setting('negative_mark', value)
    await update.message.reply_text(f"✅ Negative mark {value} সেট করা হয়েছে।")

# ============================================
# COMMAND: /settimer (Admin)
# ============================================
async def cmd_settimer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /settimer <seconds>\nExample: /settimer 30")
        return
    seconds = int(args[0])
    set_setting('timer_seconds', seconds)
    await update.message.reply_text(f"✅ Quiz timer {seconds} seconds সেট করা হয়েছে।")

# ============================================
# COMMAND: /tag (Admin)
# ============================================
async def cmd_tag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
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
        await update.message.reply_text(f"✅ Tag সেট: [{tag}]\n\nসব Quiz/Poll/Exam এ দেখাবে।")

# ============================================
# COMMAND: /exp (Admin)
# ============================================
async def cmd_exp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
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
        await update.message.reply_text(f"✅ Exp text সেট: {exp_text}\n\nসব Explanation এর শেষে যোগ হবে।")

# ============================================
# COMMAND: /log (Admin)
# ============================================
async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু এডমিন ব্যবহার করতে পারবেন।")
        return
    error_file = os.path.join(LOG_DIR, f"errors_{datetime.now(BD_TZ).strftime('%Y-%m-%d')}.log")
    try:
        if os.path.exists(error_file):
            with open(error_file, "r") as f:
                lines = f.readlines()
            text = "📋 Recent Errors:\n\n" + "".join(lines[-10:]) if lines else "✅ No errors today!"
            if len(text) > 4000:
                text = text[-4000:]
            await update.message.reply_text(text)
        else:
            await update.message.reply_text("✅ আজ কোনো error নেই!")
    except Exception as e:
        await update.message.reply_text(f"❌ Log read error: {e}")

# ============================================
# HANDLE IMAGE MESSAGE
# ============================================
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    log(f"🖼️ Image from {user['user_id']}")
    allowed, usage, limit, is_perm = check_access(user['user_id'])
    if not allowed:
        if is_perm:
            await update.message.reply_text(f"❌ আপনার আজকের লিমিট ({limit}) শেষ। আগামীকাল আবার চেষ্টা করুন।")
        else:
            await update.message.reply_text(PREMIUM_MSG)
        return
    eta = random.randint(5, 12)
    end_time = (datetime.now(BD_TZ) + timedelta(seconds=eta)).strftime("%I:%M %p")
    processing_text = PROCESSING_MSG.format(
        first_name=user['first_name'],
        attempt=usage + 1,
        limit=limit,
        eta=eta,
        end_time=end_time
    )
    processing_msg = await update.message.reply_text(processing_text)
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = bytes(await file.download_as_bytearray())
        if not image_bytes:
            await processing_msg.edit_text("❌ ইমেজ ডাউনলোড করতে সমস্যা হয়েছে। আবার চেষ্টা করুন।")
            return
        mcqs, error = await mcq_generator.generate_from_image(image_bytes)
        if error:
            await processing_msg.edit_text(f"❌ MCQ বানাতে সমস্যা হয়েছে: {error}\n\nআবার চেষ্টা করুন।")
            return
        quiz_id = save_mcq(user['user_id'], mcqs, 'image')
        new_usage = increment_usage(user['user_id'])
        success_text = f"✅ {len(mcqs)} টি MCQ তৈরি হয়েছে!\n\n📊 আজকের ব্যবহার: {new_usage}/{limit}"
        keyboard = [
            [
                InlineKeyboardButton("📊 Poll Solve", callback_data=f"poll_{quiz_id}"),
                InlineKeyboardButton("📝 Quiz Solve", callback_data=f"quiz_{quiz_id}"),
            ],
            [
                InlineKeyboardButton("🌐 Website Exam", url=create_exam_link(quiz_id, mcqs))
            ]
        ]
        await processing_msg.edit_text(success_text, reply_markup=InlineKeyboardMarkup(keyboard))
        log(f"✅ Image MCQ generated: {quiz_id} ({len(mcqs)} questions)")
    except Exception as e:
        log_error(f"Image handler error: {e}")
        try:
            await processing_msg.edit_text(f"❌ একটা সমস্যা হয়েছে। আবার চেষ্টা করুন।\nError: {str(e)[:100]}")
        except:
            pass

# ============================================
# HANDLE TEXT MESSAGE
# ============================================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user_info(update)
    text = update.message.text
    log(f"📝 Text from {user['user_id']} ({len(text)} chars)")
    allowed, usage, limit, is_perm = check_access(user['user_id'])
    if not allowed:
        if is_perm:
            await update.message.reply_text(f"❌ আপনার আজকের লিমিট ({limit}) শেষ। আগামীকাল আবার চেষ্টা করুন।")
        else:
            await update.message.reply_text(PREMIUM_MSG)
        return
    eta = random.randint(3, 8)
    end_time = (datetime.now(BD_TZ) + timedelta(seconds=eta)).strftime("%I:%M %p")
    processing_text = PROCESSING_MSG.format(
        first_name=user['first_name'],
        attempt=usage + 1,
        limit=limit,
        eta=eta,
        end_time=end_time
    )
    processing_msg = await update.message.reply_text(processing_text)
    try:
        mcqs, error = await mcq_generator.generate_from_text(text)
        if error:
            await processing_msg.edit_text(f"❌ MCQ বানাতে সমস্যা হয়েছে: {error}\n\nআবার চেষ্টা করুন।")
            return
        quiz_id = save_mcq(user['user_id'], mcqs, 'text')
        new_usage = increment_usage(user['user_id'])
        success_text = f"✅ {len(mcqs)} টি MCQ তৈরি হয়েছে!\n\n📊 আজকের ব্যবহার: {new_usage}/{limit}"
        keyboard = [
            [
                InlineKeyboardButton("📊 Poll Solve", callback_data=f"poll_{quiz_id}"),
                InlineKeyboardButton("📝 Quiz Solve", callback_data=f"quiz_{quiz_id}"),
            ],
            [
                InlineKeyboardButton("🌐 Website Exam", url=create_exam_link(quiz_id, mcqs))
            ]
        ]
        await processing_msg.edit_text(success_text, reply_markup=InlineKeyboardMarkup(keyboard))
        log(f"✅ Text MCQ generated: {quiz_id} ({len(mcqs)} questions)")
    except Exception as e:
        log_error(f"Text handler error: {e}")
        try:
            await processing_msg.edit_text(f"❌ একটা সমস্যা হয়েছে। আবার চেষ্টা করুন।\nError: {str(e)[:100]}")
        except:
            pass

# ============================================
# HANDLE CALLBACK QUERIES
# ============================================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = query.from_user
    chat_id = query.message.chat_id
    log(f"🔘 Callback: {data} from {user.id}")
    await query.answer()
    try:
        if data.startswith("poll_"):
            quiz_id = data.replace("poll_", "")
            await handle_poll_solve(query, quiz_id, user)
        elif data.startswith("quiz_"):
            quiz_id = data.replace("quiz_", "")
            await handle_quiz_start(query, quiz_id, user, chat_id)
        elif data.startswith("startquiz_"):
            await send_first_question(query, chat_id)
        elif data.startswith("again_"):
            quiz_id = data.replace("again_", "")
            await handle_poll_solve(query, quiz_id, user)
        elif data.startswith("newp_"):
            await handle_new_practice(query, user, 'poll')
        elif data.startswith("newq_"):
            await handle_new_practice(query, user, 'quiz')
        elif data.startswith("retake_"):
            quiz_id = data.replace("retake_", "")
            await handle_quiz_start(query, quiz_id, user, chat_id)
    except Exception as e:
        log_error(f"Callback error: {e}")
        try:
            await query.message.reply_text(f"❌ Error: {str(e)[:100]}")
        except:
            pass

# ============================================
# POLL SOLVE
# ============================================
async def handle_poll_solve(query, quiz_id, user):
    log(f"📊 Poll solve: {quiz_id}")
    mcq_data = get_mcq(quiz_id)
    if not mcq_data:
        await query.message.reply_text("❌ MCQ data পাওয়া যায়নি।")
        return
    mcqs = apply_tag_exp(mcq_data['mcqs'])
    total = len(mcqs)
    settings = get_all_settings()
    timer = int(settings.get('timer_seconds', DEFAULT_TIMER))
    await query.message.reply_text(f"📊 {total} টি Poll পাঠানো শুরু হচ্ছে...\n⏱️ প্রতিটিতে {timer} সেকেন্ড।")
    for i, mcq in enumerate(mcqs):
        try:
            q_text = format_poll_question(mcq, i + 1)
            exp_text = format_explanation(mcq)
            options = mcq['options'][:4]
            correct_id = mcq.get('answer', 0)
            if correct_id >= len(options):
                correct_id = 0
            await query.message.chat.send_poll(
                question=q_text,
                options=options,
                type=Poll.QUIZ,
                correct_option_id=correct_id,
                explanation=exp_text,
                is_anonymous=True,
            )
            if i < total - 1:
                await asyncio.sleep(POLL_DELAY)
        except Exception as e:
            log_error(f"Poll {i+1} send error: {e}")
            continue
    keyboard = [
        [
            InlineKeyboardButton("🔄 Again Practice", callback_data=f"again_{quiz_id}"),
            InlineKeyboardButton("🆕 New Practice", callback_data=f"newp_{quiz_id}")
        ]
    ]
    await query.message.chat.send_message(
        f"✅ Total {total} টি poll পাঠানো হয়েছে।",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ============================================
# QUIZ SOLVE — Native Poll with auto-next
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
    quiz_state = {
        'quiz_id': quiz_id,
        'mcqs': mcqs,
        'current_index': 0,
        'answers': {},
        'correct': 0,
        'wrong': 0,
        'skipped': 0,
        'start_time': time.time(),
        'timer': timer,
        'neg_mark': neg_mark,
        'current_poll_id': None,
    }
    save_active_quiz(chat_id, quiz_state)
    ready_text = (
        f"📝 *Quiz Ready!*\n\n"
        f"📋 Total Questions: {total}\n"
        f"⏱️ Per Question: {timer} সেকেন্ড\n"
        f"📊 Negative Mark: {neg_mark}\n\n"
        f"প্রস্তুত? শুরু করুন! 🚀"
    )
    keyboard = [[InlineKeyboardButton("▶️ Start Quiz", callback_data=f"startquiz_{quiz_id}")]]
    await query.message.reply_text(ready_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)


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
    total = len(mcqs)
    timer = quiz['timer']
    q_text = format_poll_question(mcq, idx + 1)
    exp_text = format_explanation(mcq)
    options = mcq['options'][:4]
    correct_id = mcq.get('answer', 0)
    if correct_id >= len(options):
        correct_id = 0
    try:
        msg = await application.bot.send_poll(
            chat_id=chat_id,
            question=q_text,
            options=options,
            type=Poll.QUIZ,
            correct_option_id=correct_id,
            explanation=exp_text,
            is_anonymous=False,
            open_period=timer,
        )
        poll_id = msg.poll.id
        quiz['current_poll_id'] = poll_id
        _poll_chat_map[poll_id] = chat_id
        # Cancel old timer
        old_task = _timer_tasks.pop(chat_id, None)
        if old_task and not old_task.done():
            old_task.cancel()
        # Start new timer (timer + 1s buffer)
        task = asyncio.create_task(_quiz_timer_task(chat_id, timer + 1))
        _timer_tasks[chat_id] = task
        log(f"📊 Quiz poll sent: Q{idx+1}/{total} chat={chat_id}")
    except Exception as e:
        log_error(f"Send quiz poll error: {e}")
        quiz['skipped'] += 1
        quiz['current_index'] += 1
        await send_quiz_poll(chat_id)


async def _quiz_timer_task(chat_id, delay):
    """Auto-next after timer expires"""
    await asyncio.sleep(delay)
    quiz = get_active_quiz(chat_id)
    if not quiz:
        return
    log(f"⏱️ Timer expired chat={chat_id}, auto-next")
    quiz['skipped'] += 1
    quiz['current_index'] += 1
    await send_quiz_poll(chat_id)


async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle quiz poll answer → auto next"""
    answer = update.poll_answer
    poll_id = answer.poll_id
    chat_id = _poll_chat_map.get(poll_id)
    if not chat_id:
        return
    quiz = get_active_quiz(chat_id)
    if not quiz:
        return
    # Cancel timer
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
    mins = time_taken // 60
    secs = time_taken % 60
    time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
    result_text = (
        f"🎯 *QUIZ RESULT*\n"
        f"{'━'*22}\n"
        f"📝 Total: {total}\n"
        f"✅ Right: {correct}\n"
        f"❌ Wrong: {wrong}\n"
        f"⏭️ Skipped: {skipped}\n"
        f"⏱️ Time: {time_str}\n"
        f"{'━'*22}\n"
        f"📊 *Negative Mark:*\n"
        f"❌ {wrong} × {neg_mark} = -{penalty:.2f}\n"
        f"📊 Final Mark: *{final_mark:.2f}/{total}*\n"
        f"{'━'*22}\n"
        f"{feedback}\n\n"
        f"📖 _{ayat}_"
    )
    keyboard = [
        [
            InlineKeyboardButton("🔄 Same Quiz Retake", callback_data=f"retake_{quiz['quiz_id']}"),
            InlineKeyboardButton("🆕 New Quiz (15)", callback_data=f"newq_{quiz['quiz_id']}"),
        ],
        [
            InlineKeyboardButton("📊 New Poll (15)", callback_data=f"newp_{quiz['quiz_id']}")
        ]
    ]
    try:
        save_result(
            user_id=chat_id,
            quiz_name=f"Quiz_{quiz['quiz_id'][:6]}",
            total=total,
            right=correct,
            wrong=wrong,
            skipped=skipped,
            time_taken=time_taken,
            mark=final_mark,
            negative_mark=penalty
        )
    except Exception as e:
        log_error(f"Save result error: {e}")
    try:
        await application.bot.send_message(
            chat_id=chat_id,
            text=result_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        log_error(f"Send result error: {e}")
    remove_active_quiz(chat_id)

# ============================================
# NEW PRACTICE
# ============================================
async def handle_new_practice(query, user, mode='poll'):
    chat_id = query.message.chat_id
    await query.message.reply_text("🆕 নতুন MCQ তৈরি করা হচ্ছে...")
    try:
        mcqs_data = get_user_mcqs(user.id)
        if not mcqs_data:
            await query.message.reply_text("❌ কোনো MCQ পাওয়া যায়নি।")
            return
        old_mcqs = mcqs_data[0]['mcqs']
        random.shuffle(old_mcqs)
        new_mcqs = old_mcqs[:NEW_PRACTICE_COUNT]
        quiz_id = save_mcq(user.id, new_mcqs, 'practice')
        if mode == 'quiz':
            settings = get_all_settings()
            timer = int(settings.get('timer_seconds', DEFAULT_TIMER))
            neg_mark = float(settings.get('negative_mark', DEFAULT_NEGATIVE_MARK))
            tagged = apply_tag_exp(new_mcqs)
            random.shuffle(tagged)
            quiz_state = {
                'quiz_id': quiz_id,
                'mcqs': tagged,
                'current_index': 0,
                'answers': {},
                'correct': 0,
                'wrong': 0,
                'skipped': 0,
                'start_time': time.time(),
                'timer': timer,
                'neg_mark': neg_mark,
                'current_poll_id': None,
            }
            save_active_quiz(chat_id, quiz_state)
            keyboard = [[InlineKeyboardButton("▶️ Start Quiz", callback_data=f"startquiz_{quiz_id}")]]
            await query.message.reply_text(
                f"✅ {len(new_mcqs)} টি নতুন MCQ রেডি!\n\n[▶️ Start Quiz] চাপুন।",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            class _Q:
                message = query.message
            await handle_poll_solve(_Q(), quiz_id, user)
    except Exception as e:
        log_error(f"New practice error: {e}")
        await query.message.reply_text(f"❌ Error: {str(e)[:100]}")

# ============================================
# WEBHOOK HANDLER
# ============================================
_bot_loop = None

def setup_webhook_route(flask_app):
    from flask import request as flask_request

    @flask_app.route('/webhook', methods=['POST'])
    def webhook():
        token = flask_request.headers.get('X-Bot-Token', '')
        if token != BOT_TOKEN:
            return 'Unauthorized', 401
        data = flask_request.get_json(force=True)
        if data and application and _bot_loop:
            update = Update.de_json(data, application.bot)
            asyncio.run_coroutine_threadsafe(
                application.process_update(update),
                _bot_loop
            )
        return 'OK', 200

# ============================================
# MAIN
# ============================================
async def main():
    global _bot_loop
    _bot_loop = asyncio.get_event_loop()

    log("=" * 60)
    log("🚀 ATLAS MCQ BOT STARTING (WEBHOOK MODE)")
    log("=" * 60)

    log("📦 Initializing database...")
    init_database()

    log("🤖 Setting up bot...")
    await setup_bot()

    await application.initialize()
    await application.start()

    webhook_url = f"https://atlas-bot-proxy.hamza818483.workers.dev/webhook/{BOT_TOKEN}"
    try:
        await application.bot.set_webhook(max_connections=40,
            url=webhook_url,
            allowed_updates=["message", "callback_query", "poll_answer", "poll"],
            drop_pending_updates=True
        )
        log(f"✅ Webhook set: {webhook_url}")
    except Exception as e:
        log_error(f"Webhook set failed: {e}")

    from exam_server import app as fastapi_app
    setup_webhook_route(fastapi_app)

    log("🌐 Starting exam+webhook server on port 7860...")
    import uvicorn
    config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=7860, log_level="warning")
    server = uvicorn.Server(config)

    log("✅ Bot is running in webhook mode!")
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
