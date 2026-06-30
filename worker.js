// ATLAS BOT - Cloudflare Worker Proxy + Webhook (v2.0)
// URL: https://atlas-bot-proxy.hamza818483.workers.dev
// Routes:
//   POST /webhook/{TOKEN}        → forward update to HF Space
//   POST /tg-senddoc             → proxy sendDocument to Telegram (HF blocked)
//   GET  /tg-file/{TOKEN}/{path} → proxy file download from Telegram
//   *    everything else         → proxy to api.telegram.org

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // ============================================
    // -1. WEB EXAM — fully independent of HF/Render.
    // Serves static index.html (Cloudflare Assets) with quiz data
    // injected, data fetched from D1 cache -> Supabase fallback.
    // GET  /exam/{cache_id}        -> HTML page
    // GET  /api/exam/{cache_id}    -> raw JSON (used by page JS too)
    // POST /api/exam/result        -> save result to Supabase
    // GET  /api/tg-image/{file_id} -> proxy source image via Telegram
    // ============================================
    if (request.method === 'GET' && url.pathname.startsWith('/exam/')) {
      return await handleExamPage(request, url, env);
    }
    if (request.method === 'GET' && url.pathname.startsWith('/api/exam/') && !url.pathname.startsWith('/api/exam/result')) {
      const cacheId = url.pathname.replace('/api/exam/', '').split('?')[0].trim();
      const data = await fetchExamData(cacheId, env);
      if (!data) return jsonResp({ error: 'not_found' }, 404);
      return jsonResp(data);
    }
    if (request.method === 'POST' && url.pathname === '/api/exam/result') {
      return await handleExamResult(request, env);
    }
    if (request.method === 'GET' && url.pathname.startsWith('/api/tg-image/')) {
      const fileId = url.pathname.replace('/api/tg-image/', '').trim();
      return await handleTgImageProxy(fileId, env);
    }

    // ============================================
    // -0.5. INIT DB — creates exam_cache table (run once after deploy)
    // GET /init-db
    // ============================================
    if (request.method === 'GET' && url.pathname === '/init-db') {
      try {
        await env.DB.exec(
          "CREATE TABLE IF NOT EXISTS exam_cache (quiz_id TEXT PRIMARY KEY, mcqs TEXT, topic TEXT, page INTEGER DEFAULT 1, tag TEXT DEFAULT '', image_file_id TEXT DEFAULT '', chat_id TEXT, message_id TEXT, prompt_type TEXT DEFAULT 'prompt_1', created_at INTEGER DEFAULT (unixepoch()))"
        );
        return jsonResp({ ok: true, message: 'exam_cache table ready' });
      } catch (e) {
        return jsonResp({ ok: false, error: e.message }, 500);
      }
    }

    // ============================================
    // 0. D1 QUERY ROUTE — used by storage.py (ATLAS dual storage)
    // POST /d1/query   Header: X-D1-Token: {D1_TOKEN}
    // Body: {"sql": "...", "params": [...]}
    // ============================================
    if (request.method === 'POST' && url.pathname === '/d1/query') {
      const token = request.headers.get('X-D1-Token') || '';
      if (token !== env.D1_TOKEN) {
        return new Response('Unauthorized', { status: 401 });
      }
      try {
        const { sql, params } = await request.json();
        const stmt = env.DB.prepare(sql);
        const bound = (params && params.length) ? stmt.bind(...params) : stmt;
        const result = await bound.all();
        return new Response(JSON.stringify({
          ok: true,
          results: result.results || [],
          meta: result.meta || {}
        }), { headers: { 'Content-Type': 'application/json' } });
      } catch (e) {
        return new Response(JSON.stringify({ ok: false, error: String(e) }), {
          status: 500, headers: { 'Content-Type': 'application/json' }
        });
      }
    }

    // ============================================
    // 1. WEBHOOK — Telegram sends updates here
    // POST /webhook/{BOT_TOKEN}
    // ============================================
    if (request.method === 'POST' && url.pathname.startsWith('/webhook/')) {
      const token = url.pathname.split('/webhook/')[1];
      if (!token) return new Response('Unauthorized', { status: 401 });
      const body = await request.text();
      try {
        await fetch('https://hamzahf2-atlasbot.hf.space/webhook', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Bot-Token': token },
          body,
        });
      } catch (_) {}
      return new Response('OK', { status: 200 });
    }

    // ============================================
    // 2. FILE DOWNLOAD PROXY
    // GET /file/bot{TOKEN}/{file_path}
    // (python-telegram-bot uses base_file_url + /file/bot{TOKEN}/{path})
    // ============================================
    if (request.method === 'GET' && url.pathname.startsWith('/file/bot')) {
      const tgUrl = 'https://api.telegram.org' + url.pathname + url.search;
      try {
        const resp = await fetch(tgUrl);
        const newResp = new Response(resp.body, resp);
        newResp.headers.set('Access-Control-Allow-Origin', '*');
        return newResp;
      } catch (e) {
        return new Response(JSON.stringify({ ok: false, error: e.message }), {
          status: 502, headers: { 'Content-Type': 'application/json' },
        });
      }
    }

    // ============================================
    // 3. SEND DOCUMENT PROXY
    // POST /tg-senddoc
    // Body: multipart/form-data (forwarded as-is to api.telegram.org/bot{TOKEN}/sendDocument)
    // Header: X-Bot-Token: {TOKEN}
    // ============================================
    if (request.method === 'POST' && url.pathname === '/tg-senddoc') {
      const token = request.headers.get('X-Bot-Token') || '';
      if (!token) return new Response('Unauthorized', { status: 401 });
      const tgUrl = `https://api.telegram.org/bot${token}/sendDocument`;
      try {
        const body = await request.arrayBuffer();
        const resp = await fetch(tgUrl, {
          method: 'POST',
          headers: { 'Content-Type': request.headers.get('Content-Type') || 'multipart/form-data' },
          body,
        });
        const data = await resp.json();
        return new Response(JSON.stringify(data), {
          status: resp.status,
          headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
        });
      } catch (e) {
        return new Response(JSON.stringify({ ok: false, error: e.message }), {
          status: 502, headers: { 'Content-Type': 'application/json' },
        });
      }
    }

    // ============================================
    // 4. GENERAL PROXY → api.telegram.org
    // Handles all bot API calls (sendMessage, sendPhoto, getFile, etc.)
    // python-telegram-bot calls: /bot{TOKEN}/{method}
    // ============================================
    const proxyUrl = new URL(request.url);
    proxyUrl.hostname = 'api.telegram.org';
    proxyUrl.port = '';
    proxyUrl.protocol = 'https:';

    // ptb 20+ with base_url("https://HOST/") already calls "https://HOST/bot{TOKEN}/{method}"
    // (PTB includes "bot{TOKEN}" in the path itself — it does NOT send an X-Bot-Token header
    // on normal API calls). So we only need to swap the hostname; the path is already correct.
    // Only fall back to prefixing when the path is missing the bot-token segment entirely
    // (defensive — should not normally happen with PTB 20+).
    if (!proxyUrl.pathname.includes('/bot')) {
      const token = request.headers.get('X-Bot-Token') || env.BOT_TOKEN;
      if (token) {
        proxyUrl.pathname = `/bot${token}${proxyUrl.pathname}`;
      }
    }

    try {
      const resp = await fetch(new Request(proxyUrl, {
        method: request.method,
        headers: request.headers,
        body: request.method !== 'GET' && request.method !== 'HEAD' ? request.body : undefined,
      }));
      const newResp = new Response(resp.body, resp);
      newResp.headers.set('Access-Control-Allow-Origin', '*');
      newResp.headers.set('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
      return newResp;
    } catch (e) {
      return new Response(JSON.stringify({ ok: false, error: 'Proxy error', message: e.message }), {
        status: 502, headers: { 'Content-Type': 'application/json' },
      });
    }
  },
};

// ============================================================
// WEB EXAM HELPERS — independent of HF/Render
// Data source priority: D1 cache -> Supabase (mcqs table)
// ============================================================

const SB_URL = "https://wbdyjpjbczfunyhhmtry.supabase.co";
const SB_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndiZHlqcGpiY3pmdW55aGhtdHJ5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA2OTI5ODAsImV4cCI6MjA5NjI2ODk4MH0.0WR1sgVsl_1XWZfSd0Pwoe6Uxp-2GMTksfseMn5aWjg";

const PROMPT_DISPLAY_NAMES = {
  prompt_1: "📋 ATLAS Special MCQ",
  prompt_2: "🧠 Conceptual MCQ",
  prompt_3: "🎯 Board Pattern MCQ",
};

function jsonResp(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
  });
}

function notFoundHtml() {
  return `<!DOCTYPE html><html lang="bn"><head><meta charset="UTF-8">
<title>Exam পাওয়া যায়নি</title>
<style>body{font-family:sans-serif;background:#0A0D1E;color:#E8EAFF;display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center;}
.box{padding:20px;}</style></head>
<body><div class="box"><h2>❌ Exam পাওয়া যায়নি</h2><p>লিংকটি ভুল অথবা মেয়াদোত্তীর্ণ।</p></div></body></html>`;
}

// ── Fetch quiz data: D1 exam_cache -> D1 mcqs (bot dual_insert target) -> Supabase mcqs ──
async function fetchExamData(cacheId, env) {
  if (!cacheId) return null;

  // Layer 1: D1 exam_cache (fast path, if previously cached by this worker)
  try {
    if (env.DB) {
      const row = await env.DB.prepare(
        "SELECT * FROM exam_cache WHERE quiz_id=?1"
      ).bind(cacheId).first();
      if (row) {
        return {
          mcqs: JSON.parse(row.mcqs || "[]"),
          topic: row.topic || "ATLAS Exam",
          page: row.page || 1,
          tag: row.tag || "",
          image_file_id: row.image_file_id || "",
          is_new_gen: false,
          chat_id: row.chat_id || null,
          message_id: row.message_id || null,
          prompt_type: row.prompt_type || "prompt_1",
        };
      }
    }
  } catch (e) {
    console.warn("[exam] D1 exam_cache lookup failed:", e.message);
  }

  // Layer 2: D1 mcqs (bot.py writes here via dual_insert — PRIMARY data source)
  try {
    if (env.DB) {
      const row = await env.DB.prepare(
        "SELECT * FROM mcqs WHERE quiz_id=?1"
      ).bind(cacheId).first();
      if (row) {
        const mcqs = typeof row.mcqs === "string" ? JSON.parse(row.mcqs) : row.mcqs;
        const promptType = row.prompt_type || "prompt_1";
        const result = {
          mcqs,
          topic: PROMPT_DISPLAY_NAMES[promptType] || "ATLAS Special MCQ",
          page: 1,
          tag: "",
          image_file_id: row.image_file_id || "",
          is_new_gen: false,
          chat_id: row.chat_id || null,
          message_id: row.message_id || null,
          prompt_type: promptType,
        };
        // Mirror into exam_cache for next-time fast lookup
        try {
          await env.DB.prepare(
            `INSERT OR REPLACE INTO exam_cache
             (quiz_id, mcqs, topic, page, tag, image_file_id, chat_id, message_id, prompt_type)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)`
          ).bind(
            cacheId, JSON.stringify(mcqs), result.topic, result.page, result.tag,
            result.image_file_id, result.chat_id, result.message_id, result.prompt_type
          ).run();
        } catch (_) {}
        return result;
      }
    }
  } catch (e) {
    console.warn("[exam] D1 mcqs lookup failed:", e.message);
  }

  // Layer 3: Supabase mcqs
  try {
    const r = await fetch(
      `${SB_URL}/rest/v1/mcqs?quiz_id=eq.${encodeURIComponent(cacheId)}&select=*`,
      { headers: { apikey: SB_KEY, Authorization: `Bearer ${SB_KEY}` } }
    );
    const data = await r.json();
    if (data && data[0]) {
      const row = data[0];
      const mcqs = typeof row.mcqs === "string" ? JSON.parse(row.mcqs) : row.mcqs;
      const promptType = row.prompt_type || "prompt_1";
      const result = {
        mcqs,
        topic: PROMPT_DISPLAY_NAMES[promptType] || "ATLAS Special MCQ",
        page: 1,
        tag: "",
        image_file_id: row.image_file_id || "",
        is_new_gen: false,
        chat_id: row.chat_id || null,
        message_id: row.message_id || null,
        prompt_type: promptType,
      };
      try {
        if (env.DB) {
          await env.DB.prepare(
            `INSERT OR REPLACE INTO exam_cache
             (quiz_id, mcqs, topic, page, tag, image_file_id, chat_id, message_id, prompt_type)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)`
          ).bind(
            cacheId, JSON.stringify(mcqs), result.topic, result.page, result.tag,
            result.image_file_id, result.chat_id, result.message_id, result.prompt_type
          ).run();
        }
      } catch (_) {}
      return result;
    }
  } catch (e) {
    console.warn("[exam] Supabase lookup failed:", e.message);
  }

  // Layer 4: Render live bot (in-memory exam_store fallback — last resort)
  try {
    const RENDER_URLS = [
      "https://atlasbot-3tgq.onrender.com",
    ];
    for (const base of RENDER_URLS) {
      try {
        const r = await fetch(`${base}/api/exam/${encodeURIComponent(cacheId)}`, {
          signal: AbortSignal.timeout(8000)
        });
        if (r.ok) {
          const d = await r.json();
          if (d && d.mcqs && d.mcqs.length > 0) {
            // Mirror into D1 exam_cache for future fast lookups
            try {
              if (env.DB) {
                await env.DB.prepare(
                  `INSERT OR REPLACE INTO exam_cache
                   (quiz_id, mcqs, topic, page, tag, image_file_id, chat_id, message_id, prompt_type)
                   VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)`
                ).bind(
                  cacheId, JSON.stringify(d.mcqs), d.topic || "ATLAS Exam", d.page || 1,
                  d.tag || "", d.image_file_id || "", d.chat_id || null,
                  d.message_id || null, d.prompt_type || "prompt_1"
                ).run();
              }
            } catch (_) {}
            return d;
          }
        }
      } catch (_) { /* try next render url */ }
    }
  } catch (e) {
    console.error("[exam] Render fallback failed:", e.message);
  }

  return null;
}

// ── Serve the exam HTML page with config injected ──
async function handleExamPage(request, url, env) {
  const cacheId = url.pathname.replace("/exam/", "").split("?")[0].trim();
  const uid = parseInt(url.searchParams.get("uid") || "0", 10) || 0;
  const name = (url.searchParams.get("name") || "").trim();
  const challenger = parseInt(url.searchParams.get("challenger") || "0", 10) || 0;

  const data = await fetchExamData(cacheId, env);
  if (!data) {
    return new Response(notFoundHtml(), {
      status: 404,
      headers: { "Content-Type": "text/html; charset=utf-8" },
    });
  }

  let html;
  try {
    const assetUrl = new URL("/index.html", request.url);
    const assetResp = await env.ASSETS.fetch(new Request(assetUrl));
    html = await assetResp.text();
  } catch (e) {
    return jsonResp({ error: "template_unavailable", message: e.message }, 500);
  }

  const cfg = {
    cacheId, userId: uid, total: data.mcqs.length, topic: data.topic,
    tag: data.tag, page: data.page, imageFileId: data.image_file_id,
    isNewGen: data.is_new_gen, mcqs: data.mcqs,
    negPerWrong: 0.25, secPerQ: 60,
    hasSource: !!(data.chat_id && data.message_id),
    promptDisplay: PROMPT_DISPLAY_NAMES[data.prompt_type] || "📋 ATLAS Special MCQ",
    hfSpaceUrl: `${url.protocol}//${url.host}`,
    challengerId: challenger,
    websiteUrl: "https://atlascourses.com",
    youtubeUrl: "https://www.youtube.com/@atlasprep",
    whatsappUrl: "https://wa.me/8801999681290",
    groupsUrl: "https://t.me/MediAtlas/4221",
  };
  const cfgJson = JSON.stringify(cfg);
  const nameSafe = name.replace(/'/g, "\\'").replace(/"/g, '\\"');

  html = html.replace("__CFG_JSON__", cfgJson).replace("__USER_NAME_SAFE__", nameSafe);

  return new Response(html, {
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}

// ── Save exam result to Supabase ──
async function handleExamResult(request, env) {
  let body;
  try {
    body = await request.json();
  } catch (e) {
    return jsonResp({ error: "bad_json" }, 400);
  }
  const cacheId = body.cache_id || "";
  const userId = parseInt(body.user_id || 0, 10) || 0;
  const userName = (body.user_name || "Student").trim().slice(0, 40);
  const correct = parseInt(body.correct || 0, 10);
  const wrong = parseInt(body.wrong || 0, 10);
  const skipped = parseInt(body.skipped || 0, 10);
  const timeTaken = parseInt(body.time_taken || 0, 10);
  const total = correct + wrong + skipped;
  const negPerWrong = 0.25;
  const neg = wrong * negPerWrong;
  const finalScore = correct - neg;

  const data = await fetchExamData(cacheId, env);
  const topic = data ? data.topic : "ATLAS Exam";

  try {
    await fetch(`${SB_URL}/rest/v1/results`, {
      method: "POST",
      headers: {
        apikey: SB_KEY, Authorization: `Bearer ${SB_KEY}`,
        "Content-Type": "application/json", Prefer: "return=minimal",
      },
      body: JSON.stringify({
        user_id: userId, quiz_id: cacheId,
        quiz_name: topic || `Exam_${cacheId.slice(0, 6)}`,
        total, correct, wrong, skipped, time_taken: timeTaken,
        mark: finalScore, negative_mark: neg,
        created_at: new Date().toISOString(),
      }),
    });
  } catch (e) {
    console.error("[exam] save result failed:", e.message);
  }

  const pct = total > 0 ? Math.round((correct / total) * 100) : 0;
  let motivation;
  if (pct >= 80) motivation = "🌟 অসাধারণ! তুমি দারুণ প্রস্তুতি নিয়েছো!";
  else if (pct >= 60) motivation = "👍 ভালো করেছো! আরেকটু পড়লে আরও ভালো হবে।";
  else if (pct >= 40) motivation = "💪 চেষ্টা চালিয়ে যাও, তুমি পারবে!";
  else motivation = "📚 আরও অনুশীলন দরকার, হাল ছেড়ো না!";

  return jsonResp({ motivation, ayat: "" });
}

// ── Proxy source image via Telegram getFile + file download ──
async function handleTgImageProxy(fileId, env) {
  if (!fileId) return new Response("image unavailable", { status: 404 });
  const token = env.BOT_TOKEN || env.ATLAS_BOT_TOKEN;
  if (!token) return new Response("bot token not configured", { status: 500 });
  try {
    const infoR = await fetch(`https://api.telegram.org/bot${token}/getFile?file_id=${fileId}`);
    const info = await infoR.json();
    if (!info.ok) return new Response("image unavailable", { status: 404 });
    const filePath = info.result.file_path;
    const fileR = await fetch(`https://api.telegram.org/file/bot${token}/${filePath}`);
    const buf = await fileR.arrayBuffer();
    let ct = "image/jpeg";
    const bytes = new Uint8Array(buf.slice(0, 12));
    if (bytes[0] === 0x89 && bytes[1] === 0x50) ct = "image/png";
    else if (bytes[0] === 0x52 && bytes[1] === 0x49) ct = "image/webp";
    return new Response(buf, {
      headers: { "Content-Type": ct, "Cache-Control": "public, max-age=3600" },
    });
  } catch (e) {
    return new Response("image unavailable", { status: 404 });
  }
}

