FROM python:3.11-slim

# Instalar TODAS las dependencias del sistema manualmente
# (evitamos playwright install-deps que falla en Debian trixie)
RUN apt-get update && apt-get install -y \
    wget curl gnupg ca-certificates \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 \
    libx11-6 libx11-xcb1 libxcb1 libxext6 \
    libxshmfence1 libgl1 libglib2.0-0 \
    fonts-liberation fonts-unifont \
    --no-install-recommends && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    python -m playwright install chromium

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["python", "app.py"]
