"""
ATLAS AtlasBot — Exam Server (FastAPI)
Single-file: all HTML/CSS/JS generated inside Python.
Stack: Telegram -> CF Worker -> HF Space (this) -> Supabase
Routes through CF Worker for all Telegram API + file access.
"""

import os
import json
import uuid
import base64
import asyncio
import logging
from datetime import datetime

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

# ============================================
# CONFIG (HF secrets via env)
# ============================================
CF_WORKER_URL = os.getenv("CF_WORKER_URL", "https://atlas-mcq-worker.hamza818483.workers.dev").rstrip("/")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BASE_URL = os.getenv("BASE_URL", "https://hamzaHF1-atlasbot.hf.space").rstrip("/")
CHROMIUM_PATH = os.getenv("CHROMIUM_PATH", "/usr/bin/chromium")

MAX_REGEN = 5  # max New Exam regenerations

logging.basicConfig(level=logging.INFO, format="%(asctime)s [EXAM] %(message)s")
log = logging.getLogger("exam").info

app = FastAPI(title="ATLAS Exam Server")

# ============================================
# IN-MEMORY EXAM STORE
# ============================================
# cache_id -> {mcqs, topic, page, tag, image_file_id, is_new_gen, regen_count, src_cache_id}
exam_store = {}


def store_exam(quiz_id, mcqs, topic="", page=1, tag="", image_file_id="",
               is_new_gen=False, src_cache_id=None):
    """Store exam data in memory. Called from bot.py."""
    exam_store[quiz_id] = {
        "mcqs": mcqs,
        "topic": topic,
        "page": page,
        "tag": tag,
        "image_file_id": image_file_id,
        "is_new_gen": is_new_gen,
        "regen_count": 0,
        "src_cache_id": src_cache_id or quiz_id,
        "created_at": datetime.utcnow().isoformat(),
    }
    log(f"Exam stored: {quiz_id} ({len(mcqs)} q, new_gen={is_new_gen})")
    return quiz_id


def create_exam_link(quiz_id, mcqs):
    """Store exam and return public URL. Called from bot.py."""
    try:
        from database import get_all_settings
        s = get_all_settings() or {}
    except Exception:
        s = {}
    topic = s.get("quiz_topic", "")
    tag = s.get("quiz_tag", "")
    page = int(s.get("quiz_page", 1) or 1)
    img = s.get("quiz_image_file_id", "")
    store_exam(quiz_id, mcqs, topic=topic, page=page, tag=tag, image_file_id=img)
    return f"{BASE_URL}/exam/{quiz_id}"


def _get_exam(cache_id):
    """Memory first, then Supabase rehydrate."""
    if cache_id in exam_store:
        return exam_store[cache_id]
    try:
        from database import get_mcq, is_new_gen as _ing
        row = get_mcq(cache_id)
        if row:
            store_exam(
                cache_id,
                row.get("mcqs", []),
                topic=row.get("topic", ""),
                page=int(row.get("page", 1) or 1),
                tag=row.get("tag", ""),
                image_file_id=row.get("image_file_id", ""),
                is_new_gen=bool(row.get("is_new_gen", False)),
            )
            return exam_store[cache_id]
    except Exception as e:
        log(f"_get_exam rehydrate fail: {e}")
    return None


# ============================================
# CF WORKER PROXY (Telegram file fetch)
# ============================================
async def _tg_file_bytes(file_id):
    """Download a Telegram file via CF Worker proxy. Returns bytes or None."""
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{CF_WORKER_URL}/tg-file", params={"file_id": file_id})
            if r.status_code == 200 and r.content:
                return r.content
    except Exception as e:
        log(f"tg-file fetch fail: {e}")
    return None


# ============================================
# ROUTES — health
# ============================================
@app.get("/health")
async def health():
    return PlainTextResponse("OK")


# ============================================
# ROUTES — serve exam page
# ============================================
@app.get("/exam/{cache_id}", response_class=HTMLResponse)
async def serve_exam(cache_id: str):
    data = _get_exam(cache_id)
    if not data:
        return HTMLResponse(_not_found_html(), status_code=404)
    return HTMLResponse(generate_exam_html(cache_id, data))


# ============================================
# ROUTES — exam JSON
# ============================================
@app.get("/api/exam/{cache_id}")
async def api_exam(cache_id: str):
    data = _get_exam(cache_id)
    if not data:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {
        "mcqs": data["mcqs"],
        "topic": data["topic"],
        "page": data["page"],
        "tag": data["tag"],
        "image_file_id": data["image_file_id"],
        "is_new_gen": data["is_new_gen"],
    }


# ============================================
# ROUTES — TG image proxy (pre-exam screen)
# ============================================
@app.get("/api/tg-image/{file_id}")
async def api_tg_image(file_id: str):
    raw = await _tg_file_bytes(file_id)
    if not raw:
        return PlainTextResponse("image unavailable", status_code=404)
    # detect content type by magic bytes (jpg/png/webp)
    ct = "image/jpeg"
    if raw[:8].startswith(b"\x89PNG"):
        ct = "image/png"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        ct = "image/webp"
    from fastapi.responses import Response
    return Response(content=raw, media_type=ct,
                    headers={"Cache-Control": "public, max-age=3600"})


# ============================================
# ROUTES — submit result
# ============================================
@app.post("/api/exam/result")
async def api_result(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad_json"}, status_code=400)

    cache_id = body.get("cache_id", "")
    user_id = body.get("user_id", "anonymous")
    user_name = (body.get("user_name") or "Anonymous").strip()[:40]
    correct = int(body.get("correct", 0))
    wrong = int(body.get("wrong", 0))
    skipped = int(body.get("skipped", 0))
    time_taken = int(body.get("time_taken", 0))

    total = correct + wrong + skipped
    final_score = round(correct - wrong * 0.25, 2)

    data = _get_exam(cache_id)
    is_ng = bool(data and data.get("is_new_gen"))

    # save leaderboard only for original (non new-gen) exams
    if not is_ng:
        try:
            from database import save_leaderboard_entry
            save_leaderboard_entry({
                "quiz_id": cache_id,
                "user_id": str(user_id),
                "user_name": user_name,
                "final_score": final_score,
                "correct": correct,
                "wrong": wrong,
                "skipped": skipped,
                "total": total,
                "time_taken": time_taken,
            })
        except Exception as e:
            log(f"save_leaderboard fail: {e}")

    motivation, ayat = _pick_feedback(correct, total)
    return {"motivation": motivation, "ayat": ayat,
            "final_score": final_score, "leaderboard_disabled": is_ng}


# ============================================
# ROUTES — new exam (regenerate from same image)
# ============================================
@app.post("/api/new-exam")
async def api_new_exam(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad_json"}, status_code=400)

    cache_id = body.get("cache_id", "")
    src = _get_exam(cache_id)
    if not src:
        return JSONResponse({"error": "not_found"}, status_code=404)

    root_id = src.get("src_cache_id", cache_id)
    root = exam_store.get(root_id, src)
    if root.get("regen_count", 0) >= MAX_REGEN:
        return JSONResponse({"ok": False, "error": "limit",
                             "message": f"সর্বোচ্চ {MAX_REGEN} বার নতুন এক্সাম বানানো যায়।"})

    file_id = src.get("image_file_id", "")
    if not file_id:
        return JSONResponse({"ok": False, "error": "no_image",
                             "message": "এই এক্সামে কোনো source image নেই।"})

    img_bytes = await _tg_file_bytes(file_id)
    if not img_bytes:
        return JSONResponse({"ok": False, "error": "image_fail",
                             "message": "ছবি লোড করা যায়নি।"})

    try:
        from database import get_prompt
        mixed_prompt = get_prompt("mixed") or get_prompt("default") or ""
    except Exception:
        mixed_prompt = ""

    try:
        import mcq_generator
        gen = mcq_generator.generate_from_image(
            img_bytes, prompt_override=mixed_prompt, count=15)
        if asyncio.iscoroutine(gen):
            gen = await gen
        new_mcqs = gen if isinstance(gen, list) else gen.get("mcqs", [])
    except Exception as e:
        log(f"new-exam generate fail: {e}")
        return JSONResponse({"ok": False, "error": "gen_fail",
                             "message": "প্রশ্ন তৈরি ব্যর্থ হয়েছে।"})

    if not new_mcqs:
        return JSONResponse({"ok": False, "error": "empty",
                             "message": "নতুন প্রশ্ন পাওয়া যায়নি।"})

    new_id = uuid.uuid4().hex[:12]
    store_exam(new_id, new_mcqs,
               topic=src.get("topic", ""), page=src.get("page", 1),
               tag=src.get("tag", ""), image_file_id=file_id,
               is_new_gen=True, src_cache_id=root_id)
    root["regen_count"] = root.get("regen_count", 0) + 1
    exam_store[root_id] = root
    return {"ok": True, "new_cache_id": new_id}


# ============================================
# ROUTES — leaderboard
# ============================================
@app.get("/api/leaderboard/{cache_id}")
async def api_leaderboard(cache_id: str):
    data = _get_exam(cache_id)
    if data and data.get("is_new_gen"):
        return {"disabled": True}
    qid = data.get("src_cache_id", cache_id) if data else cache_id
    try:
        from database import get_leaderboard
        rows = get_leaderboard(qid) or []
    except Exception as e:
        log(f"leaderboard fail: {e}")
        rows = []
    out = [{
        "user_name": r.get("user_name", "Anonymous"),
        "final_score": r.get("final_score", 0),
        "correct": r.get("correct", 0),
        "total": r.get("total", 0),
    } for r in rows]
    return {"data": out}


# ============================================
# ROUTES — bookmark add / remove
# ============================================
@app.post("/api/bookmark")
async def api_bookmark_add(request: Request):
    try:
        body = await request.json()
        from database import add_bookmark
        ok = add_bookmark(body.get("user_id", "anonymous"), {
            "question_text": body.get("question_text", ""),
            "option1": body.get("option1", ""),
            "option2": body.get("option2", ""),
            "option3": body.get("option3", ""),
            "option4": body.get("option4", ""),
            "answer_index": body.get("answer_index", 0),
            "explanation": body.get("explanation", ""),
            "exam_name": body.get("exam_name", "ATLAS Exam"),
        })
        return {"success": bool(ok)}
    except Exception as e:
        log(f"bookmark add fail: {e}")
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.delete("/api/bookmark")
async def api_bookmark_del(request: Request):
    try:
        body = await request.json()
        try:
            from database import remove_bookmark
            ok = remove_bookmark(body.get("user_id", "anonymous"),
                                 body.get("question_text", ""))
        except Exception:
            ok = True  # client-side localStorage is source of truth fallback
        return {"success": bool(ok)}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


# ============================================
# ROUTES — Solve PDF (Chromium + Playwright)
# ============================================
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

    html = generate_solve_pdf_html(data, answers)
    try:
        pdf_bytes = await _render_pdf(html)
    except Exception as e:
        log(f"pdf render fail: {e}")
        return JSONResponse({"ok": False, "message": "PDF তৈরি ব্যর্থ।"}, status_code=500)

    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    return {"ok": True, "pdf_base64": b64,
            "filename": f"ATLAS_Solve_{cache_id}.pdf"}


async def _render_pdf(html):
    from playwright.async_api import async_playwright
    flags = ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
             "--single-process", "--headless=new"]
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROMIUM_PATH, args=flags)
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="networkidle")
            await page.wait_for_timeout(800)  # font settle
            pdf = await page.pdf(format="A4", print_background=True,
                                 margin={"top": "14mm", "bottom": "14mm",
                                         "left": "10mm", "right": "10mm"})
            return pdf
        finally:
            await browser.close()


# ============================================
# FEEDBACK (motivation + ayat by score band)
# ============================================
_AYATS = [
    "পড়ো তোমার প্রভুর নামে যিনি সৃষ্টি করেছেন... (সূরা আলাক: ১)",
    "আল্লাহ ধৈর্যশীলদের সাথে আছেন... (সূরা বাকারা: ১৫৩)",
    "নিশ্চয়ই কষ্টের সাথেই স্বস্তি আছে... (সূরা ইনশিরাহ: ৫-৬)",
    "মানুষ যা চেষ্টা করে, সে তাই পায়... (সূরা নাজম: ৩৯)",
    "আল্লাহ কাউকে তার সাধ্যের বাইরে দায়িত্ব দেন না... (সূরা বাকারা: ২৮৬)",
]


def _pick_feedback(correct, total):
    import random
    pct = (correct / total * 100) if total else 0
    if pct >= 80:
        msg = "🏆 অসাধারণ! অনেক ভালো করেছো!"
    elif pct >= 60:
        msg = "👍 ভালো হয়েছে! আরেকটু চেষ্টা করলেই সেরা হবে।"
    elif pct >= 40:
        msg = "💪 চালিয়ে যাও — উন্নতি হচ্ছে।"
    else:
        msg = "📚 হাল ছেড়ো না, আবার চেষ্টা করো!"
    return msg, random.choice(_AYATS)


def _not_found_html():
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<style>body{background:#0A0D1E;color:#A8B4FF;font-family:sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh;"
            "text-align:center;margin:0}</style></head><body><div>"
            "<h2>⚠️ এক্সাম পাওয়া যায়নি</h2>"
            "<p>লিংকটি মেয়াদোত্তীর্ণ বা ভুল হতে পারে।</p></div></body></html>")


# ============================================
# SHARED CSS  (AtlasBoss dark theme — exact tokens)
# ============================================
def _css():
    return """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=Noto+Sans+Bengali:wght@400;500;600;700;800&display=swap');
:root{
  --bg:#F0F2F8;--bg-secondary:#FFFFFF;--bg-tertiary:#E8EBF5;
  --text:#1A1D2E;--text-secondary:#4A5270;--text-tertiary:#7A82A8;
  --accent:#5A5FE0;--accent-hover:#4349C8;--accent-light:#ECEEFF;--accent-glow:rgba(90,95,224,.18);
  --atlas-bg:#0E1225;--atlas-card:#141830;--atlas-text:#A8B4FF;--atlas-border:#5A5FE0;
  --atlas-glow:rgba(90,95,224,.32);--atlas-glow-strong:rgba(90,95,224,.55);
  --success:#0EA867;--success-light:#DCFBEE;--success-glow:rgba(14,168,103,.18);
  --error:#E53E3E;--error-light:#FEE8E8;--error-glow:rgba(229,62,62,.18);
  --warning:#D97706;--warning-light:#FEF5DC;--info:#2B7EDB;
  --border:#D4D8EE;--divider:#E2E5F2;--overlay:rgba(10,12,30,.52);
  --shadow-sm:0 1px 4px rgba(30,35,90,.08);--shadow:0 2px 10px rgba(30,35,90,.11);
  --shadow-md:0 4px 18px rgba(30,35,90,.14);--shadow-lg:0 8px 34px rgba(30,35,90,.18);
  --radius:16px;--radius-md:12px;--radius-sm:8px;--radius-full:9999px;
  --card-bg:#FFFFFF;--card-hover:#F4F6FF;--option-bg:#F4F6FF;--option-border:#D4D8EE;
  --option-selected-bg:#ECEEFF;--option-selected-border:#5A5FE0;--btn-bg:#E8EBF8;
}
.dark-mode{
  --bg:#0A0D1E;--bg-secondary:#0F1528;--bg-tertiary:#161D35;
  --text:#E8ECFF;--text-secondary:#8892C8;--text-tertiary:#555E88;
  --accent:#7B82FF;--accent-hover:#9CA3FF;--accent-light:#1A1E40;--accent-glow:rgba(123,130,255,.22);
  --atlas-bg:#0A0D1E;--atlas-card:#12162E;--atlas-text:#A8B4FF;--atlas-border:#5A5FE0;
  --atlas-glow:rgba(123,130,255,.30);--atlas-glow-strong:rgba(123,130,255,.55);
  --success:#22D47A;--success-light:#052A18;--error:#F87171;--error-light:#2A0808;
  --warning:#FBBF24;--warning-light:#291A00;--border:#252E55;--divider:#1E2645;--overlay:rgba(0,0,0,.78);
  --shadow-sm:0 1px 4px rgba(0,0,0,.45);--shadow:0 2px 10px rgba(0,0,0,.55);
  --shadow-md:0 4px 18px rgba(0,0,0,.65);--shadow-lg:0 8px 34px rgba(0,0,0,.75);
  --card-bg:#12162E;--card-hover:#161D35;--option-bg:#161D35;--option-border:#252E55;
  --option-selected-bg:#1A1E40;--option-selected-border:#7B82FF;--btn-bg:#1E2645;
}
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth;-webkit-font-smoothing:antialiased}
body{font-family:'Inter','Noto Sans Bengali',system-ui,sans-serif;background:var(--bg);color:var(--text);
  min-height:100vh;overflow-x:hidden;line-height:1.65;transition:background .3s,color .3s}
.bn{font-family:'Noto Sans Bengali','Inter',sans-serif}
@keyframes fadeIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
@keyframes slideUp{from{transform:translateY(100%)}to{transform:translateY(0)}}
@keyframes timerPulse{0%,100%{opacity:1}50%{opacity:.5}}
.wrap{max-width:600px;margin:0 auto;width:100%}
.header{position:fixed;top:0;left:0;right:0;height:56px;background:var(--card-bg);
  border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;
  padding:0 16px;z-index:1000;box-shadow:var(--shadow-sm);backdrop-filter:blur(14px)}
.atlas-brand-box{display:inline-flex;align-items:center;gap:8px;padding:6px 14px;border-radius:8px;
  background:linear-gradient(135deg,#5A5FE0,#8B5CF6);color:#fff}
.dark-mode .atlas-brand-box{background:linear-gradient(135deg,#7B82FF,#A78BFA)}
.atlas-brand-text{font-size:16px;font-weight:800;letter-spacing:-.5px;color:#fff;white-space:nowrap}
.header-icon{background:var(--bg-tertiary);border:1px solid var(--border);color:var(--text);
  width:36px;height:36px;border-radius:var(--radius-full);font-size:16px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;transition:all .2s}
.header-icon:hover{background:var(--accent-light);border-color:var(--accent);color:var(--accent)}
.screen{display:none}
.screen.active{display:block;animation:fadeIn .25s}
/* PRE-EXAM */
.pre-wrap{padding:72px 16px 40px}
.pre-card{background:var(--card-bg);border:1px solid var(--border);border-radius:var(--radius);
  overflow:hidden;box-shadow:var(--shadow-md)}
.pre-img{width:100%;display:block;max-height:300px;object-fit:contain;background:var(--bg-tertiary)}
.pre-body{padding:18px}
.pre-title{font-size:18px;font-weight:800;margin-bottom:6px}
.pre-tag{display:inline-block;font-size:11px;font-weight:700;color:var(--accent);
  background:var(--accent-light);padding:3px 10px;border-radius:var(--radius-full);margin-bottom:12px}
.pre-meta{font-size:13px;color:var(--text-secondary);line-height:2;margin-bottom:16px}
.name-input{width:100%;padding:12px 14px;border:1.5px solid var(--border);border-radius:var(--radius-sm);
  background:var(--option-bg);color:var(--text);font-size:14px;font-family:inherit;margin-bottom:14px}
.name-input:focus{outline:none;border-color:var(--accent)}
.btn-start{width:100%;padding:15px;background:var(--accent);color:#fff;border:none;border-radius:var(--radius-sm);
  font-size:16px;font-weight:800;cursor:pointer;font-family:inherit;transition:filter .2s}
.btn-start:hover{filter:brightness(1.08)}
/* EXAM */
.timer-sticky{position:sticky;top:56px;z-index:100;background:var(--atlas-bg);border:1px solid var(--atlas-border);
  border-radius:var(--radius-sm);padding:10px 14px;display:flex;align-items:center;justify-content:space-between;
  margin:64px 14px 0;box-shadow:0 2px 10px var(--atlas-glow)}
.timer-text{font-size:18px;font-weight:800;color:var(--atlas-text);letter-spacing:2px;font-variant-numeric:tabular-nums}
.timer-warning{color:var(--error)!important;animation:timerPulse .5s infinite}
.progress-text{font-size:12px;font-weight:600;color:var(--atlas-text);opacity:.75}
.progress-bar-wrap{height:4px;background:rgba(255,255,255,.1);margin:8px 14px 12px;overflow:hidden;border-radius:2px}
.progress-bar-fill{height:100%;background:linear-gradient(90deg,var(--accent),#7c3aed);transition:width .4s;width:0}
.questions-area{padding:0 14px 90px}
.mcq-card{background:var(--card-bg);border:1px solid var(--border);border-radius:var(--radius);
  padding:16px;margin-bottom:12px;box-shadow:var(--shadow-sm);animation:fadeIn .2s}
.q-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.q-number{font-size:11px;color:var(--accent);font-weight:700;background:var(--accent-light);
  padding:3px 9px;border-radius:var(--radius-full)}
.icon-btn{background:none;border:1px solid var(--border);font-size:14px;cursor:pointer;padding:4px 7px;
  border-radius:6px;transition:.2s;opacity:.7;color:var(--text-secondary)}
.icon-btn:hover{opacity:1;border-color:var(--accent)}
.icon-btn.active{opacity:1;color:var(--warning);border-color:var(--warning)}
.q-text{font-size:14px;font-weight:600;margin-bottom:12px;line-height:1.7;color:var(--text)}
.q-text img{max-width:100%;border-radius:8px;margin:6px 0}
.option-item{display:flex;align-items:center;gap:10px;padding:11px 13px;margin-bottom:7px;
  background:var(--option-bg);border:1.5px solid var(--option-border);border-radius:var(--radius-sm);
  cursor:pointer;transition:all .18s;font-size:13px;color:var(--text)}
.option-item:hover:not(.dimmed):not(.revealed){border-color:var(--accent);background:var(--option-selected-bg)}
.option-item.selected{background:var(--option-selected-bg);border-color:var(--option-selected-border);cursor:default}
.option-item.dimmed{opacity:.4;cursor:not-allowed;pointer-events:none}
.option-item.correct-reveal{background:var(--success-light);border-color:var(--success)}
.option-item.wrong-reveal{background:var(--error-light);border-color:var(--error)}
.option-radio{width:20px;height:20px;border-radius:50%;border:2px solid var(--text-tertiary);
  display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:11px;font-weight:700;
  color:var(--text-tertiary);transition:all .18s}
.option-item.selected .option-radio{border-color:var(--option-selected-border);background:var(--option-selected-border);color:#fff}
.option-item.correct-reveal .option-radio{border-color:var(--success);background:var(--success);color:#fff}
.option-item.wrong-reveal .option-radio{border-color:var(--error);background:var(--error);color:#fff}
.option-text{flex:1;line-height:1.5}
.q-explain{margin-top:10px;padding:10px 12px;background:var(--accent-light);border-radius:var(--radius-sm);
  font-size:12px;color:var(--text-secondary);line-height:1.7;display:none}
.q-explain.show{display:block}
.submit-fixed{position:fixed;bottom:0;left:0;right:0;background:var(--success);color:#fff;border:none;
  padding:14px;font-size:16px;font-weight:700;cursor:pointer;z-index:100;box-shadow:0 -2px 16px var(--success-glow);
  transition:filter .2s;text-align:center;max-width:600px;margin:0 auto}
.submit-fixed:hover{filter:brightness(.92)}
.nav-fab{position:fixed;right:0;top:50%;transform:translateY(-50%);width:38px;height:38px;background:var(--accent);
  border:none;border-radius:10px 0 0 10px;color:#fff;font-size:15px;cursor:pointer;z-index:101;
  box-shadow:0 4px 12px var(--accent-glow);display:flex;align-items:center;justify-content:center}
.nav-overlay,.confirm-overlay{position:fixed;inset:0;background:var(--overlay);z-index:200;display:none}
.nav-overlay{align-items:flex-end}
.nav-overlay.active{display:flex}
.nav-popup{width:100%;max-width:600px;margin:0 auto;background:var(--card-bg);
  border-radius:var(--radius) var(--radius) 0 0;padding:16px;border:1px solid var(--border);
  animation:slideUp .2s;max-height:80vh;overflow-y:auto}
.nav-popup-title{font-size:14px;font-weight:700;color:var(--accent);margin-bottom:12px;text-align:center}
.nav-grid{display:grid;grid-template-columns:repeat(8,1fr);gap:5px}
@media(max-width:480px){.nav-grid{grid-template-columns:repeat(6,1fr)}}
.nav-num{aspect-ratio:1;border-radius:6px;background:var(--option-bg);border:1px solid var(--border);
  color:var(--text-secondary);font-size:11px;font-weight:600;cursor:pointer;display:flex;
  align-items:center;justify-content:center;transition:all .2s}
.nav-num.answered{background:var(--accent);border-color:var(--accent);color:#fff}
.nav-num.bm{box-shadow:0 0 0 2px var(--warning) inset}
.nav-stats{text-align:center;margin-top:10px;font-size:11px;color:var(--text-secondary)}
.nav-close{display:block;margin:12px auto 0;padding:8px 20px;background:var(--accent);color:#fff;border:none;
  border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;font-family:inherit}
.confirm-overlay{align-items:center;justify-content:center}
.confirm-overlay.active{display:flex}
.confirm-modal{background:var(--card-bg);border:1px solid var(--border);border-radius:var(--radius);
  padding:20px;max-width:400px;width:92%;text-align:center;box-shadow:var(--shadow-lg);animation:fadeIn .2s}
.confirm-title{font-size:16px;font-weight:700;margin-bottom:12px}
.confirm-stats{font-size:13px;color:var(--text-secondary);margin-bottom:16px;line-height:1.9}
.confirm-buttons{display:flex;gap:8px;justify-content:center}
.confirm-btn{padding:10px 20px;border-radius:var(--radius-sm);border:none;font-size:13px;font-weight:600;
  cursor:pointer;font-family:inherit}
.cb-back{background:var(--btn-bg);color:var(--text);border:1px solid var(--border)}
.cb-go{background:var(--success);color:#fff}
/* RESULT */
.result-wrap{padding:72px 14px 40px}
.result-score-card{background:linear-gradient(140deg,var(--atlas-bg),var(--atlas-card));
  border:1.5px solid var(--atlas-border);border-radius:var(--radius);padding:24px 18px;text-align:center;
  margin-bottom:14px;box-shadow:var(--shadow-md),0 0 24px var(--atlas-glow)}
.result-exam-name{font-size:18px;font-weight:800;color:#fff;margin-bottom:12px}
.result-big-score{font-size:42px;font-weight:900;color:var(--atlas-text);text-shadow:0 0 20px var(--atlas-glow-strong)}
.result-total{font-size:14px;color:var(--atlas-text);opacity:.75;margin-top:4px}
.result-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:14px 0}
.result-stat{background:var(--card-bg);border-radius:var(--radius-sm);padding:12px 8px;text-align:center;
  border:1px solid var(--border)}
.result-stat-val{font-size:18px;font-weight:800}
.result-stat-label{font-size:10px;color:var(--text-secondary);margin-top:2px}
.correct-val{color:var(--success)}.wrong-val{color:var(--error)}.skipped-val{color:var(--warning)}
.info-row{display:flex;justify-content:space-around;font-size:13px;color:var(--atlas-text)}
.motiv-card{background:var(--card-bg);border:1px solid var(--border);border-radius:var(--radius);
  padding:16px;text-align:center;margin-bottom:14px}
.motiv-msg{font-size:15px;font-weight:700;margin-bottom:8px}
.motiv-ayat{font-size:13px;color:var(--atlas-text);line-height:1.8}
.action-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px}
.act-btn{padding:12px;border-radius:var(--radius-sm);border:none;font-size:13px;font-weight:700;cursor:pointer;
  font-family:inherit;color:#fff;transition:filter .2s}
.act-btn:hover{filter:brightness(1.08)}
.act-btn:disabled{opacity:.4;cursor:not-allowed}
.b-again{background:var(--accent)}.b-new{background:#8B5CF6}.b-mistake{background:var(--error)}
.b-board{background:var(--warning)}.b-img{background:var(--info)}.b-pdf{background:var(--success)}
.b-web{background:#0EA5E9}.b-yt{background:#EF4444}
.solve-head{font-size:15px;font-weight:800;text-align:center;margin:18px 0 12px;color:var(--accent)}
.filter-row{display:flex;gap:6px;margin-bottom:12px;justify-content:center;flex-wrap:wrap}
.filter-btn{padding:7px 14px;border-radius:var(--radius-full);border:1px solid var(--border);
  background:var(--option-bg);color:var(--text-secondary);font-size:12px;font-weight:600;cursor:pointer;
  font-family:inherit;transition:all .2s}
.filter-btn.active{background:var(--accent);border-color:var(--accent);color:#fff}
.board-row{display:flex;align-items:center;gap:10px;padding:10px 12px;background:var(--card-bg);
  border:1px solid var(--border);border-radius:var(--radius-sm);margin-bottom:6px;font-size:13px}
.board-rank{width:26px;height:26px;border-radius:50%;background:var(--accent-light);color:var(--accent);
  display:flex;align-items:center;justify-content:center;font-weight:800;font-size:12px;flex-shrink:0}
.board-name{flex:1;font-weight:600}
.board-score{font-weight:800;color:var(--success)}
.toast{position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:var(--atlas-card);
  color:var(--atlas-text);padding:10px 18px;border-radius:var(--radius-full);font-size:13px;z-index:600;
  border:1px solid var(--atlas-border);box-shadow:var(--shadow-md);opacity:0;transition:opacity .3s;pointer-events:none}
.toast.show{opacity:1}
::-webkit-scrollbar{width:5px}::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
"""


# ============================================
# EXAM PAGE GENERATOR
# ============================================
def generate_exam_html(cache_id, data):
    mcqs = data["mcqs"]
    total = len(mcqs)
    topic = data.get("topic") or "ATLAS Exam"
    tag = data.get("tag") or ""
    page = data.get("page", 1)
    image_file_id = data.get("image_file_id") or ""
    is_new_gen = bool(data.get("is_new_gen"))

    cfg = {
        "cacheId": cache_id,
        "total": total,
        "topic": topic,
        "tag": tag,
        "page": page,
        "imageFileId": image_file_id,
        "isNewGen": is_new_gen,
        "mcqs": mcqs,
        "negPerWrong": 0.25,
        "secPerQ": 30,
        "maxRegen": MAX_REGEN,
        "websiteUrl": "https://atlascourses.com",
        "youtubeUrl": "https://youtube.com/@atlascourses",
    }
    cfg_json = json.dumps(cfg, ensure_ascii=False)
    css = _css()

    return f"""<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0">
<title>ATLAS · এক্সাম</title>
<style>{css}</style>
</head>
<body class="dark-mode">

<header class="header">
  <div class="atlas-brand-box"><span class="atlas-brand-text">ATLAS EXAM</span></div>
  <button class="header-icon" id="themeToggle" onclick="toggleTheme()">☀️</button>
</header>

<!-- SCREEN 1: PRE-EXAM -->
<section class="screen active" id="screenPre">
  <div class="wrap pre-wrap">
    <div class="pre-card">
      <img class="pre-img" id="preImg" alt="" style="display:none">
      <div class="pre-body">
        <div class="pre-title bn" id="preTitle"></div>
        <span class="pre-tag bn" id="preTag" style="display:none"></span>
        <div class="pre-meta bn" id="preMeta"></div>
        <input class="name-input bn" id="nameInput" placeholder="তোমার নাম লেখো..." maxlength="40">
        <button class="btn-start bn" onclick="startExam()">▶️ এক্সাম শুরু করুন</button>
      </div>
    </div>
  </div>
</section>

<!-- SCREEN 2: EXAM HALL -->
<section class="screen" id="screenExam">
  <div class="timer-sticky wrap" id="timerSticky">
    <span class="timer-text" id="timerText">--:--</span>
    <span class="progress-text bn" id="progressText">📈 ০%</span>
  </div>
  <div class="progress-bar-wrap wrap"><div class="progress-bar-fill" id="progressBarFill"></div></div>
  <div class="questions-area wrap" id="questionsArea"></div>
  <button class="submit-fixed bn" onclick="submitExam(false)">📤 Submit করুন</button>
  <button class="nav-fab" onclick="openNav()">📋</button>
</section>

<!-- SCREEN 3: RESULT -->
<section class="screen" id="screenResult">
  <div class="wrap result-wrap" id="resultContent"></div>
</section>

<!-- NAV POPUP -->
<div class="nav-overlay" id="navOverlay" onclick="closeNav()">
  <div class="nav-popup" onclick="event.stopPropagation()">
    <div class="nav-popup-title bn">📋 প্রশ্ন নেভিগেশন</div>
    <div class="nav-grid" id="navGrid"></div>
    <div class="nav-stats bn" id="navStats"></div>
    <button class="nav-close bn" onclick="closeNav()">✕ বন্ধ</button>
  </div>
</div>

<!-- CONFIRM MODAL -->
<div class="confirm-overlay" id="confirmOverlay">
  <div class="confirm-modal">
    <div class="confirm-title bn">⚠️ নিশ্চিত করুন</div>
    <div class="confirm-stats bn" id="confirmStats"></div>
    <div class="confirm-buttons">
      <button class="confirm-btn cb-back bn" onclick="closeConfirm()">🔙 Back</button>
      <button class="confirm-btn cb-go bn" onclick="doSubmit()">✅ Confirm Submit</button>
    </div>
  </div>
</div>

<div class="toast bn" id="toast"></div>

<script>
const CFG = {cfg_json};
{_exam_js()}
</script>
</body>
</html>"""


# ============================================
# EXAM PAGE JAVASCRIPT  (plain JS, no f-string)
# ============================================
def _exam_js():
    return r"""
const LABELS=['ক','খ','গ','ঘ','ঙ'];
const BN=['০','১','২','৩','৪','৫','৬','৭','৮','৯'];
function bn(n){return String(n).split('').map(c=>/[0-9]/.test(c)?BN[+c]:c).join('');}
const MCQS=CFG.mcqs;
let answers={}, bookmarks={}, examSeconds=CFG.total*CFG.secPerQ, totalSeconds=examSeconds;
let timer=null, submitted=false, isDark=true, userName='', activeCacheId=CFG.cacheId, regenLeft=CFG.maxRegen;

function ansIndex(q){let a=(q.answer!==undefined)?q.answer:(q.answer_index!==undefined?q.answer_index:0);return (typeof a==='number'&&a>0&&a<=5&&q.options&&q.options[a]===undefined)?a-1:a;}

// ---- THEME ----
function toggleTheme(){isDark=!isDark;document.body.classList.toggle('dark-mode',isDark);
  document.getElementById('themeToggle').textContent=isDark?'☀️':'🌙';
  try{localStorage.setItem('atlas-theme',isDark?'dark':'light');}catch(e){}}
try{if(localStorage.getItem('atlas-theme')==='light'){isDark=false;document.body.classList.remove('dark-mode');document.getElementById('themeToggle').textContent='🌙';}}catch(e){}

function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2200);}

// ---- PRE-EXAM ----
function initPre(){
  document.getElementById('preTitle').textContent='📝 '+CFG.topic+(CFG.page?(' — Page '+bn(CFG.page)):'');
  if(CFG.tag){const t=document.getElementById('preTag');t.textContent=CFG.tag;t.style.display='inline-block';}
  const mins=Math.floor(examSeconds/60), secs=examSeconds%60;
  document.getElementById('preMeta').innerHTML=
    '✅ '+bn(CFG.total)+'টি প্রশ্ন<br>⏱️ '+bn(String(mins).padStart(2,'0'))+':'+bn(String(secs).padStart(2,'0'))+' মিনিট<br>📊 Negative: -'+bn('0.25');
  if(CFG.imageFileId){const im=document.getElementById('preImg');im.src='/api/tg-image/'+encodeURIComponent(CFG.imageFileId);im.style.display='block';im.onerror=()=>{im.style.display='none';};}
  try{const n=localStorage.getItem('atlas-name');if(n)document.getElementById('nameInput').value=n;}catch(e){}
}
function startExam(){
  userName=(document.getElementById('nameInput').value||'').trim()||'Anonymous';
  try{localStorage.setItem('atlas-name',userName);}catch(e){}
  showScreen('screenExam');
  renderQuestions();startTimer();updateProgress();
  document.getElementById('timerText').textContent=fmt(examSeconds);
}
function showScreen(id){document.querySelectorAll('.screen').forEach(s=>s.classList.remove('active'));document.getElementById(id).classList.add('active');window.scrollTo(0,0);}

// ---- RENDER QUESTIONS ----
function renderQuestions(){
  let h='';
  MCQS.forEach((q,i)=>{
    const bm=bookmarks[i]?' active':'';
    h+='<div class="mcq-card" id="qCard'+i+'"><div class="q-header">'+
       '<span class="q-number bn">প্রশ্ন '+bn(i+1)+'/'+bn(CFG.total)+'</span>'+
       '<button class="icon-btn'+bm+'" id="bm'+i+'" onclick="toggleBookmark('+i+')">🔖</button></div>'+
       '<div class="q-text bn">'+(q.question||'')+'</div>';
    (q.options||[]).forEach((opt,oi)=>{
      h+='<div class="option-item bn" id="opt'+i+'_'+oi+'" data-q="'+i+'" onclick="selectOption('+i+','+oi+')">'+
         '<span class="option-radio">'+LABELS[oi]+'</span><span class="option-text">'+opt+'</span></div>';
    });
    h+='<div class="q-explain bn" id="exp'+i+'"></div></div>';
  });
  document.getElementById('questionsArea').innerHTML=h;
}
function selectOption(qi,oi){
  if(submitted)return;
  document.querySelectorAll('[data-q="'+qi+'"]').forEach(el=>{el.classList.remove('selected');el.classList.add('dimmed');});
  const el=document.getElementById('opt'+qi+'_'+oi);
  if(el){el.classList.add('selected');el.classList.remove('dimmed');}
  answers[qi]=oi;updateProgress();
}

// ---- TIMER ----
function startTimer(){stopTimer();timer=setInterval(()=>{examSeconds--;document.getElementById('timerText').textContent=fmt(examSeconds);
  if(examSeconds<=60)document.getElementById('timerText').classList.add('timer-warning');
  if(examSeconds<=0){stopTimer();doSubmit(true);}},1000);}
function stopTimer(){if(timer){clearInterval(timer);timer=null;}}
function fmt(s){if(s<0)s=0;const m=Math.floor(s/60),x=s%60;return bn(String(m).padStart(2,'0')+':'+String(x).padStart(2,'0'));}

// ---- PROGRESS ----
function updateProgress(){const a=Object.keys(answers).length,p=Math.round(a/CFG.total*100);
  document.getElementById('progressText').textContent='📈 '+bn(p)+'%';
  document.getElementById('progressBarFill').style.width=p+'%';}

// ---- BOOKMARK ----
function toggleBookmark(i){
  const q=MCQS[i];
  if(bookmarks[i]){delete bookmarks[i];document.getElementById('bm'+i)?.classList.remove('active');
    fetch('/api/bookmark',{method:'DELETE',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({user_id:userName,question_text:q.question})}).catch(()=>{});}
  else{bookmarks[i]=true;document.getElementById('bm'+i)?.classList.add('active');toast('🔖 বুকমার্ক হয়েছে');
    const ai=ansIndex(q);const o=q.options||[];
    fetch('/api/bookmark',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({user_id:userName,question_text:q.question,option1:o[0]||'',option2:o[1]||'',
        option3:o[2]||'',option4:o[3]||'',answer_index:ai,explanation:q.explanation||'',exam_name:CFG.topic})}).catch(()=>{});}
}

// ---- NAV ----
function openNav(){let h='';MCQS.forEach((q,i)=>{let c='nav-num';if(answers[i]!==undefined)c+=' answered';if(bookmarks[i])c+=' bm';
  h+='<button class="'+c+'" onclick="goQ('+i+')">'+bn(i+1)+'</button>';});
  document.getElementById('navGrid').innerHTML=h;
  document.getElementById('navStats').textContent='🟦 উত্তর: '+bn(Object.keys(answers).length)+' | ⬜ বাকি: '+bn(CFG.total-Object.keys(answers).length);
  document.getElementById('navOverlay').classList.add('active');}
function closeNav(){document.getElementById('navOverlay').classList.remove('active');}
function goQ(i){closeNav();document.getElementById('qCard'+i)?.scrollIntoView({behavior:'smooth',block:'center'});}

// ---- SUBMIT ----
function submitExam(auto){if(submitted)return;if(auto){doSubmit(true);return;}
  const a=Object.keys(answers).length,sk=CFG.total-a;
  document.getElementById('confirmStats').innerHTML='✅ উত্তর দিয়েছেন: '+bn(a)+'টি<br>⏭️ বাকি: '+bn(sk)+'টি<br>⚠️ জমা দিলে আর পরিবর্তন করা যাবে না।';
  document.getElementById('confirmOverlay').classList.add('active');}
function closeConfirm(){document.getElementById('confirmOverlay').classList.remove('active');}
function doSubmit(auto){
  document.getElementById('confirmOverlay').classList.remove('active');
  if(submitted)return;submitted=true;stopTimer();
  let correct=0,wrong=0,skipped=0;
  MCQS.forEach((q,i)=>{const ua=answers[i],ai=ansIndex(q);if(ua===undefined)skipped++;else if(ua===ai)correct++;else wrong++;});
  const neg=+(wrong*CFG.negPerWrong).toFixed(2);
  const finalScore=+(correct-neg).toFixed(2);
  const timeTaken=totalSeconds-examSeconds;
  postResult(correct,wrong,skipped,timeTaken,finalScore,neg);
}
function postResult(correct,wrong,skipped,timeTaken,finalScore,neg){
  fetch('/api/exam/result',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({cache_id:activeCacheId,user_id:userName,user_name:userName,correct,wrong,skipped,time_taken:timeTaken})})
   .then(r=>r.json()).then(d=>renderResult(correct,wrong,skipped,timeTaken,finalScore,neg,d))
   .catch(()=>renderResult(correct,wrong,skipped,timeTaken,finalScore,neg,{motivation:'✅ সম্পন্ন হয়েছে!',ayat:'',leaderboard_disabled:CFG.isNewGen}));
}
""" + _exam_js_result()


def _exam_js_result():
    return r"""
// ---- RESULT ----
function renderResult(correct,wrong,skipped,timeTaken,finalScore,neg,fb){
  revealAnswers();
  const pct=Math.round(correct/CFG.total*100);
  const boardDisabled=fb.leaderboard_disabled||CFG.isNewGen;
  let h='';
  h+='<div class="result-score-card"><div class="result-exam-name bn">📝 '+CFG.topic+'</div>'+
     '<div class="result-big-score">'+bn(finalScore.toFixed(2))+'</div>'+
     '<div class="result-total bn">/ '+bn(CFG.total)+' · ('+bn(pct)+'%)</div></div>';
  h+='<div class="result-grid">'+
     '<div class="result-stat"><div class="result-stat-val correct-val">✅ '+bn(correct)+'</div><div class="result-stat-label bn">সঠিক</div></div>'+
     '<div class="result-stat"><div class="result-stat-val wrong-val">❌ '+bn(wrong)+'</div><div class="result-stat-label bn">ভুল</div></div>'+
     '<div class="result-stat"><div class="result-stat-val skipped-val">⏭️ '+bn(skipped)+'</div><div class="result-stat-label bn">স্কিপ</div></div></div>';
  h+='<div class="result-score-card" style="padding:12px"><div class="info-row bn">'+
     '<span>⏱️ '+fmt(timeTaken)+'</span><span>📊 Negative: -'+bn(neg.toFixed(2))+'</span></div></div>';
  h+='<div class="motiv-card"><div class="motiv-msg bn">'+(fb.motivation||'')+'</div>'+
     (fb.ayat?('<div class="motiv-ayat bn">📖 '+fb.ayat+'</div>'):'')+'</div>';
  // actions
  h+='<div class="action-grid">'+
     '<button class="act-btn b-again bn" onclick="practiceAgain()">🔄 Again Practice</button>'+
     '<button class="act-btn b-new bn" id="btnNew" onclick="newExam()">🆕 New Exam</button>'+
     '<button class="act-btn b-mistake bn" onclick="mistakePractice()">❌ Mistake Practice</button>'+
     '<button class="act-btn b-board bn" '+(boardDisabled?'disabled title="New-gen এক্সামে লিডারবোর্ড নেই"':'onclick="showBoard()"')+'>🏆 Leaderboard</button>'+
     (CFG.imageFileId?'<button class="act-btn b-img bn" onclick="backToImage()">⚡ Back to Image</button>':'')+
     '<button class="act-btn b-pdf bn" id="btnPdf" onclick="solvePdf()">📄 Solve PDF</button>'+
     '<button class="act-btn b-web bn" onclick="ext(CFG.websiteUrl)">🌐 ATLAS Website</button>'+
     '<button class="act-btn b-yt bn" onclick="ext(CFG.youtubeUrl)">▶️ ATLAS YouTube</button></div>';
  // solve sheet
  h+='<div class="solve-head bn">── Solve Sheet ──</div>'+
     '<div class="filter-row">'+
     '<button class="filter-btn active bn" data-f="all" onclick="filterSolve(\'all\',this)">সব</button>'+
     '<button class="filter-btn bn" data-f="correct" onclick="filterSolve(\'correct\',this)">✅ সঠিক</button>'+
     '<button class="filter-btn bn" data-f="wrong" onclick="filterSolve(\'wrong\',this)">❌ ভুল</button>'+
     '<button class="filter-btn bn" data-f="skipped" onclick="filterSolve(\'skipped\',this)">⏭️ স্কিপ</button></div>'+
     '<div id="solveList">'+buildSolve('all')+'</div>';
  // image preview if exists
  let imgBlock='';
  if(CFG.imageFileId){imgBlock='<img id="resImg" class="pre-img" style="border-radius:12px;margin-bottom:14px" src="/api/tg-image/'+encodeURIComponent(CFG.imageFileId)+'" onerror="this.style.display=\'none\'">';}
  document.getElementById('resultContent').innerHTML=imgBlock+h;
  showScreen('screenResult');
}
function revealAnswers(){MCQS.forEach((q,i)=>{const ua=answers[i],ai=ansIndex(q);
  (q.options||[]).forEach((o,oi)=>{const el=document.getElementById('opt'+i+'_'+oi);if(!el)return;
    if(oi===ai)el.classList.add('correct-reveal');
    if(ua!==undefined&&oi===ua&&ua!==ai)el.classList.add('wrong-reveal');});
  if(q.explanation){const e=document.getElementById('exp'+i);if(e){e.innerHTML='📋 ব্যাখ্যা: '+q.explanation;e.classList.add('show');}}});}

function statusOf(i){const ua=answers[i],ai=ansIndex(MCQS[i]);if(ua===undefined)return'skipped';return ua===ai?'correct':'wrong';}
function buildSolve(filter){
  let h='';
  MCQS.forEach((q,i)=>{const st=statusOf(i);if(filter!=='all'&&st!==filter)return;
    const ai=ansIndex(q),ua=answers[i];
    const badge=st==='correct'?'✅ সঠিক':(st==='wrong'?'❌ ভুল':'⏭️ স্কিপ');
    h+='<div class="mcq-card"><div class="q-header"><span class="q-number bn">'+badge+' — প্রশ্ন '+bn(i+1)+'/'+bn(CFG.total)+'</span></div>'+
       '<div class="q-text bn">'+(q.question||'')+'</div>';
    (q.options||[]).forEach((o,oi)=>{let cls='option-item bn';if(oi===ai)cls+=' correct-reveal';else if(ua!==undefined&&oi===ua)cls+=' wrong-reveal';
      h+='<div class="'+cls+'"><span class="option-radio">'+LABELS[oi]+'</span><span class="option-text">'+o+'</span></div>';});
    if(q.explanation)h+='<div class="q-explain show bn">📋 ব্যাখ্যা: '+q.explanation+'</div>';
    h+='</div>';});
  return h||'<div class="motiv-card bn" style="color:var(--text-secondary)">এই ক্যাটাগরিতে কোনো প্রশ্ন নেই।</div>';
}
function filterSolve(f,btn){document.querySelectorAll('.filter-btn').forEach(b=>b.classList.remove('active'));btn.classList.add('active');
  document.getElementById('solveList').innerHTML=buildSolve(f);}

// ---- ACTIONS ----
function practiceAgain(){answers={};submitted=false;examSeconds=totalSeconds;
  document.getElementById('timerText').classList.remove('timer-warning');
  showScreen('screenExam');renderQuestions();startTimer();updateProgress();}
function mistakePractice(){const wrong=MCQS.map((q,i)=>statusOf(i)==='wrong'?i:-1).filter(i=>i>=0);
  if(!wrong.length){toast('🎉 কোনো ভুল নেই!');return;}
  window._fullMcqs=window._fullMcqs||MCQS.slice();
  const subset=wrong.map(i=>MCQS[i]);MCQS.length=0;subset.forEach(q=>MCQS.push(q));
  CFG.total=MCQS.length;answers={};submitted=false;examSeconds=CFG.total*CFG.secPerQ;totalSeconds=examSeconds;
  document.getElementById('timerText').classList.remove('timer-warning');
  showScreen('screenExam');renderQuestions();startTimer();updateProgress();toast('❌ ভুল প্রশ্ন প্র্যাকটিস');}
function backToImage(){showScreen('screenPre');document.getElementById('resImg');window.scrollTo(0,0);}
function ext(u){window.open(u,'_blank');}

function newExam(){const b=document.getElementById('btnNew');b.disabled=true;b.textContent='⏳ তৈরি হচ্ছে...';
  fetch('/api/new-exam',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cache_id:activeCacheId})})
   .then(r=>r.json()).then(d=>{if(d.ok&&d.new_cache_id){window.location.href='/exam/'+d.new_cache_id;}
     else{toast(d.message||'নতুন এক্সাম তৈরি ব্যর্থ।');b.disabled=false;b.textContent='🆕 New Exam';}})
   .catch(()=>{toast('সার্ভার সমস্যা।');b.disabled=false;b.textContent='🆕 New Exam';});}

function showBoard(){fetch('/api/leaderboard/'+activeCacheId).then(r=>r.json()).then(d=>{
  if(d.disabled){toast('New-gen এক্সামে লিডারবোর্ড নেই।');return;}
  const rows=d.data||[];let h='<div class="solve-head bn">🏆 লিডারবোর্ড</div>';
  if(!rows.length)h+='<div class="motiv-card bn" style="color:var(--text-secondary)">এখনো কেউ অংশ নেয়নি।</div>';
  rows.sort((a,b)=>b.final_score-a.final_score).slice(0,50).forEach((r,i)=>{
    h+='<div class="board-row"><div class="board-rank">'+bn(i+1)+'</div>'+
       '<div class="board-name bn">'+(r.user_name||'Anonymous')+'</div>'+
       '<div class="board-score bn">'+bn((+r.final_score).toFixed(2))+' / '+bn(r.total||CFG.total)+'</div></div>';});
  document.getElementById('resultContent').insertAdjacentHTML('beforeend','<div id="boardBlock">'+h+'</div>');
  document.getElementById('boardBlock').scrollIntoView({behavior:'smooth'});}).catch(()=>toast('লিডারবোর্ড লোড ব্যর্থ।'));}

function solvePdf(){const b=document.getElementById('btnPdf');b.disabled=true;b.textContent='⏳ PDF...';
  fetch('/api/solve-pdf',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({cache_id:activeCacheId,answers:answers})})
   .then(r=>r.json()).then(d=>{if(d.ok&&d.pdf_base64){
     const bin=atob(d.pdf_base64);const arr=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)arr[i]=bin.charCodeAt(i);
     const blob=new Blob([arr],{type:'application/pdf'});const url=URL.createObjectURL(blob);
     const a=document.createElement('a');a.href=url;a.download=d.filename||'ATLAS_Solve.pdf';a.click();
     setTimeout(()=>URL.revokeObjectURL(url),4000);toast('📄 PDF ডাউনলোড হচ্ছে');}
     else toast(d.message||'PDF তৈরি ব্যর্থ।');b.disabled=false;b.textContent='📄 Solve PDF';})
   .catch(()=>{toast('সার্ভার সমস্যা।');b.disabled=false;b.textContent='📄 Solve PDF';});}

// ---- BOOT ----
initPre();
"""


# ============================================
# SOLVE PDF HTML GENERATOR
# ============================================
def _ans_index(q):
    a = q.get("answer", q.get("answer_index", 0))
    opts = q.get("options", [])
    if isinstance(a, int) and a > 0 and a <= len(opts) and (a - 1) < len(opts):
        # heuristic: if answers look 1-based
        if a == len(opts) and len(opts) >= a:
            return a - 1
    return a if isinstance(a, int) and a < len(opts) else 0


def generate_solve_pdf_html(data, answers):
    mcqs = data["mcqs"]
    topic = data.get("topic") or "ATLAS Exam"
    tag = data.get("tag") or ""
    page = data.get("page", 1)
    labels = ["ক", "খ", "গ", "ঘ", "ঙ"]

    rows = ""
    for i, q in enumerate(mcqs):
        ai = q.get("answer", q.get("answer_index", 0))
        opts = q.get("options", [])
        if isinstance(ai, int) and ai >= len(opts) and ai > 0:
            ai = ai - 1
        ua = answers.get(str(i), answers.get(i))
        opts_html = ""
        for oi, opt in enumerate(opts):
            cls = ""
            if oi == ai:
                cls = "correct"
            elif ua is not None and oi == ua:
                cls = "wrong"
            opts_html += (f'<div class="opt {cls}"><span class="lbl">{labels[oi] if oi < len(labels) else oi+1}</span>'
                          f'<span>{opt}</span></div>')
        exp = q.get("explanation", "")
        exp_html = f'<div class="exp">📋 ব্যাখ্যা: {exp}</div>' if exp else ""
        rows += (f'<div class="qcard"><div class="qnum">প্রশ্ন {i+1}/{len(mcqs)}</div>'
                 f'<div class="qtext">{q.get("question","")}</div>{opts_html}{exp_html}</div>')

    tag_html = f'<span class="tag">{tag}</span>' if tag else ""
    return f"""<!DOCTYPE html><html lang="bn"><head><meta charset="UTF-8">
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Bengali:wght@400;500;600;700;800&family=Inter:wght@400;600;700&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Noto Sans Bengali','Inter',sans-serif;color:#1A1D2E;padding:0;font-size:13px;line-height:1.7}}
.head{{text-align:center;padding:18px;background:linear-gradient(135deg,#5A5FE0,#8B5CF6);color:#fff;border-radius:10px;margin-bottom:18px}}
.head h1{{font-size:20px;font-weight:800}}
.head .sub{{font-size:13px;opacity:.9;margin-top:4px}}
.tag{{display:inline-block;background:rgba(255,255,255,.25);padding:2px 10px;border-radius:20px;font-size:11px;margin-top:6px}}
.qcard{{border:1px solid #D4D8EE;border-radius:10px;padding:14px;margin-bottom:12px;page-break-inside:avoid}}
.qnum{{display:inline-block;font-size:11px;font-weight:700;color:#5A5FE0;background:#ECEEFF;padding:2px 8px;border-radius:12px;margin-bottom:8px}}
.qtext{{font-weight:600;margin-bottom:10px}}
.opt{{display:flex;align-items:center;gap:8px;padding:8px 10px;margin-bottom:5px;border:1.5px solid #D4D8EE;border-radius:7px}}
.opt .lbl{{width:18px;height:18px;border-radius:50%;border:1.5px solid #7A82A8;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:#7A82A8;flex-shrink:0}}
.opt.correct{{background:#DCFBEE;border-color:#0EA867}}
.opt.correct .lbl{{background:#0EA867;border-color:#0EA867;color:#fff}}
.opt.wrong{{background:#FEE8E8;border-color:#E53E3E}}
.opt.wrong .lbl{{background:#E53E3E;border-color:#E53E3E;color:#fff}}
.exp{{margin-top:8px;padding:8px 10px;background:#ECEEFF;border-radius:7px;font-size:12px;color:#4A5270}}
.foot{{text-align:center;font-size:11px;color:#7A82A8;margin-top:14px}}
</style></head><body>
<div class="head"><h1>📝 {topic}</h1><div class="sub">Page {page} · মোট {len(mcqs)}টি প্রশ্ন</div>{tag_html}</div>
{rows}
<div class="foot">— ATLAS Courses · atlascourses.com —</div>
</body></html>"""


# ============================================
# ENTRYPOINT
# ============================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "7860"))
    uvicorn.run(app, host="0.0.0.0", port=port)
