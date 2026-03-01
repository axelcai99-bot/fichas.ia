# Imagen oficial de Playwright con Python 3.11 y Chromium ya instalado
# No hay que instalar nada extra - viene todo listo
FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["python", "app.py"]
