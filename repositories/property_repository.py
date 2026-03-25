import json
from datetime import datetime
from typing import Any

from db import get_connection


class PropertyRepository:
    def create_property(self, payload: dict[str, Any]) -> int:
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO properties(
                    owner_username, source_portal, titulo, precio, ubicacion, descripcion,
                    detalles_json, caracteristicas_json, info_adicional_json,
                    image_paths_json, source_image_urls_json, agent_name, agent_whatsapp, form_url,
                    source_url, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("owner_username", "admin"),
                    payload.get("source_portal", "zonaprop"),
                    payload["titulo"],
                    payload["precio"],
                    payload["ubicacion"],
                    payload["descripcion"],
                    json.dumps(payload.get("detalles", {}), ensure_ascii=False),
                    json.dumps(payload.get("caracteristicas", []), ensure_ascii=False),
                    json.dumps(payload.get("info_adicional", {}), ensure_ascii=False),
                    json.dumps(payload.get("image_paths", []), ensure_ascii=False),
                    json.dumps(payload.get("source_image_urls", []), ensure_ascii=False),
                    payload["agent_name"],
                    payload["agent_whatsapp"],
                    payload.get("form_url", ""),
                    payload.get("source_url", ""),
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def update_image_paths(self, property_id: int, image_paths: list[str]) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE properties SET image_paths_json = ? WHERE id = ?",
                (json.dumps(image_paths, ensure_ascii=False), property_id),
            )
            conn.commit()

    def get_property(self, property_id: int) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT id, titulo, precio, ubicacion, descripcion,
                       detalles_json, caracteristicas_json, info_adicional_json,
                       image_paths_json, source_image_urls_json,
                       agent_name, agent_whatsapp, form_url, owner_username, source_portal,
                       source_url, created_at
                FROM properties
                WHERE id = ?
                """,
                (property_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "titulo": row["titulo"],
            "precio": row["precio"],
            "ubicacion": row["ubicacion"],
            "descripcion": row["descripcion"],
            "detalles": json.loads(row["detalles_json"] or "{}"),
            "caracteristicas": json.loads(row["caracteristicas_json"] or "[]"),
            "info_adicional": json.loads(row["info_adicional_json"] or "{}"),
            "image_paths": json.loads(row["image_paths_json"] or "[]"),
            "source_image_urls": json.loads(row["source_image_urls_json"] or "[]"),
            "agent_name": row["agent_name"],
            "agent_whatsapp": row["agent_whatsapp"],
            "form_url": row["form_url"] or "",
            "owner_username": row["owner_username"] or "admin",
            "source_portal": row["source_portal"] or "zonaprop",
            "source_url": row["source_url"] or "",
            "created_at": row["created_at"],
        }

    def list_portals(self, owner_username: str | None = None) -> list[str]:
        with get_connection() as conn:
            if owner_username:
                rows = conn.execute(
                    "SELECT DISTINCT source_portal FROM properties WHERE owner_username = ? AND source_portal IS NOT NULL ORDER BY source_portal",
                    (owner_username,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT DISTINCT source_portal FROM properties WHERE source_portal IS NOT NULL ORDER BY source_portal",
                ).fetchall()
        return [r["source_portal"] for r in rows if r["source_portal"]]

    def list_properties(self, limit: int = 50, owner_username: str | None = None, source_portal: str | None = None) -> list[dict[str, Any]]:
        with get_connection() as conn:
            if owner_username and source_portal:
                rows = conn.execute(
                    """
                    SELECT id, titulo, precio, ubicacion, created_at, owner_username, source_portal
                    FROM properties
                    WHERE owner_username = ? AND source_portal = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (owner_username, source_portal, limit),
                ).fetchall()
            elif owner_username:
                rows = conn.execute(
                    """
                    SELECT id, titulo, precio, ubicacion, created_at, owner_username, source_portal
                    FROM properties
                    WHERE owner_username = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (owner_username, limit),
                ).fetchall()
            elif source_portal:
                rows = conn.execute(
                    """
                    SELECT id, titulo, precio, ubicacion, created_at, owner_username, source_portal
                    FROM properties
                    WHERE source_portal = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (source_portal, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, titulo, precio, ubicacion, created_at, owner_username, source_portal
                    FROM properties
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [
            {
                "id": r["id"],
                "titulo": r["titulo"],
                "precio": r["precio"],
                "ubicacion": r["ubicacion"],
                "created_at": r["created_at"],
                "owner_username": r["owner_username"] or "admin",
                "source_portal": r["source_portal"] or "zonaprop",
            }
            for r in rows
        ]

    def delete_property(self, property_id: int, owner_username: str | None = None) -> bool:
        with get_connection() as conn:
            if owner_username:
                cur = conn.execute(
                    "DELETE FROM properties WHERE id = ? AND owner_username = ?",
                    (property_id, owner_username),
                )
            else:
                cur = conn.execute("DELETE FROM properties WHERE id = ?", (property_id,))
            conn.commit()
            return cur.rowcount > 0
