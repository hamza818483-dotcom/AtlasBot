FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-core \
    fonts-noto-color-emoji \
    curl \
    ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


COPY . .

RUN mkdir -p data logs data/pdfs

# HF Spaces runs as non-root user 1000
RUN useradd -m -u 1000 atlasuser 2>/dev/null || true \
    && chown -R 1000:1000 /app

USER 1000

EXPOSE 7860

CMD ["python", "bot.py"]
