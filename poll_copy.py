# ============================================================
# ATLAS BOT — Poll Copy (poll_copy.py)
# /pollcopy <link1>\n<link2>
# Telethon দিয়ে source channel/group (thread/topic সহ) থেকে
# first_link → second_link range এর সব quiz poll extract করে,
# তারপর সেগুলো BRAND NEW live quiz poll হিসেবে এই চ্যাটে পাঠায় —
# user আবার নতুন করে solve করতে পারবে (পুরনো poll-এর copy/forward
# না, প্রতিটা একদম fresh poll object, নতুন vote count/state)।
#
# Extraction logic (parse_tg_link, extract_polls_telethon,
# rate limiter) QuizBot repo-র poll_extract.py থেকে reused —
# same SESSION_STRING/API_ID/API_HASH ব্যবহার করে, কারণ userbot
# session ইতিমধ্যে সেখানে সেট করা আছে ও bot+account দুটোই ওই
# channel/group-এ admin।
# ============================================================

import os
import re
import asyncio
import logging
import time

from telegram import Poll
from telegram.error import TelegramError, RetryAfter

logger = logging.getLogger("atlas.poll_copy")



API_ID      = int(os.environ.get("API_ID", "33312774"))
API_HASH    = os.environ.get("API_HASH", "883db3366f8759d1d14c861c0d628232")
SESSION_STR = os.environ.get("SESSION_STRING", "")

# How long to wait between posting each live poll into the destination
# chat -- Telegram Bot API itself rate-limits sendPoll in a busy group,
# and this also keeps the feed readable instead of a wall of polls
# landing in under a second.
POLL_REPOST_DELAY = 1.2


# ── Link parser (identical contract to QuizBot's poll_extract.py) ──
def parse_tg_link(link: str):
    """
    Returns (channel_entity, msg_id, topic_id)
    Private:       t.me/c/123/456       → (int(-100123), 456, None)
    Private topic: t.me/c/123/3/456     → (int(-100123), 456, 3)
    Public:        t.me/mychan/456       → ("mychan", 456, None)
    Public topic:  t.me/mychan/3/456    → ("mychan", 456, 3)
    """
    link = link.strip().rstrip("/")
    m = re.search(r"t\.me/c/(\d+)/(\d+)/(\d+)", link)
    if m:
        return int(f"-100{m.group(1)}"), int(m.group(3)), int(m.group(2))
    m = re.search(r"t\.me/c/(\d+)/(\d+)", link)
    if m:
        return int(f"-100{m.group(1)}"), int(m.group(2)), None
    m = re.search(r"t\.me/([A-Za-z0-9_]+)/(\d+)/(\d+)", link)
    if m:
        return m.group(1), int(m.group(3)), int(m.group(2))
    m = re.search(r"t\.me/([A-Za-z0-9_]+)/(\d+)", link)
    if m:
        return m.group(1), int(m.group(2)), None
    return None, None, None


class _AdaptiveRateLimiter:
    """Same proactive+reactive limiter as QuizBot's poll_extract.py --
    keeps Telethon SendVoteRequest calls under Telegram's real limits
    instead of hitting FloodWait reactively."""
    def __init__(self):
        self.current_delay = 0.8
        self.flood_hits = 0
        self.max_per_minute = 20
        self.request_times = []

    def register_flood_wait(self, wait_seconds: float):
        self.flood_hits += 1
        self.current_delay = min(self.current_delay * 2.5, 15.0)
        self.max_per_minute = max(5, int(self.max_per_minute * 0.5))
        logger.warning(f"[poll_copy] FloodWait #{self.flood_hits} ({wait_seconds}s) — delay {self.current_delay:.1f}s")

    async def wait_before_request(self):
        now = time.monotonic()
        self.request_times = [t for t in self.request_times if now - t < 60]
        if len(self.request_times) >= self.max_per_minute:
            oldest = self.request_times[0]
            wait_needed = 60 - (now - oldest) + 0.5
            if wait_needed > 0:
                await asyncio.sleep(wait_needed)
        await asyncio.sleep(self.current_delay)
        self.request_times.append(time.monotonic())


_rate_limiter = _AdaptiveRateLimiter()


def _extract_poll_results_from_updates(vote_res):
    """SendVoteRequest returns an Updates object, not a Poll directly --
    the actual results live inside vote_res.updates as an UpdateMessagePoll."""
    try:
        for upd in getattr(vote_res, "updates", []):
            if hasattr(upd, "results") and hasattr(upd, "poll_id"):
                return upd.results
    except Exception:
        pass
    return None


async def _process_single_poll(client, channel, message):
    """Vote on the poll (guaranteed, infinite retry unless the message/poll
    itself is gone) so Telegram reveals which option is correct + any
    solution/explanation text, then return the MCQ as a plain dict."""
    from telethon.tl import functions
    from telethon.errors import FloodWaitError

    p = message.poll.poll
    q_text = p.question.text if hasattr(p.question, "text") else str(p.question)

    options = []
    for ans in p.answers:
        opt = ans.text.text if hasattr(ans.text, "text") else str(ans.text)
        options.append(opt)

    def _parse_results(res):
        cidx, expl = 0, ""
        found = False
        if res and getattr(res, "results", None):
            for i, r in enumerate(res.results):
                if getattr(r, "correct", False):
                    cidx = i
                    found = True
                    break
        if res and getattr(res, "solution", None):
            expl = res.solution
        return cidx, expl, found

    correct_idx, explanation, found = 0, "", False
    try:
        correct_idx, explanation, found = _parse_results(message.poll.results)
    except Exception:
        pass

    max_wait = 6.0
    attempt = 0
    while not found:
        attempt += 1
        await _rate_limiter.wait_before_request()
        try:
            vote_res = await client(functions.messages.SendVoteRequest(
                peer=channel, msg_id=message.id, options=[p.answers[0].option]
            ))
            vote_poll_results = _extract_poll_results_from_updates(vote_res)
            if vote_poll_results:
                correct_idx, explanation, found = _parse_results(vote_poll_results)
        except FloodWaitError as fw:
            logger.warning(f"[poll_copy] msg {message.id}: FloodWait {fw.seconds}s")
            _rate_limiter.register_flood_wait(fw.seconds)
            await asyncio.sleep(fw.seconds + 1)
        except Exception:
            pass  # already voted -- fine, fall through to refetch

        if found:
            break

        wait = min(1.0 + attempt * 0.5, max_wait)
        await asyncio.sleep(wait)
        await _rate_limiter.wait_before_request()
        try:
            fetched = await client.get_messages(channel, ids=message.id)
            if not fetched or not fetched.poll:
                logger.warning(f"[poll_copy] msg {message.id}: message/poll gone — best-effort fallback")
                break
            correct_idx, explanation, found = _parse_results(fetched.poll.results)
        except Exception:
            pass

        if found:
            break

        if attempt % 5 == 0:
            await _rate_limiter.wait_before_request()
            try:
                fresh_entity = await client.get_entity(channel)
                fetched = await client.get_messages(fresh_entity, ids=message.id)
                if fetched and fetched.poll:
                    correct_idx, explanation, found = _parse_results(fetched.poll.results)
            except Exception:
                pass

    if len(options) > 4:
        if found and correct_idx >= 4:
            options = options[:3] + [options[correct_idx]]
            correct_idx = 3
        else:
            options = options[:4]

    return {
        "question": q_text[:290],  # Telegram poll question hard cap is 300 chars
        "options": [o[:100] for o in options],  # option hard cap is 100 chars
        "correct_idx": correct_idx,
        "explanation": (explanation or "")[:195],  # explanation hard cap is 200 chars
    }, found


async def extract_polls_telethon(channel, start_id: int, end_id: int, topic_id=None, progress_cb=None) -> list:
    """Scans start_id..end_id and returns a list of extracted MCQ dicts, in
    original order. When topic_id is given, uses Telegram's own server-side
    reply_to=topic_id filter (same reliable approach as QuizBot's
    poll_extract.py) instead of guessing at raw reply_to attribute shapes."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import FloodWaitError

    polls = []
    client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)
    await client.start()
    try:
        entity = await client.get_entity(channel)
        checked = 0
        found_count = 0
        t0 = time.monotonic()

        quiz_messages = []
        if topic_id is not None:
            # Server-side topic filter -- Telegram only returns messages
            # actually in this forum topic, no manual attribute matching.
            attempts = 0
            while True:
                try:
                    async for message in client.iter_messages(
                        entity, reply_to=topic_id, min_id=start_id - 1, max_id=end_id + 1, reverse=True
                    ):
                        checked += 1
                        if message.poll and getattr(message.poll.poll, "quiz", False):
                            quiz_messages.append(message)
                        if progress_cb:
                            await progress_cb(checked, len(quiz_messages), time.monotonic() - t0)
                    break
                except FloodWaitError as fw:
                    _rate_limiter.register_flood_wait(fw.seconds)
                    await asyncio.sleep(fw.seconds + 1)
                except Exception as e:
                    attempts += 1
                    logger.warning(f"[poll_copy] topic-scan error (attempt {attempts}): {e}")
                    if attempts >= 5:
                        raise
                    await asyncio.sleep(2.0 * attempts)
        else:
            ids = list(range(start_id, end_id + 1))
            BATCH = 100
            for i in range(0, len(ids), BATCH):
                batch_ids = ids[i:i + BATCH]
                messages = await client.get_messages(entity, ids=batch_ids)
                for message in messages:
                    checked += 1
                    if message and getattr(message, "poll", None) and getattr(message.poll.poll, "quiz", False):
                        quiz_messages.append(message)
                if progress_cb:
                    await progress_cb(checked, len(quiz_messages), time.monotonic() - t0)

        for message in quiz_messages:
            mcq, ok = await _process_single_poll(client, entity, message)
            if mcq["question"]:
                polls.append(mcq)
                found_count += 1
            await asyncio.sleep(_rate_limiter.current_delay)
    finally:
        await client.disconnect()
    return polls


async def run_poll_copy(update, context, links: list):
    """Core logic shared by /pollcopy command and the DM-2-links shortcut.
    links must be exactly 2 t.me URLs (order doesn't matter, smaller msg_id
    is treated as range start automatically)."""
    chat_id = update.effective_chat.id
    ch1, start_id, topic1 = parse_tg_link(links[0])
    ch2, end_id, topic2 = parse_tg_link(links[1])

    if not ch1 or not start_id or not end_id:
        await update.message.reply_text("❌ Link parse হয়নি। সঠিক Telegram link দাও।")
        return
    if ch1 != ch2:
        await update.message.reply_text("❌ দুটো link একই channel/group এর হতে হবে!")
        return

    topic_id = topic1 or topic2
    if start_id > end_id:
        start_id, end_id = end_id, start_id

    total = end_id - start_id + 1

    if not SESSION_STR:
        await update.message.reply_text("❌ SESSION_STRING সেট করা নেই। Environment secrets এ add করো।")
        return

    status = await update.message.reply_text(f"⏳ Scan করছি: {start_id} → {end_id} ({total} messages)...")

    async def progress(checked, found, elapsed):
        try:
            await status.edit_text(f"⏳ চেক: {checked}/{total} — Poll পেয়েছি: {found}")
        except Exception:
            pass

    try:
        mcqs = await extract_polls_telethon(ch1, start_id, end_id, topic_id=topic_id, progress_cb=progress)
    except Exception as e:
        logger.error(f"[pollcopy] Telethon error: {e}")
        await status.edit_text(f"❌ Error: {e}")
        return

    if not mcqs:
        await status.edit_text(f"😕 এই range এ কোনো quiz poll পাওয়া যায়নি।\n({total} messages চেক হয়েছে)")
        return

    await status.edit_text(f"✅ {len(mcqs)}টি poll পাওয়া গেছে। নতুন করে পাঠানো হচ্ছে...")

    sent = 0
    for mcq in mcqs:
        options = mcq["options"]
        correct_id = mcq["correct_idx"]
        if correct_id >= len(options) or correct_id < 0:
            correct_id = 0
        try:
            await context.bot.send_poll(
                chat_id=chat_id,
                question=mcq["question"] or "প্রশ্ন",
                options=options if len(options) >= 2 else options + ["N/A"],
                type=Poll.QUIZ,
                correct_option_id=correct_id,
                explanation=mcq["explanation"] or None,
                is_anonymous=True,
            )
            sent += 1
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await context.bot.send_poll(
                    chat_id=chat_id, question=mcq["question"] or "প্রশ্ন",
                    options=options if len(options) >= 2 else options + ["N/A"],
                    type=Poll.QUIZ, correct_option_id=correct_id,
                    explanation=mcq["explanation"] or None, is_anonymous=True,
                )
                sent += 1
            except Exception as e2:
                logger.warning(f"[pollcopy] retry-after send failed: {e2}")
        except TelegramError as e:
            logger.warning(f"[pollcopy] send_poll failed: {e}")
        await asyncio.sleep(POLL_REPOST_DELAY)

    await update.message.reply_text(f"✅ সম্পন্ন! {sent}/{len(mcqs)}টি poll নতুন করে পাঠানো হয়েছে।")


async def handle_dm_poll_links(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> bool:
    """DM-only: if the admin sends exactly 2 t.me links (no command, any
    order, newline/space separated) it's treated as a poll-copy request --
    extract quiz polls in that range and repost them as brand-new live
    polls the admin can immediately re-solve. Returns True if handled (so
    the caller can stop the handler chain), False otherwise so normal text
    flows continue untouched."""
    if update.effective_chat.type != "private":
        return False
    from bot import is_admin, get_user_info
    user = get_user_info(update)
    if not is_admin(user['user_id']):
        return False
    text = (update.message.text or "").strip()
    if not text or text.startswith("/"):
        return False
    links = [l.strip() for l in re.split(r'[\s\n]+', text) if "t.me/" in l]
    if len(links) != 2:
        return False
    await run_poll_copy(update, context, links)
    return True


async def handle_pollcopy_command(update, context):
    """
    /pollcopy
    https://t.me/c/.../101
    https://t.me/c/.../250

    Extracts every quiz poll in that range (topic-filtered if a topic link
    is given) and reposts each as a BRAND NEW live quiz poll in this chat --
    users can solve them fresh, independent of the original polls' state.
    Kept as a fallback entry point alongside the DM-2-links shortcut.
    """
    from bot import is_admin, get_user_info  # local import avoids circular import at module load

    user = get_user_info(update)
    if not is_admin(user['user_id']):
        await update.message.reply_text("❌ এই কমান্ড শুধু admin ব্যবহার করতে পারবে।")
        return

    text = update.message.text or ""
    body = re.sub(r"^/pollcopy\s*", "", text, flags=re.IGNORECASE).strip()
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    links = [l for l in lines if "t.me/" in l]

    if len(links) < 2:
        await update.message.reply_text(
            "❌ দুটো link দাও!\n\n"
            "📌 Format:\n"
            "/pollcopy\n"
            "https://t.me/c/.../101\n"
            "https://t.me/c/.../250\n\n"
            "• প্রথম link = range start\n"
            "• দ্বিতীয় link = range end\n"
            "• Poll গুলো নতুন করে এই চ্যাটে পাঠানো হবে, user আবার solve করতে পারবে।"
        )
        return

    await run_poll_copy(update, context, links[:2])
