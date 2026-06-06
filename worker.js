// ATLAS BOT - Cloudflare Worker Proxy + Webhook
// URL: https://atlas-bot-proxy.hamza818483.workers.dev

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // ============================================
    // WEBHOOK ENDPOINT — Telegram sends updates here
    // POST /webhook/{BOT_TOKEN}
    // ============================================
    if (request.method === 'POST' && url.pathname.startsWith('/webhook/')) {
      const token = url.pathname.split('/webhook/')[1];
      if (!token) {
        return new Response('Unauthorized', { status: 401 });
      }

      // Forward update to HF Space bot
      const body = await request.text();
      const hfUrl = `https://hamzaHF1-atlasbot.hf.space/webhook`;

      try {
        const hfResponse = await fetch(hfUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Bot-Token': token
          },
          body: body
        });
        return new Response('OK', { status: 200 });
      } catch (error) {
        return new Response('Error', { status: 500 });
      }
    }

    // ============================================
    // PROXY — Forward all other requests to Telegram API
    // ============================================
    url.hostname = 'api.telegram.org';

    const modifiedRequest = new Request(url, {
      method: request.method,
      headers: request.headers,
      body: request.method !== 'GET' && request.method !== 'HEAD' ? request.body : undefined
    });

    try {
      const response = await fetch(modifiedRequest);
      const newResponse = new Response(response.body, response);
      newResponse.headers.set('Access-Control-Allow-Origin', '*');
      newResponse.headers.set('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
      return newResponse;
    } catch (error) {
      return new Response(JSON.stringify({
        ok: false,
        error: 'Proxy error',
        message: error.message
      }), {
        status: 502,
        headers: { 'Content-Type': 'application/json' }
      });
    }
  }
};
