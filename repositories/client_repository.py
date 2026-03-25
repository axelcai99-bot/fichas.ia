from datetime import datetime
from typing import Any

from db import get_connection


class ClientRepository:
    def list_clients(self, owner_username: str, search: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
        conditions = ["owner_username = ?", "deleted_at IS NULL"]
        params: list = [owner_username]
        if search:
            conditions.append("(nombre LIKE ? OR telefono LIKE ? OR zonas_busqueda LIKE ? OR tipo LIKE ?)")
            q = f"%{search}%"
            params.extend([q, q, q, q])
        where = " AND ".join(conditions)

        with get_connection() as conn:
            count_row = conn.execute(f"SELECT COUNT(*) AS total FROM clients WHERE {where}", params).fetchone()
            total = count_row["total"] if count_row else 0
            rows = conn.execute(
                f"""
                SELECT id, owner_username, nombre, telefono, presupuesto, tipo, ambientes,
                       apto_credito, zonas_busqueda, notas_resumidas, situacion, created_at, updated_at
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
                    apto_credito, zonas_busqueda, notas_resumidas, situacion, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner_username,
                    payload.get("nombre", "").strip(),
                    payload.get("telefono", "").strip(),
                    payload.get("presupuesto", "").strip(),
                    payload.get("tipo", "otro").strip().lower(),
                    payload.get("ambientes", "").strip(),
                    1 if payload.get("apto_credito") else 0,
                    payload.get("zonas_busqueda", "").strip(),
                    payload.get("notas_resumidas", "").strip(),
                    payload.get("situacion", "").strip(),
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
                    apto_credito = ?, zonas_busqueda = ?, notas_resumidas = ?, situacion = ?, updated_at = ?
                WHERE id = ? AND owner_username = ?
                """,
                (
                    payload.get("nombre", "").strip(),
                    payload.get("telefono", "").strip(),
                    payload.get("presupuesto", "").strip(),
                    payload.get("tipo", "otro").strip().lower(),
                    payload.get("ambientes", "").strip(),
                    1 if payload.get("apto_credito") else 0,
                    payload.get("zonas_busqueda", "").strip(),
                    payload.get("notas_resumidas", "").strip(),
                    payload.get("situacion", "").strip(),
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
                       apto_credito, zonas_busqueda, notas_resumidas, situacion, created_at, updated_at
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

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
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
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
