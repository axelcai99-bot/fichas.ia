from typing import Any

from db import get_connection


class UserRepository:
    def get_user(self, username: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT username, password_hash, role, nombre, whatsapp, form_url, active, created_at
                FROM users
                WHERE username = ?
                """,
                (username,),
            ).fetchone()
        if not row:
            return None
        return {
            "username": row["username"],
            "password": row["password_hash"],
            "role": row["role"],
            "nombre": row["nombre"],
            "whatsapp": row["whatsapp"],
            "form_url": row["form_url"],
            "active": bool(row["active"]),
            "created": row["created_at"],
        }

    def list_users(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT username, role, nombre, active, created_at
                FROM users
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [
            {
                "username": r["username"],
                "nombre": r["nombre"] or r["username"],
                "role": r["role"],
                "active": bool(r["active"]),
                "created": r["created_at"],
            }
            for r in rows
        ]

    def update_profile(self, username: str, *, nombre: str, whatsapp: str, form_url: str) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE users
                SET nombre = ?, whatsapp = ?, form_url = ?
                WHERE username = ?
                """,
                (nombre, whatsapp, form_url, username),
            )
            conn.commit()

    def update_password(self, username: str, password_hash: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?",
                (password_hash, username),
            )
            conn.commit()

    def create_user(self, *, username: str, password_hash: str, nombre: str, role: str = "user") -> None:
        from datetime import datetime

        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO users(username, password_hash, role, nombre, whatsapp, form_url, active, created_at)
                VALUES (?, ?, ?, ?, '', '', 1, ?)
                """,
                (username, password_hash, role, nombre, datetime.now().isoformat()),
            )
            conn.commit()

    def toggle_user(self, username: str) -> bool | None:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT active FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if not row:
                return None
            new_active = 0 if row["active"] else 1
            conn.execute(
                "UPDATE users SET active = ? WHERE username = ?",
                (new_active, username),
            )
            conn.commit()
            return bool(new_active)

    def delete_user(self, username: str) -> bool:
        with get_connection() as conn:
            cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
            conn.commit()
            return cur.rowcount > 0
