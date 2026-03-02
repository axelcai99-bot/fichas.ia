import json
import os
import sqlite3
from datetime import datetime


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "properties.db")
USERS_JSON_PATH = os.path.join(BASE_DIR, "users.json")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def hash_pw(pw: str) -> str:
    import hashlib
    return hashlib.sha256(pw.encode()).hexdigest()


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                nombre TEXT NOT NULL DEFAULT '',
                whatsapp TEXT NOT NULL DEFAULT '',
                form_url TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS properties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo TEXT NOT NULL,
                precio TEXT NOT NULL,
                ubicacion TEXT NOT NULL,
                descripcion TEXT NOT NULL,
                detalles_json TEXT NOT NULL,
                caracteristicas_json TEXT NOT NULL,
                info_adicional_json TEXT NOT NULL,
                image_paths_json TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                agent_whatsapp TEXT NOT NULL,
                form_url TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_properties_created_at
            ON properties(created_at DESC)
            """
        )
        conn.commit()

    _bootstrap_users()


def _bootstrap_users() -> None:
    now = datetime.now().isoformat()
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()
        users_count = row["count"] if row else 0
        if users_count > 0:
            return

        imported = _import_users_json(conn)
        if imported:
            conn.commit()
            return

        conn.execute(
            """
            INSERT INTO users(username, password_hash, role, nombre, whatsapp, form_url, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("admin", hash_pw("admin123"), "admin", "Administrador", "", "", 1, now),
        )
        conn.commit()


def _import_users_json(conn: sqlite3.Connection) -> bool:
    if not os.path.exists(USERS_JSON_PATH):
        return False
    try:
        with open(USERS_JSON_PATH, "r", encoding="utf-8") as f:
            users = json.load(f)
    except Exception:
        return False

    if not isinstance(users, dict) or not users:
        return False

    for username, data in users.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO users(username, password_hash, role, nombre, whatsapp, form_url, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (username or "").strip().lower(),
                data.get("password", hash_pw("admin123")),
                data.get("role", "user"),
                data.get("nombre", username),
                data.get("whatsapp", ""),
                data.get("form_url", ""),
                1 if data.get("active", True) else 0,
                data.get("created") or datetime.now().isoformat(),
            ),
        )
    _mark_users_json_migrated()
    return True


def _mark_users_json_migrated() -> None:
    migrated_path = f"{USERS_JSON_PATH}.migrated"
    try:
        if os.path.exists(migrated_path):
            os.remove(migrated_path)
        os.rename(USERS_JSON_PATH, migrated_path)
    except Exception:
        # Si no se puede renombrar (permisos/bloqueo), se deja el archivo original.
        # La tabla users ya no vacia evita reimportaciones en arranques siguientes.
        pass
