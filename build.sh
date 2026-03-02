#!/bin/bash
set -e
echo "🐍 Python version: $(python --version)"
echo "📦 Instalando dependencias..."
pip install --upgrade pip
pip install -r requirements.txt
echo "🌐 Instalando Chrome..."
sbase install chromedriver latest
echo "✅ Build completado"
