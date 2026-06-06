FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    fonts-noto-core \
    fonts-noto-color-emoji \
    curl \
    ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV CHROMIUM_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage --single-process --headless=new"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data

EXPOSE 7860

CMD ["python", "bot.py"]
