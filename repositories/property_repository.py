import json
import os
from datetime import datetime
from typing import Any

from db import get_connection


class PropertyRepository:
    def create_property(self, payload: dict[str, Any]) -> int:
        token = os.urandom(16).hex()
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO properties(
                    owner_username, source_portal, titulo, precio, ubicacion, descripcion,
                    detalles_json, caracteristicas_json, info_adicional_json,
                    image_paths_json, source_image_urls_json, agent_name, agent_whatsapp, form_url,
                    source_url, public_token, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    token,
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def find_by_source_url(self, source_url: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT id, titulo, precio, ubicacion, descripcion,
                       detalles_json, caracteristicas_json, info_adicional_json,
                       image_paths_json, source_image_urls_json,
                       agent_name, agent_whatsapp, form_url, owner_username, source_portal,
                       source_url, created_at
                FROM properties
                WHERE source_url = ? AND deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (source_url,),
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
                       source_url, public_token, created_at
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
            "public_token": row["public_token"] or "",
            "created_at": row["created_at"],
        }

    def find_by_token(self, token: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT id, titulo, precio, ubicacion, descripcion,
                       detalles_json, caracteristicas_json, info_adicional_json,
                       image_paths_json, source_image_urls_json,
                       agent_name, agent_whatsapp, form_url, owner_username, source_portal,
                       source_url, public_token, created_at
                FROM properties WHERE public_token = ? AND deleted_at IS NULL
                """,
                (token,),
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
            "public_token": row["public_token"] or "",
            "created_at": row["created_at"],
        }

    def list_properties(self, limit: int = 50, offset: int = 0, owner_username: str | None = None, source_portal: str | None = None, search: str = "") -> dict[str, Any]:
        conditions = ["deleted_at IS NULL"]
        params: list = []
        if owner_username:
            conditions.append("owner_username = ?")
            params.append(owner_username)
        if source_portal:
            conditions.append("source_portal = ?")
            params.append(source_portal)
        if search:
            conditions.append("(titulo LIKE ? OR ubicacion LIKE ?)")
            q = f"%{search}%"
            params.extend([q, q])
        where = " AND ".join(conditions)

        with get_connection() as conn:
            count_row = conn.execute(f"SELECT COUNT(*) AS total FROM properties WHERE {where}", params).fetchone()
            total = count_row["total"] if count_row else 0
            rows = conn.execute(
                f"""
                SELECT id, titulo, precio, ubicacion, created_at, owner_username, source_portal, source_url, tags_json, public_token
                FROM properties WHERE {where}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            ).fetchall()
        items = [
            {
                "id": r["id"],
                "titulo": r["titulo"],
                "precio": r["precio"],
                "ubicacion": r["ubicacion"],
                "created_at": r["created_at"],
                "owner_username": r["owner_username"] or "admin",
                "source_portal": r["source_portal"] or "zonaprop",
                "source_url": r["source_url"] or "",
                "tags": json.loads(r["tags_json"] or "[]"),
                "public_token": r["public_token"] or "",
            }
            for r in rows
        ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def update_tags(self, property_id: int, owner_username: str, tags: list[str]) -> bool:
        with get_connection() as conn:
            cur = conn.execute(
                "UPDATE properties SET tags_json = ? WHERE id = ? AND owner_username = ? AND deleted_at IS NULL",
                (json.dumps(tags, ensure_ascii=False), property_id, owner_username),
            )
            conn.commit()
            return cur.rowcount > 0

    def soft_delete_all_properties(self, owner_username: str) -> int:
        now = datetime.now().isoformat()
        with get_connection() as conn:
            cur = conn.execute(
                "UPDATE properties SET deleted_at = ? WHERE owner_username = ? AND deleted_at IS NULL",
                (now, owner_username),
            )
            conn.commit()
            return cur.rowcount

    def soft_delete_property(self, property_id: int, owner_username: str | None = None) -> bool:
        now = datetime.now().isoformat()
        with get_connection() as conn:
            if owner_username:
                cur = conn.execute(
                    "UPDATE properties SET deleted_at = ? WHERE id = ? AND owner_username = ? AND deleted_at IS NULL",
                    (now, property_id, owner_username),
                )
            else:
                cur = conn.execute(
                    "UPDATE properties SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                    (now, property_id),
                )
            conn.commit()
            return cur.rowcount > 0

    def restore_property(self, property_id: int, owner_username: str) -> bool:
        with get_connection() as conn:
            cur = conn.execute(
                "UPDATE properties SET deleted_at = NULL WHERE id = ? AND owner_username = ? AND deleted_at IS NOT NULL",
                (property_id, owner_username),
            )
            conn.commit()
            return cur.rowcount > 0

    def list_deleted_properties(self, owner_username: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, titulo, precio, ubicacion, created_at, owner_username, source_portal, deleted_at
                FROM properties WHERE owner_username = ? AND deleted_at IS NOT NULL
                ORDER BY deleted_at DESC LIMIT 50
                """,
                (owner_username,),
            ).fetchall()
        return [
            {
                "id": r["id"], "titulo": r["titulo"], "precio": r["precio"],
                "ubicacion": r["ubicacion"], "created_at": r["created_at"],
                "source_portal": r["source_portal"] or "zonaprop", "deleted_at": r["deleted_at"],
            }
            for r in rows
        ]

    def delete_property(self, property_id: int, owner_username: str | None = None) -> bool:
        with get_connection() as conn:
            if owner_username:
                cur = conn.execute(
                    "DELETE FROM properties WHERE id = ? AND owner_username = ? AND deleted_at IS NOT NULL",
                    (property_id, owner_username),
                )
            else:
                cur = conn.execute("DELETE FROM properties WHERE id = ? AND deleted_at IS NOT NULL", (property_id,))
            conn.commit()
            return cur.rowcount > 0
