import json
import os
import sqlite3
from datetime import datetime


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", "").strip() or os.path.join(BASE_DIR, "properties.db")
USERS_JSON_PATH = os.path.join(BASE_DIR, "users.json")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def hash_pw(pw: str) -> str:
    from werkzeug.security import generate_password_hash
    return generate_password_hash(pw)


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
                owner_username TEXT NOT NULL DEFAULT 'admin',
                source_portal TEXT NOT NULL DEFAULT 'zonaprop',
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
        _ensure_properties_owner_column(conn)
        _ensure_properties_source_portal_column(conn)
        _ensure_properties_source_image_urls_column(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_username TEXT NOT NULL,
                nombre TEXT NOT NULL,
                telefono TEXT NOT NULL DEFAULT '',
                presupuesto TEXT NOT NULL DEFAULT '',
                tipo TEXT NOT NULL DEFAULT 'otro',
                ambientes TEXT NOT NULL DEFAULT '',
                apto_credito INTEGER NOT NULL DEFAULT 0,
                zonas_busqueda TEXT NOT NULL DEFAULT '',
                notas_resumidas TEXT NOT NULL DEFAULT '',
                situacion TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_properties_created_at
            ON properties(created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_properties_owner_username
            ON properties(owner_username)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_properties_source_portal
            ON properties(source_portal)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_clients_owner_username
            ON clients(owner_username)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_clients_updated_at
            ON clients(updated_at DESC)
            """
        )

        # Soft-delete columns
        _ensure_column(conn, "properties", "deleted_at", "TEXT")
        _ensure_column(conn, "clients", "deleted_at", "TEXT")

        # CRM pipeline columns for clients
        _ensure_column(conn, "clients", "estado", "TEXT NOT NULL DEFAULT 'nuevo_lead'")
        _ensure_column(conn, "clients", "proxima_accion", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "clients", "proxima_accion_fecha", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "clients", "tipos_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "clients", "ambientes_min", "INTEGER")
        _ensure_column(conn, "clients", "ambientes_max", "INTEGER")
        _ensure_column(conn, "clients", "zonas_json", "TEXT NOT NULL DEFAULT '[]'")

        # Client-Property interests
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS client_property_interests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                property_id INTEGER NOT NULL,
                owner_username TEXT NOT NULL,
                nota TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(client_id, property_id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cpi_client
            ON client_property_interests(client_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cpi_property
            ON client_property_interests(property_id)
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


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, col_type: str) -> None:
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if column not in {c["name"] for c in cols}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def _ensure_properties_owner_column(conn: sqlite3.Connection) -> None:
    cols = conn.execute("PRAGMA table_info(properties)").fetchall()
    col_names = {c["name"] for c in cols}
    if "owner_username" not in col_names:
        conn.execute("ALTER TABLE properties ADD COLUMN owner_username TEXT")
        conn.execute("UPDATE properties SET owner_username = 'admin' WHERE owner_username IS NULL OR owner_username = ''")


def _ensure_properties_source_portal_column(conn: sqlite3.Connection) -> None:
    cols = conn.execute("PRAGMA table_info(properties)").fetchall()
    col_names = {c["name"] for c in cols}
    if "source_portal" not in col_names:
        conn.execute("ALTER TABLE properties ADD COLUMN source_portal TEXT")
        conn.execute("UPDATE properties SET source_portal = 'zonaprop' WHERE source_portal IS NULL OR source_portal = ''")


def _ensure_properties_source_image_urls_column(conn: sqlite3.Connection) -> None:
    cols = conn.execute("PRAGMA table_info(properties)").fetchall()
    col_names = {c["name"] for c in cols}
    if "source_image_urls_json" not in col_names:
        conn.execute("ALTER TABLE properties ADD COLUMN source_image_urls_json TEXT NOT NULL DEFAULT '[]'")
