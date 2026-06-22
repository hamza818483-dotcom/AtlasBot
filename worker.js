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
        await fetch('https://hamzaHF1-atlasbot.hf.space/webhook', {
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
