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
# Thu Jun 11 14:42:27 +06 2026
