from datetime import datetime
from typing import Any

from db import get_connection


class InterestRepository:
    def add(self, client_id: int, property_id: int, owner_username: str, nota: str = "") -> bool:
        now = datetime.now().isoformat()
        with get_connection() as conn:
            client = conn.execute(
                "SELECT id FROM clients WHERE id = ? AND owner_username = ? AND deleted_at IS NULL",
                (client_id, owner_username),
            ).fetchone()
            prop = conn.execute(
                "SELECT id FROM properties WHERE id = ? AND owner_username = ? AND deleted_at IS NULL",
                (property_id, owner_username),
            ).fetchone()
            if not client or not prop:
                return False
            conn.execute(
                "INSERT OR IGNORE INTO client_property_interests (client_id, property_id, owner_username, nota, created_at) VALUES (?, ?, ?, ?, ?)",
                (client_id, property_id, owner_username, nota, now),
            )
            conn.commit()
        return True

    def remove(self, client_id: int, property_id: int, owner_username: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM client_property_interests WHERE client_id = ? AND property_id = ? AND owner_username = ?",
                (client_id, property_id, owner_username),
            )
            conn.commit()

    def by_client(self, client_id: int, owner_username: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT cpi.id, cpi.property_id, cpi.nota, cpi.created_at,
                       p.titulo, p.precio, p.ubicacion
                FROM client_property_interests cpi
                JOIN properties p ON p.id = cpi.property_id
                JOIN clients c ON c.id = cpi.client_id
                WHERE cpi.client_id = ? AND cpi.owner_username = ?
                  AND c.owner_username = ?
                  AND p.owner_username = ?
                  AND c.deleted_at IS NULL
                  AND p.deleted_at IS NULL
                ORDER BY cpi.created_at DESC
                """,
                (client_id, owner_username, owner_username, owner_username),
            ).fetchall()
        return [
            {"id": r["id"], "property_id": r["property_id"], "nota": r["nota"],
             "created_at": r["created_at"], "titulo": r["titulo"],
             "precio": r["precio"], "ubicacion": r["ubicacion"]}
            for r in rows
        ]

    def by_property(self, property_id: int, owner_username: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT cpi.id, cpi.client_id, cpi.nota, cpi.created_at,
                       c.nombre, c.telefono
                FROM client_property_interests cpi
                JOIN clients c ON c.id = cpi.client_id
                JOIN properties p ON p.id = cpi.property_id
                WHERE cpi.property_id = ? AND cpi.owner_username = ?
                  AND c.owner_username = ?
                  AND p.owner_username = ?
                  AND c.deleted_at IS NULL
                  AND p.deleted_at IS NULL
                ORDER BY cpi.created_at DESC
                """,
                (property_id, owner_username, owner_username, owner_username),
            ).fetchall()
        return [
            {"id": r["id"], "client_id": r["client_id"], "nota": r["nota"],
             "created_at": r["created_at"], "nombre": r["nombre"],
             "telefono": r["telefono"]}
            for r in rows
        ]
