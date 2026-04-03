"""Configuración centralizada de la aplicación."""
import os

# Security
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()

# File uploads
STATIC_DIR = "static"
PROPERTIES_DIR = os.path.join(STATIC_DIR, "properties")

# Firecrawl API
FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "").strip()

# Debug mode
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
