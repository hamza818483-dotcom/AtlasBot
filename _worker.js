// _worker.js — Cloudflare Pages Worker
// Deployed at: atlas-bot-proxy-pages.pages.dev
// Same routing as worker.js (Workers) but for Pages deployment
// HF Space can reach *.pages.dev but NOT *.workers.dev

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // ── 1. WEBHOOK: Telegram → HF Space ──────────────────────────────
    if (request.method === 'POST' && url.pathname.startsWith('/webhook/')) {
      const token = url.pathname.split('/webhook/')[1];
      if (!token) return new Response('Unauthorized', { status: 401 });
      const body = await request.text();
      try {
        await fetch('https://atlasbot-pvp7.onrender.com/webhook', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Bot-Token': token },
          body,
        });
      } catch (_) {}
      return new Response('OK', { status: 200 });
    }

    // ── 2. FILE DOWNLOAD PROXY ────────────────────────────────────────
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

    // ── 3. SEND DOCUMENT PROXY ────────────────────────────────────────
    if (request.method === 'POST' && url.pathname === '/tg-senddoc') {
      const token = request.headers.get('X-Bot-Token') || '';
      if (!token) return new Response('Unauthorized', { status: 401 });
      const tgUrl = `https://api.telegram.org/bot${token}/sendDocument`;
      try {
        const bodyData = await request.json();
        // Build multipart form
        const form = new FormData();
        form.append('chat_id', String(bodyData.chat_id || ''));
        if (bodyData.caption) form.append('caption', bodyData.caption);
        if (bodyData.parse_mode) form.append('parse_mode', bodyData.parse_mode);
        if (bodyData.reply_to_message_id) form.append('reply_to_message_id', String(bodyData.reply_to_message_id));
        if (bodyData.message_thread_id) form.append('message_thread_id', String(bodyData.message_thread_id));
        if (bodyData.reply_markup) form.append('reply_markup', JSON.stringify(bodyData.reply_markup));
        // File from base64
        if (bodyData.doc_b64) {
          const bin = atob(bodyData.doc_b64);
          const bytes = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
          const blob = new Blob([bytes], { type: bodyData.mime_type || 'application/octet-stream' });
          form.append('document', blob, bodyData.filename || 'file');
        }
        const resp = await fetch(tgUrl, { method: 'POST', body: form });
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

    // ── 4. GENERAL PROXY → api.telegram.org ──────────────────────────
    // PTB calls: /bot{TOKEN}/{method}
    const proxyUrl = new URL(request.url);
    proxyUrl.hostname = 'api.telegram.org';
    proxyUrl.port = '';
    proxyUrl.protocol = 'https:';

    try {
      const proxyReq = new Request(proxyUrl.toString(), {
        method: request.method,
        headers: request.headers,
        body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
      });
      const resp = await fetch(proxyReq);
      const newResp = new Response(resp.body, resp);
      newResp.headers.set('Access-Control-Allow-Origin', '*');
      return newResp;
    } catch (e) {
      return new Response(JSON.stringify({ ok: false, error: e.message }), {
        status: 502, headers: { 'Content-Type': 'application/json' },
      });
    }
  },
};
