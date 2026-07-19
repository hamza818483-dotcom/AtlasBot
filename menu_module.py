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
    res = await d1_query(
        "INSERT INTO menu_items (parent_id, name, csv_data, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
        [parent_id, name, csv_data, uid, int(time.time())],
    )
    results = res.get("results", [])
    if results and isinstance(results, list) and results[0].get("meta", {}).get("last_row_id"):
        return results[0]["meta"]["last_row_id"]
    meta = res.get("meta") or (results[0].get("meta") if results else {}) or {}
    return meta.get("last_row_id", 0)


async def _get_children_fresh(parent_id: int, expect_name: str = None) -> list:
    """Like _get_children but retries briefly if a just-inserted row (expect_name)
    isn't visible yet — works around D1 read-replica lag."""
    import asyncio
    for attempt in range(4):
        children = await _get_children(parent_id)
        if not expect_name or any(c["name"] == expect_name for c in children):
            return children
        await asyncio.sleep(0.3 * (attempt + 1))
    return children


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


async def _build_reply_keyboard(parent_id: int = 0, expect_name: str = None) -> ReplyKeyboardMarkup:
    """Bottom keyboard (box-icon area) — ONLY items, like Exampedia's persistent menu.
    Open/close is handled purely by Telegram's native keyboard-toggle icon; the bot
    never removes this keyboard, so the icon stays available forever."""
    children = await _get_children_fresh(parent_id, expect_name) if expect_name else await _get_children(parent_id)
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
    if parent_id:
        item = await _get_item(parent_id)
        back_target = f"mnuopen_{item['parent_id']}" if item and item["parent_id"] else "mnuroot"
        rows.append([InlineKeyboardButton("🔙 Back", callback_data=back_target)])
        title = f"📁 <b>{item['name']}</b>" if item else "📋 <b>Menu</b>"
    else:
        title = "📋 <b>Main Menu</b>"
    return title, InlineKeyboardMarkup(rows)


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


async def _safe_edit(query, text, **kwargs):
    try:
        await query.edit_message_text(text, **kwargs)
    except Exception as e:
        if "not modified" not in str(e).lower():
            raise


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
        parent_id = int(data[len("mnuadd_"):])
        MENU_ADD_PENDING[uid] = parent_id
        await query.answer()
        await _safe_edit(query, 
            "✏️ নতুন item-এর নাম লিখে পাঠাও।\n📎 অথবা CSV ফাইল পাঠাও (MCQ practice item হিসেবে)।"
        )
        return True

    if data.startswith("mnudelpick_"):
        parent_id = int(data[len("mnudelpick_"):])
        children = await _get_children(parent_id)
        if not children:
            await query.answer("❌ Delete করার মতো কিছু নেই।", show_alert=True)
            return True
        flat = [InlineKeyboardButton(f"🗑 {ch['name']}", callback_data=f"mnudelask_{ch['id']}_{parent_id}") for ch in children]
        rows = [flat[i:i + 2] for i in range(0, len(flat), 2)]
        back_target = f"mnuopen_{parent_id}" if parent_id else "mnuroot"
        rows.append([InlineKeyboardButton("🔙 Back", callback_data=back_target)])
        await query.answer()
        await _safe_edit(query, "🗑 কোনটা Delete করবে?", reply_markup=InlineKeyboardMarkup(rows))
        return True

    if data.startswith("mnudelask_"):
        rest = data[len("mnudelask_"):]
        item_id_s, parent_id_s = rest.split("_")
        item_id, parent_id = int(item_id_s), int(parent_id_s)
        item = await _get_item(item_id)
        if not item:
            await query.answer()
            return True
        back_target = f"mnuopen_{parent_id}" if parent_id else "mnuroot"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ হ্যাঁ, Delete করো", callback_data=f"mnudelyes_{item_id}_{parent_id}"),
            InlineKeyboardButton("❌ না", callback_data=back_target),
        ]])
        await query.answer()
        await _safe_edit(query, 
            f"🗑 <b>{item['name']}</b> delete করবে?", parse_mode=ParseMode.HTML, reply_markup=kb,
        )
        return True

    if data.startswith("mnudelyes_"):
        rest = data[len("mnudelyes_"):]
        item_id_s, parent_id_s = rest.split("_")
        item_id, parent_id = int(item_id_s), int(parent_id_s)
        await _delete_item_recursive(item_id)
        await query.answer("✅ Delete হয়েছে")
        import asyncio as _asyncio
        children = await _get_children(parent_id)
        for _attempt in range(4):
            if not any(c["id"] == item_id for c in children):
                break
            await _asyncio.sleep(0.3 * (_attempt + 1))
            children = await _get_children(parent_id)
        title, kb = await _render_listing(parent_id)
        await _safe_edit(query, title, parse_mode=ParseMode.HTML, reply_markup=kb)
        if parent_id == 0:
            await context.bot.send_message(
                update.effective_chat.id, "📋 Menu (box-icon)", reply_markup=await _build_reply_keyboard(0)
            )
        return True

    if data.startswith("mnueditpick_"):
        parent_id = int(data[len("mnueditpick_"):])
        children = await _get_children(parent_id)
        if not children:
            await query.answer("❌ Edit করার মতো কিছু নেই।", show_alert=True)
            return True
        flat = [InlineKeyboardButton(f"✏️ {ch['name']}", callback_data=f"mnueditask_{ch['id']}_{parent_id}") for ch in children]
        rows = [flat[i:i + 2] for i in range(0, len(flat), 2)]
        back_target = f"mnuopen_{parent_id}" if parent_id else "mnuroot"
        rows.append([InlineKeyboardButton("🔙 Back", callback_data=back_target)])
        await query.answer()
        await _safe_edit(query, "✏️ কোনটা Edit করবে?", reply_markup=InlineKeyboardMarkup(rows))
        return True

    if data.startswith("mnueditask_"):
        rest = data[len("mnueditask_"):]
        item_id_s, parent_id_s = rest.split("_")
        item_id, parent_id = int(item_id_s), int(parent_id_s)
        MENU_EDIT_PENDING[uid] = {"item_id": item_id, "parent_id": parent_id}
        await query.answer()
        await _safe_edit(query, "✏️ নতুন নাম লিখে পাঠাও।")
        return True

    if data == "mnuroot":
        title, kb = await _render_listing(0)
        await query.answer()
        await _safe_edit(query, title, parse_mode=ParseMode.HTML, reply_markup=kb)
        return True

    if data.startswith("mnuopen_"):
        item_id = int(data[len("mnuopen_"):])
        item = await _get_item(item_id)
        from bot import is_admin
        await query.answer()
        if not item:
            return True
        if item.get("csv_data"):
            mcqs = json.loads(item["csv_data"])
            MENU_COUNT_PENDING[uid] = {"item_id": item_id, "max": len(mcqs)}
            await _safe_edit(query, 
                f"📁 <b>{item['name']}</b> — {len(mcqs)} টি MCQ সংরক্ষিত আছে।\n\n"
                "কয়টি MCQ practice করতে চান, সংখ্যা লিখে পাঠান:",
                parse_mode=ParseMode.HTML,
            )
            return True
        if is_admin(uid):
            title, kb = await _render_listing(item_id)
            await _safe_edit(query, title, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            children = await _get_children(item_id)
            if not children:
                await _safe_edit(query, f"📁 <b>{item['name']}</b> — এখনো কিছু যোগ করা হয়নি।", parse_mode=ParseMode.HTML)
            else:
                flat = [InlineKeyboardButton(f"📁 {ch['name']}", callback_data=f"mnuopen_{ch['id']}") for ch in children]
                rows = [flat[i:i + 3] for i in range(0, len(flat), 3)]
                back_target = f"mnuopen_{item['parent_id']}" if item["parent_id"] else "mnuroot"
                rows.append([InlineKeyboardButton("🔙 Back", callback_data=back_target)])
                await _safe_edit(query, f"📁 <b>{item['name']}</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
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
    """Returns True if consumed (uid was awaiting a menu-add name/csv / edit-name / CSV count)."""
    uid = update.effective_user.id
    msg = update.message

    if uid in MENU_ADD_PENDING:
        parent_id = MENU_ADD_PENDING[uid]

        if msg.document and msg.document.file_name.lower().endswith(".csv"):
            MENU_ADD_PENDING.pop(uid)
            file = await context.bot.get_file(msg.document.file_id)
            raw = await file.download_as_bytearray()
            try:
                reader = _csv_mod.DictReader(StringIO(raw.decode("utf-8-sig")))
                mcqs = []
                for raw_row in reader:
                    row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items()}
                    q = row.get("question") or row.get("questions") or ""
                    if not q:
                        continue
                    opts = [row.get(f"option{i}") or "" for i in range(1, 5)]
                    opts = [o for o in opts if o]
                    ans_raw = row.get("answer") or "0"
                    try:
                        ans = int(ans_raw)
                    except ValueError:
                        ans = 0
                    expl = row.get("explanation") or ""
                    mcqs.append({"question": q, "options": opts, "answer": ans, "explanation": expl})
            except Exception as e:
                await msg.reply_text(f"❌ CSV পড়তে সমস্যা হয়েছে: {e}")
                return True
            if not mcqs:
                try:
                    headers = reader.fieldnames
                except Exception:
                    headers = None
                await msg.reply_text(
                    f"❌ CSV-তে কোনো valid MCQ পাওয়া যায়নি।\n"
                    f"প্রয়োজনীয় কলাম: question(s), option1-4, answer, explanation\n"
                    f"পাওয়া গেছে: {headers}"
                )
                return True
            name = msg.document.file_name.rsplit(".", 1)[0]
            await _add_item(parent_id, name, uid, csv_data=json.dumps(mcqs))
            await msg.reply_text(f"✅ যোগ হয়েছে: <b>{name}</b> ({len(mcqs)} টি MCQ)", parse_mode=ParseMode.HTML)
            title, kb = await _render_listing(parent_id)
            await msg.reply_text(title, parse_mode=ParseMode.HTML, reply_markup=kb)
            if parent_id == 0:
                await msg.reply_text("📋 Menu (box-icon)", reply_markup=await _build_reply_keyboard(0, expect_name=name))
            return True

        text = (msg.text or "").strip()
        if not text or text.startswith("/"):
            return False
        MENU_ADD_PENDING.pop(uid)
        await _add_item(parent_id, text, uid)
        await msg.reply_text(f"✅ যোগ হয়েছে: <b>{text}</b>", parse_mode=ParseMode.HTML)
        title, kb = await _render_listing(parent_id)
        await msg.reply_text(title, parse_mode=ParseMode.HTML, reply_markup=kb)
        if parent_id == 0:
            await msg.reply_text("📋 Menu (box-icon)", reply_markup=await _build_reply_keyboard(0, expect_name=text))
        return True

    if uid in MENU_EDIT_PENDING:
        text = (msg.text or "").strip()
        if not text or text.startswith("/"):
            return False
        info = MENU_EDIT_PENDING.pop(uid)
        item_id, parent_id = info["item_id"], info["parent_id"]
        await _rename_item(item_id, text)
        await msg.reply_text(f"✅ নতুন নাম সেভ হয়েছে: <b>{text}</b>", parse_mode=ParseMode.HTML)
        title, kb = await _render_listing(parent_id)
        await msg.reply_text(title, parse_mode=ParseMode.HTML, reply_markup=kb)
        if parent_id == 0:
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
