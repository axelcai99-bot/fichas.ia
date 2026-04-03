import json
import os
import sqlite3
from datetime import datetime


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", "").strip() or os.path.join(BASE_DIR, "properties.db")
USERS_JSON_PATH = os.path.join(BASE_DIR, "users.json")


def get_connection() -> sqlite3.Connection:
    """Devuelve una conexión SQLite. Dentro de un request Flask, reutiliza la misma."""
    try:
        from flask import g, has_app_context
        if has_app_context():
            if "db" not in g:
                g.db = sqlite3.connect(DB_PATH, check_same_thread=False)
                g.db.row_factory = sqlite3.Row
            return g.db
    except ImportError:
        pass
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

        _ensure_column(conn, "properties", "deleted_at", "TEXT")
        _ensure_column(conn, "clients", "deleted_at", "TEXT")
        _ensure_column(conn, "clients", "estado", "TEXT NOT NULL DEFAULT 'nuevo_lead'")
        _ensure_column(conn, "clients", "proxima_accion", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "clients", "proxima_accion_fecha", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "clients", "proxima_accion_nota", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "clients", "apto_credito_estado", "TEXT NOT NULL DEFAULT 'indiferente'")
        _ensure_column(conn, "clients", "tipos_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "clients", "ambientes_min", "INTEGER")
        _ensure_column(conn, "clients", "ambientes_max", "INTEGER")
        _ensure_column(conn, "clients", "zonas_json", "TEXT NOT NULL DEFAULT '[]'")
        _migrate_clients_crm_enums(conn)
        _ensure_column(conn, "properties", "tags_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "properties", "public_token", "TEXT")
        _migrate_public_tokens(conn)

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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS client_activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                owner_username TEXT NOT NULL,
                tipo TEXT NOT NULL DEFAULT 'nota',
                texto TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cal_client
            ON client_activity_log(client_id)
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

        admin_password = (os.environ.get("ADMIN_PASSWORD") or "").strip() or "admin123"

        conn.execute(
            """
            INSERT INTO users(username, password_hash, role, nombre, whatsapp, form_url, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("admin", hash_pw(admin_password), "admin", "Administrador", "", "", 1, now),
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

    fallback_password = (os.environ.get("ADMIN_PASSWORD") or "").strip() or os.urandom(12).hex()

    for username, data in users.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO users(username, password_hash, role, nombre, whatsapp, form_url, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (username or "").strip().lower(),
                data.get("password", hash_pw(fallback_password)),
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
        pass


def _migrate_public_tokens(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT id FROM properties WHERE public_token IS NULL").fetchall()
    if not rows:
        return
    conn.executemany(
        "UPDATE properties SET public_token = ? WHERE id = ?",
        [(os.urandom(16).hex(), r["id"]) for r in rows],
    )


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


def _migrate_clients_crm_enums(conn: sqlite3.Connection) -> None:
    estado_map = {
        "new_lead": "nuevo_lead",
        "contacted": "contactado",
        "visited_property": "visito_propiedad",
        "negotiating": "negociando",
        "closed": "cerrado",
        "lost": "perdido",
    }
    old_estados = tuple(estado_map.keys())
    needs_migration = conn.execute(
        f"SELECT 1 FROM clients WHERE estado IN ({','.join('?' * len(old_estados))}) LIMIT 1",
        old_estados,
    ).fetchone()
    if not needs_migration:
        return
    for old, new in estado_map.items():
        conn.execute("UPDATE clients SET estado = ? WHERE estado = ?", (new, old))
        conn.execute("UPDATE clients SET situacion = ? WHERE situacion = ?", (new, old))

    accion_map = {
        "call": "llamar",
        "send_properties": "enviar_propiedades",
        "schedule_visit": "coordinar_visita",
        "follow_up": "seguimiento",
    }
    for old, new in accion_map.items():
        conn.execute("UPDATE clients SET proxima_accion = ? WHERE proxima_accion = ?", (new, old))

    conn.execute(
        """
        UPDATE clients
        SET apto_credito_estado = CASE
            WHEN apto_credito = 1 THEN 'si'
            ELSE 'no'
        END
        WHERE apto_credito_estado IS NULL OR apto_credito_estado = ''
        """
    )
