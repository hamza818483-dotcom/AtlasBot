# ATLAS MCQ BOT - Dockerfile
# Python 3.10 slim base with Chromium for PDF generation

# ============================================
# BASE IMAGE
# ============================================
FROM python:3.10-slim

# ============================================
# SET ENVIRONMENT VARIABLES
# ============================================
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# ============================================
# INSTALL SYSTEM DEPENDENCIES
# ============================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chromium dependencies
    chromium \
    chromium-driver \
    chromium-common \
    chromium-sandbox \
    # Font packages for Bengali + Unicode support
    fonts-noto-core \
    fonts-noto-color-emoji \
    fonts-noto-extra \
    fonts-noto-ui-core \
    fonts-noto-unhinted \
    fonts-freefont-ttf \
    fonts-dejavu-core \
    fonts-liberation \
    fontconfig \
    # Image processing dependencies
    libjpeg62-turbo \
    libpng16-16 \
    libwebp7 \
    libtiff6 \
    libgomp1 \
    # PDF generation dependencies
    libnss3 \
    libnspr4 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    libcups2 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrender1 \
    libxtst6 \
    # Utility packages
    curl \
    wget \
    ca-certificates \
    gnupg \
    # Clean up apt cache
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/* /var/tmp/*

# ============================================
# SET CHROMIUM ENVIRONMENT
# ============================================
ENV CHROMIUM_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage --single-process --disable-software-rasterizer --disable-extensions --disable-background-networking --disable-sync --no-first-run --headless=new" \
    CHROME_BIN=/usr/bin/chromium \
    CHROMIUM_PATH=/usr/bin/chromium

# ============================================
# SET WORKING DIRECTORY
# ============================================
WORKDIR /app

# ============================================
# INSTALL PYTHON DEPENDENCIES
# ============================================
# Copy requirements first for better caching
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# ============================================
# COPY APPLICATION FILES
# ============================================
COPY . .

# ============================================
# CREATE NECESSARY DIRECTORIES
# ============================================
RUN mkdir -p data/pdfs logs && \
    chmod -R 755 data logs

# ============================================
# CREATE NON-ROOT USER (Security)
# ============================================
RUN useradd -m -u 1000 atlas && \
    chown -R atlas:atlas /app

USER atlas

# ============================================
# EXPOSE PORT FOR FLASK SERVER
# ============================================
EXPOSE 7860

# ============================================
# HEALTH CHECK
# ============================================
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:7860/health || exit 1

# ============================================
# START APPLICATION
# ============================================
CMD ["python", "bot.py"]
