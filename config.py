"""Configuración centralizada de la aplicación."""
import os

# Security
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()

# Rate limiting (login)
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300  # 5 minutos

# Database
DB_PATH = "properties.db"

# File uploads
STATIC_DIR = "static"
PROPERTIES_DIR = os.path.join(STATIC_DIR, "properties")
UPLOAD_FOLDER = PROPERTIES_DIR

# Firecrawl API
FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "").strip()

# Netlify
NETLIFY_TOKEN_ENV_VAR = "NETLIFY_TOKEN"
NETLIFY_SITE_ID_ENV_VAR = "NETLIFY_SITE_ID"

# App defaults
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"

# Pagination
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 100

# Debug mode
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
