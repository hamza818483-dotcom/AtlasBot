# ============================================================
# Custom Nested Menu System (/menu) — AtlasBot (python-telegram-bot version)
# Box-icon (bottom persistent reply-keyboard) shows ONLY the item list,
# always available — NO Add/Delete/Edit buttons inside it.
# All management is done manually via /menu commands:
#   /menu                    -> shows current list in the box-icon keyboard
#   /menu <name>             -> add a new item (root level)
#   /menu del <name>         -> delete an item (and its sub-items if any)
#   /menu edit <old> | <new> -> rename an item
# Tapping an item name in the box-icon opens that item (CSV practice flow
# if it has CSV data attached; otherwise just shows sub-items, if you nest).
# Storage: D1 table menu_items (self-referencing parent_id)
# ============================================================
import json
import time
import csv as _csv_mod
from io import StringIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from storage import d1_query

_TABLE_READY = False

# uid -> {"item_id": int, "max": int} jekhane CSV shobe save hoyeche, count jiggesh kora hocche
MENU_COUNT_PENDING = {}
# uid -> True mane ei uid /menu box-icon active kore rekheche (item-tap detect korar jonno)
MENU_NAV_STATE = {}
# uid -> True mane notun item-er naam ashar opekkhay (inline "Add more" theke)
MENU_ADD_PENDING = {}
# uid -> item_id jar notun naam ashar opekkhay (inline "Edit" theke)
MENU_EDIT_PENDING = {}


async def _ensure_table():
    global _TABLE_READY
    if _TABLE_READY:
        return
    await d1_query(
        "CREATE TABLE IF NOT EXISTS menu_items ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "parent_id INTEGER NOT NULL DEFAULT 0, "
        "name TEXT NOT NULL, "
        "csv_data TEXT, "
        "created_by INTEGER, "
        "created_at INTEGER)"
    )
    _TABLE_READY = True


async def _add_item(parent_id: int, name: str, uid: int, csv_data: str = None) -> int:
    await _ensure_table()
    await d1_query(
        "INSERT INTO menu_items (parent_id, name, csv_data, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
        [parent_id, name, csv_data, uid, int(time.time())],
    )
    res = await d1_query(
        "SELECT id FROM menu_items WHERE parent_id = ? AND name = ? ORDER BY id DESC LIMIT 1",
        [parent_id, name],
    )
    rows = res.get("results", [])
    return rows[0]["id"] if rows else 0


async def _get_children(parent_id: int) -> list:
    await _ensure_table()
    res = await d1_query(
        "SELECT id, name, csv_data FROM menu_items WHERE parent_id = ? ORDER BY id ASC",
        [parent_id],
    )
    return res.get("results", []) or []


async def _get_item(item_id: int) -> dict:
    await _ensure_table()
    res = await d1_query("SELECT id, parent_id, name, csv_data FROM menu_items WHERE id = ?", [item_id])
    rows = res.get("results", [])
    return rows[0] if rows else None


async def _get_item_by_name(parent_id: int, name: str) -> dict:
    await _ensure_table()
    res = await d1_query(
        "SELECT id, parent_id, name, csv_data FROM menu_items WHERE parent_id = ? AND name = ? LIMIT 1",
        [parent_id, name],
    )
    rows = res.get("results", [])
    return rows[0] if rows else None


async def _delete_item_recursive(item_id: int):
    children = await _get_children(item_id)
    for ch in children:
        await _delete_item_recursive(ch["id"])
    await d1_query("DELETE FROM menu_items WHERE id = ?", [item_id])


async def _rename_item(item_id: int, new_name: str):
    await _ensure_table()
    await d1_query("UPDATE menu_items SET name = ? WHERE id = ?", [new_name, item_id])


async def _build_reply_keyboard(parent_id: int = 0) -> ReplyKeyboardMarkup:
    """Bottom keyboard (box-icon area) — ONLY items, like Exampedia's persistent menu.
    Open/close is handled purely by Telegram's native keyboard-toggle icon; the bot
    never removes this keyboard, so the icon stays available forever."""
    children = await _get_children(parent_id)
    names = [ch["name"] for ch in children]
    if not names:
        rows = [[KeyboardButton("📋 Menu খালি — /menu <নাম> দিয়ে যোগ করো")]]
    else:
        rows = [[KeyboardButton(n) for n in names[i:i + 3]] for i in range(0, len(names), 3)]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


async def _render_listing(parent_id: int = 0):
    children = await _get_children(parent_id)
    flat = [InlineKeyboardButton(f"📁 {ch['name']}", callback_data=f"mnuopen_{ch['id']}") for ch in children]
    rows = [flat[i:i + 3] for i in range(0, len(flat), 3)]
    action_row = [InlineKeyboardButton("➕ Add more", callback_data=f"mnuadd_{parent_id}")]
    if children:
        action_row.append(InlineKeyboardButton("🗑 Delete", callback_data=f"mnudelpick_{parent_id}"))
        action_row.append(InlineKeyboardButton("✏️ Edit", callback_data=f"mnueditpick_{parent_id}"))
    rows.append(action_row)
    return "📋 <b>Main Menu</b>", InlineKeyboardMarkup(rows)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot import is_admin
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ এই কমান্ড শুধু Admin-এর জন্য।")
        return
    MENU_NAV_STATE[uid] = True
    kb_reply = await _build_reply_keyboard(0)
    await update.message.reply_text("📋 Menu (box-icon)", reply_markup=kb_reply)
    title, kb_inline = await _render_listing(0)
    await update.message.reply_text(title, parse_mode=ParseMode.HTML, reply_markup=kb_inline)


async def handle_menu_reply_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles taps on the persistent bottom keyboard items — works for ALL users. Returns True if consumed."""
    uid = update.effective_user.id
    msg = update.message
    text = (msg.text or "").strip()
    if not text:
        return False

    match = await _get_item_by_name(0, text)
    if not match:
        return False

    if match.get("csv_data"):
        mcqs = json.loads(match["csv_data"])
        MENU_COUNT_PENDING[uid] = {"item_id": match["id"], "max": len(mcqs)}
        await msg.reply_text(
            f"📁 <b>{match['name']}</b> — {len(mcqs)} টি MCQ সংরক্ষিত আছে।\n\n"
            "কয়টি MCQ practice করতে চান, সংখ্যা লিখে পাঠান:",
            parse_mode=ParseMode.HTML,
        )
        return True

    await msg.reply_text(f"📁 <b>{match['name']}</b>", parse_mode=ParseMode.HTML)
    return True


async def handle_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if handled."""
    query = update.callback_query
    data = query.data
    uid = update.effective_user.id

    MANAGEMENT_PREFIXES = ("mnuadd_", "mnudelpick_", "mnudelask_", "mnudelyes_", "mnueditpick_", "mnueditask_")
    if data.startswith(MANAGEMENT_PREFIXES) or data == "mnuroot":
        from bot import is_admin
        if not is_admin(uid):
            await query.answer("❌ শুধু Admin ব্যবহার করতে পারবে।", show_alert=True)
            return True

    if data.startswith("mnuadd_"):
        MENU_ADD_PENDING[uid] = True
        await query.answer()
        await query.edit_message_text("✏️ নতুন item-এর নাম লিখে পাঠাও।")
        return True

    if data.startswith("mnudelpick_"):
        children = await _get_children(0)
        if not children:
            await query.answer("❌ Delete করার মতো কিছু নেই।", show_alert=True)
            return True
        flat = [InlineKeyboardButton(f"🗑 {ch['name']}", callback_data=f"mnudelask_{ch['id']}") for ch in children]
        rows = [flat[i:i + 2] for i in range(0, len(flat), 2)]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="mnuroot")])
        await query.answer()
        await query.edit_message_text("🗑 কোনটা Delete করবে?", reply_markup=InlineKeyboardMarkup(rows))
        return True

    if data.startswith("mnudelask_"):
        item_id = int(data[len("mnudelask_"):])
        item = await _get_item(item_id)
        if not item:
            await query.answer()
            return True
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ হ্যাঁ, Delete করো", callback_data=f"mnudelyes_{item_id}"),
            InlineKeyboardButton("❌ না", callback_data="mnuroot"),
        ]])
        await query.answer()
        await query.edit_message_text(
            f"🗑 <b>{item['name']}</b> delete করবে?", parse_mode=ParseMode.HTML, reply_markup=kb,
        )
        return True

    if data.startswith("mnudelyes_"):
        item_id = int(data[len("mnudelyes_"):])
        await _delete_item_recursive(item_id)
        await query.answer("✅ Delete হয়েছে")
        title, kb = await _render_listing(0)
        await query.edit_message_text(title, parse_mode=ParseMode.HTML, reply_markup=kb)
        await context.bot.send_message(
            update.effective_chat.id, "📋 Menu (box-icon)", reply_markup=await _build_reply_keyboard(0)
        )
        return True

    if data.startswith("mnueditpick_"):
        children = await _get_children(0)
        if not children:
            await query.answer("❌ Edit করার মতো কিছু নেই।", show_alert=True)
            return True
        flat = [InlineKeyboardButton(f"✏️ {ch['name']}", callback_data=f"mnueditask_{ch['id']}") for ch in children]
        rows = [flat[i:i + 2] for i in range(0, len(flat), 2)]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="mnuroot")])
        await query.answer()
        await query.edit_message_text("✏️ কোনটা Edit করবে?", reply_markup=InlineKeyboardMarkup(rows))
        return True

    if data.startswith("mnueditask_"):
        item_id = int(data[len("mnueditask_"):])
        MENU_EDIT_PENDING[uid] = item_id
        await query.answer()
        await query.edit_message_text("✏️ নতুন নাম লিখে পাঠাও।")
        return True

    if data == "mnuroot":
        title, kb = await _render_listing(0)
        await query.answer()
        await query.edit_message_text(title, parse_mode=ParseMode.HTML, reply_markup=kb)
        return True

    if data.startswith("mnuopen_"):
        item_id = int(data[len("mnuopen_"):])
        item = await _get_item(item_id)
        await query.answer()
        if item and item.get("csv_data"):
            mcqs = json.loads(item["csv_data"])
            MENU_COUNT_PENDING[uid] = {"item_id": item_id, "max": len(mcqs)}
            await query.edit_message_text(
                f"📁 <b>{item['name']}</b> — {len(mcqs)} টি MCQ সংরক্ষিত আছে।\n\n"
                "কয়টি MCQ practice করতে চান, সংখ্যা লিখে পাঠান:",
                parse_mode=ParseMode.HTML,
            )
        elif item:
            await query.edit_message_text(f"📁 <b>{item['name']}</b>", parse_mode=ParseMode.HTML)
        return True

    if data.startswith("mnucnt_"):
        parts = data.split("_")
        item_id = int(parts[1])
        count = int(parts[2])
        mode = parts[3]
        await _generate_from_item(update, context, item_id, count, mode)
        return True

    return False


async def handle_menu_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if consumed (uid was awaiting a menu-add name / edit-name / CSV count)."""
    uid = update.effective_user.id
    msg = update.message

    if uid in MENU_ADD_PENDING:
        text = (msg.text or "").strip()
        if not text or text.startswith("/"):
            return False
        MENU_ADD_PENDING.pop(uid)
        await _add_item(0, text, uid)
        await msg.reply_text(f"✅ যোগ হয়েছে: <b>{text}</b>", parse_mode=ParseMode.HTML)
        title, kb = await _render_listing(0)
        await msg.reply_text(title, parse_mode=ParseMode.HTML, reply_markup=kb)
        await msg.reply_text("📋 Menu (box-icon)", reply_markup=await _build_reply_keyboard(0))
        return True

    if uid in MENU_EDIT_PENDING:
        text = (msg.text or "").strip()
        if not text or text.startswith("/"):
            return False
        item_id = MENU_EDIT_PENDING.pop(uid)
        await _rename_item(item_id, text)
        await msg.reply_text(f"✅ নতুন নাম সেভ হয়েছে: <b>{text}</b>", parse_mode=ParseMode.HTML)
        title, kb = await _render_listing(0)
        await msg.reply_text(title, parse_mode=ParseMode.HTML, reply_markup=kb)
        await msg.reply_text("📋 Menu (box-icon)", reply_markup=await _build_reply_keyboard(0))
        return True

    if uid in MENU_COUNT_PENDING:
        text = (msg.text or "").strip()
        if not text or text.startswith("/"):
            return False
        if not text.isdigit():
            await msg.reply_text("❌ শুধু সংখ্যা পাঠাও, যেমন: 20")
            return True
        count = int(text)
        info = MENU_COUNT_PENDING.pop(uid)
        item_id = info["item_id"]
        max_n = info["max"]
        count = max(1, min(count, max_n))
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎯 Quiz বানাও (bot link)", callback_data=f"mnucnt_{item_id}_{count}_quiz")],
            [InlineKeyboardButton("📊 Poll পাঠাও (এই চ্যাটে)", callback_data=f"mnucnt_{item_id}_{count}_poll")],
            [InlineKeyboardButton("🌐 Website Exam বানাও", callback_data=f"mnucnt_{item_id}_{count}_exam")],
        ])
        await msg.reply_text(f"✅ {count} টি MCQ নেওয়া হবে। কোন ফরম্যাটে চান?", reply_markup=kb)
        return True

    return False


async def _generate_from_item(update: Update, context: ContextTypes.DEFAULT_TYPE, item_id: int, count: int, mode: str):
    query = update.callback_query
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    item = await _get_item(item_id)
    if not item or not item.get("csv_data"):
        await query.answer("❌ CSV পাওয়া যায়নি", show_alert=True)
        return
    mcqs = json.loads(item["csv_data"])[:count]
    name = item["name"]

    if mode in ("quiz", "exam"):
        from bot import save_mcq, GH_PAGES_EXAM_URL, BOT_USERNAME
        norm_mcqs = []
        for m in mcqs:
            norm_mcqs.append({
                "question": m.get("question", ""),
                "options": m.get("options", []),
                "answer": m.get("answer", 0),
                "explanation": m.get("explanation", ""),
            })
        quiz_id = await save_mcq(user_id=uid, mcqs=norm_mcqs, source_type="menu", prompt_type="prompt_1",
                                  image_file_id=None, chat_id=None, message_id=None)
        await query.answer()
        if mode == "quiz":
            uname = BOT_USERNAME or "atlasprepbot"
            link = f"https://t.me/{uname}?start={quiz_id}"
            await context.bot.send_message(
                chat_id, f"🎯 <b>Quiz তৈরি হয়েছে!</b>\n\n📝 {name} — {len(mcqs)} প্রশ্ন\n\n🔗 <code>{link}</code>",
                parse_mode=ParseMode.HTML,
            )
        else:
            web_link = f"{GH_PAGES_EXAM_URL}?id={quiz_id}&uid={uid}"
            await context.bot.send_message(
                chat_id, f"🌐 <b>Website Exam তৈরি হয়েছে!</b>\n\n📝 {name} — {len(mcqs)} প্রশ্ন\n\n🔗 {web_link}",
                parse_mode=ParseMode.HTML,
            )
        return

    if mode == "poll":
        await query.answer()
        sent = 0
        for m in mcqs:
            try:
                options = m.get("options", [])[:4]
                correct_id = m.get("answer", 0)
                if correct_id >= len(options) or correct_id < 0:
                    correct_id = 0
                await context.bot.send_poll(
                    chat_id=chat_id, question=m.get("question", "")[:300], options=options,
                    type="quiz", correct_option_id=correct_id,
                    explanation=(m.get("explanation") or None), is_anonymous=True,
                )
                sent += 1
            except Exception:
                continue
        await context.bot.send_message(chat_id, f"✅ {sent} টি poll পাঠানো হয়েছে ({name})।")
        return
