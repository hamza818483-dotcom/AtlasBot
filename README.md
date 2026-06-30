---
title: Atlas MCQ Bot
emoji: 📚
colorFrom: indigo
colorTo: purple
sdk: docker
sdk_version: "24.0.0"
app_file: bot.py
pinned: false
replicas: 1
---

# 📚 Atlas MCQ Bot

AI-powered MCQ generator using Gemini 2.5 Flash

## Features
- Generate MCQs from Image & Text
- Telegram Poll Quiz
- Inline Quiz with Timer
- Website Exam Mode
- Bookmark & PDF Download

## Commands
- /start - Start bot
- /all - View all MCQs
- /bm - Bookmark PDF
- /info - Usage report (Admin)

## Deployment platforms
Primary deployment is on Hugging Face Space (uses the `atlas-bot-proxy-pages.pages.dev`
proxy for Telegram API access, since `*.workers.dev`/direct `api.telegram.org`
is blocked from inside the HF container).

A Render deployment can serve as a backup if HF Space or the CF proxy goes
down. To deploy on Render, set these environment variables (in addition to
the usual `BOT_TOKEN`, `SUPABASE_*`, `GEMINI_KEY`, etc.):
- `RUNNING_ON=Render`
- `RENDER_URL=https://<your-app>.onrender.com`

When these are set, the bot connects to `api.telegram.org` directly (no
proxy — Render can reach it without restriction) and registers its webhook
at `{RENDER_URL}/webhook/{BOT_TOKEN}`. Without these env vars, the bot
behaves exactly as before (HF + CF proxy mode).

A `.github/workflows/keep-render-awake.yml` workflow pings Render's
`/health` endpoint every 10 minutes (plus immediately after every push to
`main`) to keep the free-tier instance from sleeping due to inactivity.

If the CF proxy stops responding while running on HF Space, the bot
automatically switches its webhook to the Render fallback URL (and
switches back once the CF proxy recovers) — see
`cf_proxy_health_check_scheduler()` in `bot.py`. Note: this internal
attempt itself goes through the CF proxy (since HF Space cannot reach
api.telegram.org directly), so it only helps when a specific API route
is broken, not when the proxy domain itself is fully unreachable.

`.github/workflows/watchdog-failover.yml` is an **independent, external**
safety net for that gap — it runs from GitHub's own network (no
workers.dev/Telegram restrictions) every 5 minutes, checks the CF
proxy's health directly, and switches the webhook to/from Render via
`api.telegram.org` itself. This works even if the CF proxy is
completely down. Requires these repo secrets:
- `BOT_TOKEN` — the Telegram bot token
- `RENDER_URL` — e.g. `https://atlasbot-pvp7.onrender.com`
# Thu Jun 11 14:42:27 +06 2026
