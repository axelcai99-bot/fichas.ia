import json
from datetime import datetime
from typing import Any

from db import get_connection


# ── Valid ENUMs (fuente de verdad) ─────────────────
VALID_CLIENT_TYPES = {"depto", "ph", "casa", "lote", "oficina", "otro"}
VALID_CLIENT_ESTADOS = {
    "nuevo_lead", "contactado", "visito_propiedad",
    "cerrado", "perdido",
}
VALID_CLIENT_ACCIONES = {
    "", "llamar", "enviar_propiedades", "coordinar_visita", "seguimiento", "esperar_respuesta",
}
VALID_CLIENT_ZONAS = {
    "agronomia", "almagro", "balvanera", "barracas", "belgrano", "boedo", "caballito",
    "chacarita", "coghlan", "colegiales", "constitucion", "flores", "floresta", "la boca",
    "la paternal", "liniers", "mataderos", "monserrat", "monte castro", "nuñez", "nunez",
    "palermo", "parque avellaneda", "parque chacabuco", "parque chas", "parque patricios",
    "puerto madero", "recoleta", "retiro", "saavedra", "san cristobal", "san nicolas",
    "san telmo", "velez sarsfield", "versalles", "villa crespo", "villa del parque",
    "villa devoto", "villa general mitre", "villa lugano", "villa luro", "villa ortuzar",
    "villa pueyrredon", "villa real", "villa riachuelo", "villa santa rita", "villa soldati",
    "villa urquiza", "olivos", "vicente lopez", "la lucila", "martinez", "san isidro",
    "acassuso", "beccar", "munro", "florida", "carapachay", "villa adelina",
}

LEGACY_ESTADO_MAP = {
    "new_lead": "nuevo_lead",
    "contacted": "contactado",
    "visited_property": "visito_propiedad",
    "negotiating": "negociando",
    "closed": "cerrado",
    "lost": "perdido",
}

LEGACY_ACCION_MAP = {
    "call": "llamar",
    "send_properties": "enviar_propiedades",
    "schedule_visit": "coordinar_visita",
    "follow_up": "seguimiento",
}

class ClientRepository:
    def list_clients(self, owner_username: str, search: str = "", estado: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
        conditions = ["owner_username = ?", "deleted_at IS NULL"]
        params: list = [owner_username]
        if search:
            conditions.append("(nombre LIKE ? OR telefono LIKE ? OR zonas_json LIKE ? OR tipos_json LIKE ?)")
            q = f"%{search}%"
            params.extend([q, q, q, q])
        if estado:
            normalized_estado = LEGACY_ESTADO_MAP.get(estado, estado)
            legacy_estados = [k for k, v in LEGACY_ESTADO_MAP.items() if v == normalized_estado]
            estado_values = [normalized_estado] + legacy_estados
            placeholders = ", ".join("?" for _ in estado_values)
            conditions.append(f"estado IN ({placeholders})")
            params.extend(estado_values)
        where = " AND ".join(conditions)

        with get_connection() as conn:
            count_row = conn.execute(f"SELECT COUNT(*) AS total FROM clients WHERE {where}", params).fetchone()
            total = count_row["total"] if count_row else 0
            rows = conn.execute(
                f"""
                SELECT id, owner_username, nombre, telefono, presupuesto, tipo, ambientes,
                       apto_credito, zonas_busqueda, notas_resumidas, situacion,
                       estado, proxima_accion, proxima_accion_fecha,
                       tipos_json, ambientes_min, ambientes_max, zonas_json,
                       created_at, updated_at
                FROM clients WHERE {where}
                ORDER BY updated_at DESC LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            ).fetchall()
        return {"items": [self._row_to_dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}

    def create_client(self, owner_username: str, payload: dict[str, Any]) -> int:
        now = datetime.now().isoformat()
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO clients(
                    owner_username, nombre, telefono, presupuesto, tipo, ambientes,
                    apto_credito, zonas_busqueda, notas_resumidas, situacion,
                    estado, proxima_accion, proxima_accion_fecha,
                    tipos_json, ambientes_min, ambientes_max, zonas_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner_username,
                    payload.get("nombre", "").strip(),
                    payload.get("telefono", "").strip(),
                    payload.get("presupuesto", "").strip(),
                    payload.get("tipo", "").strip().lower(),
                    payload.get("ambientes", "").strip(),
                    1 if payload.get("apto_credito") else 0,
                    payload.get("zonas_busqueda", "").strip(),
                    payload.get("notas_resumidas", "").strip(),
                    payload.get("situacion", "").strip(),
                    payload.get("estado", "nuevo_lead"),
                    payload.get("proxima_accion", ""),
                    payload.get("proxima_accion_fecha", ""),
                    json.dumps(payload.get("tipos", []), ensure_ascii=False),
                    payload.get("ambientes_min"),
                    payload.get("ambientes_max"),
                    json.dumps(payload.get("zonas", []), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def update_client(self, client_id: int, owner_username: str, payload: dict[str, Any]) -> bool:
        now = datetime.now().isoformat()
        with get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE clients
                SET nombre = ?, telefono = ?, presupuesto = ?, tipo = ?, ambientes = ?,
                    apto_credito = ?, zonas_busqueda = ?, notas_resumidas = ?, situacion = ?,
                    estado = ?, proxima_accion = ?, proxima_accion_fecha = ?,
                    tipos_json = ?, ambientes_min = ?, ambientes_max = ?, zonas_json = ?,
                    updated_at = ?
                WHERE id = ? AND owner_username = ?
                """,
                (
                    payload.get("nombre", "").strip(),
                    payload.get("telefono", "").strip(),
                    payload.get("presupuesto", "").strip(),
                    payload.get("tipo", "").strip().lower(),
                    payload.get("ambientes", "").strip(),
                    1 if payload.get("apto_credito") else 0,
                    payload.get("zonas_busqueda", "").strip(),
                    payload.get("notas_resumidas", "").strip(),
                    payload.get("situacion", "").strip(),
                    payload.get("estado", "nuevo_lead"),
                    payload.get("proxima_accion", ""),
                    payload.get("proxima_accion_fecha", ""),
                    json.dumps(payload.get("tipos", []), ensure_ascii=False),
                    payload.get("ambientes_min"),
                    payload.get("ambientes_max"),
                    json.dumps(payload.get("zonas", []), ensure_ascii=False),
                    now,
                    client_id,
                    owner_username,
                ),
            )
            conn.commit()
            return cur.rowcount > 0

    def soft_delete_client(self, client_id: int, owner_username: str) -> bool:
        now = datetime.now().isoformat()
        with get_connection() as conn:
            cur = conn.execute(
                "UPDATE clients SET deleted_at = ? WHERE id = ? AND owner_username = ? AND deleted_at IS NULL",
                (now, client_id, owner_username),
            )
            conn.commit()
            return cur.rowcount > 0

    def restore_client(self, client_id: int, owner_username: str) -> bool:
        with get_connection() as conn:
            cur = conn.execute(
                "UPDATE clients SET deleted_at = NULL WHERE id = ? AND owner_username = ? AND deleted_at IS NOT NULL",
                (client_id, owner_username),
            )
            conn.commit()
            return cur.rowcount > 0

    def list_deleted_clients(self, owner_username: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, owner_username, nombre, telefono, presupuesto, tipo, ambientes,
                       apto_credito, zonas_busqueda, notas_resumidas, situacion,
                       estado, proxima_accion, proxima_accion_fecha,
                       tipos_json, ambientes_min, ambientes_max, zonas_json,
                       created_at, updated_at
                FROM clients WHERE owner_username = ? AND deleted_at IS NOT NULL
                ORDER BY updated_at DESC LIMIT 50
                """,
                (owner_username,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def delete_client(self, client_id: int, owner_username: str) -> bool:
        with get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM clients WHERE id = ? AND owner_username = ?",
                (client_id, owner_username),
            )
            conn.commit()
            return cur.rowcount > 0

    def empty_trash(self, owner_username: str) -> int:
        with get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM clients WHERE owner_username = ? AND deleted_at IS NOT NULL",
                (owner_username,),
            )
            conn.commit()
            return cur.rowcount

    def add_activity(self, client_id: int, owner_username: str, tipo: str, texto: str) -> int:
        now = datetime.now().isoformat()
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM clients WHERE id = ? AND owner_username = ? AND deleted_at IS NULL",
                (client_id, owner_username),
            ).fetchone()
            if not row:
                return 0
            cur = conn.execute(
                "INSERT INTO client_activity_log(client_id, owner_username, tipo, texto, created_at) VALUES (?, ?, ?, ?, ?)",
                (client_id, owner_username, tipo, texto, now),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_activities(self, client_id: int, owner_username: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, tipo, texto, created_at
                FROM client_activity_log
                WHERE client_id = ? AND owner_username = ?
                ORDER BY created_at DESC LIMIT 50
                """,
                (client_id, owner_username),
            ).fetchall()
        return [{"id": r["id"], "tipo": r["tipo"], "texto": r["texto"], "created_at": r["created_at"]} for r in rows]

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        # Parse new JSON columns with fallback to legacy comma-separated fields
        tipos_raw = row["tipos_json"] if "tipos_json" in row.keys() else "[]"
        zonas_raw = row["zonas_json"] if "zonas_json" in row.keys() else "[]"

        try:
            tipos = json.loads(tipos_raw) if tipos_raw else []
        except (json.JSONDecodeError, TypeError):
            tipos = []
        try:
            zonas = json.loads(zonas_raw) if zonas_raw else []
        except (json.JSONDecodeError, TypeError):
            zonas = []

        # Fallback: if new columns empty, parse from legacy comma-separated
        if not tipos:
            legacy_tipo = row["tipo"] or ""
            tipos = [t.strip().lower() for t in legacy_tipo.split(",") if t.strip()]
        if not zonas:
            legacy_zonas = row["zonas_busqueda"] or ""
            zonas = [z.strip().lower() for z in legacy_zonas.split(",") if z.strip()]

        return {
            "id": row["id"],
            "owner_username": row["owner_username"],
            "nombre": row["nombre"],
            "telefono": row["telefono"],
            "presupuesto": row["presupuesto"],
            "tipo": row["tipo"],
            "ambientes": row["ambientes"],
            "apto_credito": bool(row["apto_credito"]),
            "zonas_busqueda": row["zonas_busqueda"],
            "notas_resumidas": row["notas_resumidas"],
            "situacion": row["situacion"],
            # New CRM fields
            "estado": LEGACY_ESTADO_MAP.get(row["estado"], row["estado"]) if "estado" in row.keys() else "nuevo_lead",
            "proxima_accion": LEGACY_ACCION_MAP.get(row["proxima_accion"], row["proxima_accion"]) if "proxima_accion" in row.keys() else "",
            "proxima_accion_fecha": row["proxima_accion_fecha"] if "proxima_accion_fecha" in row.keys() else "",
            "tipos": tipos,
            "ambientes_min": row["ambientes_min"] if "ambientes_min" in row.keys() else None,
            "ambientes_max": row["ambientes_max"] if "ambientes_max" in row.keys() else None,
            "zonas": zonas,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
