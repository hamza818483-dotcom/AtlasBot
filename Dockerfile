FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    fonts-noto-core \
    fonts-noto-color-emoji \
    fonts-noto-cjk \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    curl \
    ca-certificates \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Tell Playwright to use system Chromium (skip its own download)
ENV PLAYWRIGHT_BROWSERS_PATH=/usr/bin
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium
ENV CHROMIUM_PATH=/usr/bin/chromium

# Chromium flags for HF Space (no sandbox, headless)
ENV CHROMIUM_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage --single-process --headless=new --disable-setuid-sandbox"

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright but skip browser download (using system Chromium above)
RUN playwright install-deps chromium 2>/dev/null || true

COPY . .

RUN mkdir -p data logs data/pdfs

# HF Spaces runs as non-root user 1000
RUN useradd -m -u 1000 atlasuser 2>/dev/null || true \
    && chown -R 1000:1000 /app

USER 1000

EXPOSE 7860

CMD ["python", "bot.py"]
