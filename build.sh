#!/bin/bash
set -e
echo "🐍 Python version: $(python --version)"
echo "📦 Instalando dependencias..."
pip install --upgrade pip
pip install -r requirements.txt
echo "✅ Build completado — sin Chromium, sin Playwright"
