# ============================================================
# Custom Nested Menu System (/menu) — AtlasBot (python-telegram-bot version)
# - /menu <name>            -> naya main menu item add hobe
# - /menu                   -> shob main menu item list, each row e Open/Add/Delete
# - item tap                -> ওই item er under-e thaka sub-items dekhabe (+ Add + Delete + Back)
# - "➕ Add more" tap        -> naya sub-item er naam type korte bola hobe (unlimited nested)
#   -- CSV file pathle       -> সেই item-এ CSV internally save hoye thakbe, taarpor koyta MCQ
#                              practice korte chan seta jiggesh korbe. Count dile Quiz/Poll/
#                              Website Exam banaye inline button hisebe dibe.
# - "🗑 Delete" tap          -> ওই item + tar shob sub-item delete (confirm shoho)
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

# uid -> parent_id (0 = root) jekhane "Add more" chaper por naya item add hobe
MENU_ADD_PENDING = {}
# uid -> {"item_id": int, "max": int} jekhane CSV shobe save hoyeche, count jiggesh kora hocche
MENU_COUNT_PENDING = {}
# uid -> current parent_id jekhane user ekhon reply-keyboard menu-te ache (navigation state)
MENU_NAV_STATE = {}

ADD_LABEL = "➕ Add"
DELETE_LABEL = "🗑 Delete"
BACK_LABEL = "🔙 Back"
CLOSE_LABEL = "❌ Close Menu"


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


async def _delete_item_recursive(item_id: int):
    children = await _get_children(item_id)
    for ch in children:
        await _delete_item_recursive(ch["id"])
    await d1_query("DELETE FROM menu_items WHERE id = ?", [item_id])


def _item_row_buttons(item_id: int, name: str) -> list:
    return [InlineKeyboardButton(f"📁 {name}", callback_data=f"mnuopen_{item_id}")]


async def _render_listing(parent_id: int):
    if parent_id:
        item = await _get_item(parent_id)
        title = f"📁 <b>{item['name']}</b>" if item else "📋 <b>Menu</b>"
        back_target = f"mnuopen_{item['parent_id']}" if (item and item["parent_id"]) else "mnuroot"
    else:
        title = "📋 <b>Main Menu</b>"
        back_target = None

    children = await _get_children(parent_id)
    # Grid layout: 3 buttons per row (SS-এর মতো row-wise)
    flat = [InlineKeyboardButton(f"📁 {ch['name']}", callback_data=f"mnuopen_{ch['id']}") for ch in children]
    rows = [flat[i:i + 3] for i in range(0, len(flat), 3)]
    action_row = [
        InlineKeyboardButton("➕ Add more", callback_data=f"mnuadd_{parent_id}"),
    ]
    if children:
        action_row.append(InlineKeyboardButton("🗑 Delete", callback_data=f"mnudelpick_{parent_id}"))
    rows.append(action_row)
    if back_target:
        rows.append([InlineKeyboardButton("🔙 Back", callback_data=back_target)])
    return title, InlineKeyboardMarkup(rows)


async def _build_reply_keyboard(parent_id: int) -> ReplyKeyboardMarkup:
    """Persistent bottom keyboard (box-icon area) — row-wise, 3 names per row."""
    children = await _get_children(parent_id)
    names = [ch["name"] for ch in children]
    rows = [[KeyboardButton(n) for n in names[i:i + 3]] for i in range(0, len(names), 3)]
    action_row = [KeyboardButton(ADD_LABEL)]
    if children:
        action_row.append(KeyboardButton(DELETE_LABEL))
    rows.append(action_row)
    if parent_id:
        rows.append([KeyboardButton(BACK_LABEL)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    uid = update.effective_user.id
    name = text[len("/menu"):].strip()

    if name:
        await _add_item(0, name, uid)
        await update.message.reply_text(f"✅ Menu-তে যোগ হয়েছে: <b>{name}</b>", parse_mode=ParseMode.HTML)
        return

    MENU_NAV_STATE[uid] = 0
    kb = await _build_reply_keyboard(0)
    await update.message.reply_text("📋 <b>Main Menu</b>", parse_mode=ParseMode.HTML, reply_markup=kb)


async def handle_menu_reply_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles taps on the persistent bottom keyboard. Returns True if consumed."""
    uid = update.effective_user.id
    if uid not in MENU_NAV_STATE:
        return False
    msg = update.message
    text = (msg.text or "").strip()
    if not text:
        return False

    parent_id = MENU_NAV_STATE[uid]

    if text == CLOSE_LABEL:
        MENU_NAV_STATE.pop(uid, None)
        from telegram import ReplyKeyboardRemove
        await msg.reply_text("✅ Menu বন্ধ করা হয়েছে।", reply_markup=ReplyKeyboardRemove())
        return True

    if text == BACK_LABEL:
        item = await _get_item(parent_id) if parent_id else None
        new_parent = item["parent_id"] if item else 0
        MENU_NAV_STATE[uid] = new_parent
        kb = await _build_reply_keyboard(new_parent)
        if new_parent:
            parent_item = await _get_item(new_parent)
            title = f"📁 {parent_item['name']}" if parent_item else "📋 Menu"
        else:
            title = "📋 Main Menu"
        await msg.reply_text(title, reply_markup=kb)
        return True

    if text == ADD_LABEL:
        MENU_ADD_PENDING[uid] = parent_id
        await msg.reply_text(
            "✏️ নতুন item-এর নাম লিখে পাঠাও।\n📎 অথবা CSV ফাইল পাঠাও (নাম হিসেবে ফাইলের নাম ব্যবহার হবে)।"
        )
        return True

    if text == DELETE_LABEL:
        children = await _get_children(parent_id)
        if not children:
            await msg.reply_text("❌ Delete করার মতো কিছু নেই।")
            return True
        rows = [[KeyboardButton(f"🗑 {ch['name']}")] for ch in children]
        rows.append([KeyboardButton(BACK_LABEL)])
        MENU_NAV_STATE[uid] = -parent_id - 1_000_000  # sentinel: negative-offset = delete-pick mode
        await msg.reply_text("🗑 কোনটা Delete করবে?", reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True))
        return True

    if parent_id <= -1_000_000:
        real_parent = -(parent_id + 1_000_000)
        if text == BACK_LABEL:
            MENU_NAV_STATE[uid] = real_parent
            kb = await _build_reply_keyboard(real_parent)
            await msg.reply_text("📋 Menu", reply_markup=kb)
            return True
        if text.startswith("🗑 "):
            name = text[2:].strip()
            children = await _get_children(real_parent)
            match = next((c for c in children if c["name"] == name), None)
            if match:
                await _delete_item_recursive(match["id"])
                await msg.reply_text(f"✅ <b>{name}</b> delete হয়েছে।", parse_mode=ParseMode.HTML)
            MENU_NAV_STATE[uid] = real_parent
            kb = await _build_reply_keyboard(real_parent)
            await msg.reply_text("📋 Menu", reply_markup=kb)
            return True
        return False

    # Otherwise: check if text matches a child item name -> open it
    children = await _get_children(parent_id)
    match = next((c for c in children if c["name"] == text), None)
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

    MENU_NAV_STATE[uid] = match["id"]
    kb = await _build_reply_keyboard(match["id"])
    await msg.reply_text(f"📁 <b>{match['name']}</b>", parse_mode=ParseMode.HTML, reply_markup=kb)
    return True


async def handle_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if handled."""
    query = update.callback_query
    data = query.data
    uid = update.effective_user.id

    if data.startswith("mnuopen_") or data == "mnuroot":
        parent_id = 0 if data == "mnuroot" else int(data[len("mnuopen_"):])
        title, kb = await _render_listing(parent_id)
        await query.edit_message_text(title, parse_mode=ParseMode.HTML, reply_markup=kb)
        return True

    if data.startswith("mnuadd_"):
        parent_id = int(data[len("mnuadd_"):])
        MENU_ADD_PENDING[uid] = parent_id
        await query.edit_message_text(
            "✏️ নতুন item-এর নাম লিখে পাঠাও।\n📎 অথবা CSV ফাইল পাঠাও (নাম হিসেবে ফাইলের নাম ব্যবহার হবে)।"
        )
        return True

    if data.startswith("mnudelpick_"):
        parent_id = int(data[len("mnudelpick_"):])
        children = await _get_children(parent_id)
        if not children:
            return True
        flat = [InlineKeyboardButton(f"🗑 {ch['name']}", callback_data=f"mnudelask_{ch['id']}") for ch in children]
        rows = [flat[i:i + 2] for i in range(0, len(flat), 2)]
        back_target = f"mnuopen_{parent_id}" if parent_id else "mnuroot"
        rows.append([InlineKeyboardButton("🔙 Back", callback_data=back_target)])
        await query.edit_message_text(
            "🗑 কোনটা Delete করবে?", reply_markup=InlineKeyboardMarkup(rows)
        )
        return True

    if data.startswith("mnudelask_"):
        item_id = int(data[len("mnudelask_"):])
        item = await _get_item(item_id)
        if not item:
            return True
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ হ্যাঁ, Delete করো", callback_data=f"mnudelyes_{item_id}"),
            InlineKeyboardButton("❌ না", callback_data=f"mnuopen_{item['parent_id']}" if item["parent_id"] else "mnuroot"),
        ]])
        await query.edit_message_text(
            f"🗑 <b>{item['name']}</b> এবং এর ভেতরের সব কিছু delete করবে?",
            parse_mode=ParseMode.HTML, reply_markup=kb,
        )
        return True

    if data.startswith("mnudelyes_"):
        item_id = int(data[len("mnudelyes_"):])
        item = await _get_item(item_id)
        parent_id = item["parent_id"] if item else 0
        await _delete_item_recursive(item_id)
        title, kb = await _render_listing(parent_id)
        await query.edit_message_text(f"✅ Delete হয়েছে।\n\n{title}", parse_mode=ParseMode.HTML, reply_markup=kb)
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
    """Returns True if consumed (uid was awaiting a menu-add name / CSV / count)."""
    uid = update.effective_user.id
    msg = update.message

    if uid in MENU_ADD_PENDING:
        if msg.document:
            fname = msg.document.file_name or ""
            if fname.lower().endswith(".csv"):
                parent_id = MENU_ADD_PENDING.pop(uid)
                wait_msg = await msg.reply_text("⏳ CSV পড়া হচ্ছে...")
                try:
                    file = await msg.document.get_file()
                    file_bytes = bytes(await file.download_as_bytearray())
                    csv_text = file_bytes.decode("utf-8-sig")
                    reader = _csv_mod.reader(StringIO(csv_text))
                    rows = list(reader)
                    mcqs = []
                    for row in rows[1:]:
                        if len(row) < 5:
                            continue
                        q = row[0].strip()
                        opts = [row[1].strip(), row[2].strip(), row[3].strip(), row[4].strip()]
                        ans = int(row[5].strip()) if len(row) > 5 and row[5].strip().isdigit() else 0
                        exp = row[6].strip() if len(row) > 6 else ""
                        if not q or not all(opts):
                            continue
                        mcqs.append({"question": q, "options": opts, "answer": min(ans, 3), "explanation": exp})
                    if not mcqs:
                        await wait_msg.edit_text("❌ CSV-তে কোনো valid MCQ পাওয়া যায়নি।")
                        return True
                    name = fname.rsplit(".", 1)[0]
                    item_id = await _add_item(parent_id, name, uid, csv_data=json.dumps(mcqs))
                    await wait_msg.edit_text(
                        f"✅ <b>{name}</b> যোগ হয়েছে ({len(mcqs)} টি MCQ সংরক্ষিত আছে)।\n\n"
                        "কয়টি MCQ practice করতে চান, সংখ্যা লিখে পাঠান:",
                        parse_mode=ParseMode.HTML,
                    )
                    MENU_COUNT_PENDING[uid] = {"item_id": item_id, "max": len(mcqs)}
                except Exception as e:
                    await wait_msg.edit_text(f"❌ Error: {e}")
                return True
            return False

        text = (msg.text or "").strip()
        if not text or text.startswith("/"):
            return False
        parent_id = MENU_ADD_PENDING.pop(uid)
        await _add_item(parent_id, text, uid)
        await msg.reply_text(f"✅ যোগ হয়েছে: <b>{text}</b>", parse_mode=ParseMode.HTML)
        MENU_NAV_STATE[uid] = parent_id
        kb = await _build_reply_keyboard(parent_id)
        await msg.reply_text("📋 Menu", reply_markup=kb)
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
