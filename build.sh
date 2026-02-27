#!/bin/bash
set -e
echo "🐍 Python version: $(python --version)"
echo "📦 Instalando dependencias..."
pip install --upgrade pip
pip install -r requirements.txt
echo "🎭 Instalando Playwright + Chromium..."
python -m playwright install chromium
python -m playwright install-deps chromium
echo "✅ Build completado"
