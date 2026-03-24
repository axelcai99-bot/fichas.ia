import hashlib
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

from repositories.user_repository import UserRepository


def hash_pw(pw: str) -> str:
    return generate_password_hash(pw)


def _verify_pw(stored: str, pw: str) -> bool:
    """Verifica contraseña soportando hashes werkzeug (nuevos) y SHA256 (legacy)."""
    if stored.startswith("pbkdf2:") or stored.startswith("scrypt:"):
        return check_password_hash(stored, pw)
    # Legacy SHA256 sin prefijo
    return stored == hashlib.sha256(pw.encode()).hexdigest()


class AuthService:
    def __init__(self, user_repo: UserRepository):
        self.user_repo = user_repo

    def validate_login(self, username: str, password: str) -> dict[str, Any] | None:
        user = self.user_repo.get_user(username)
        if not user:
            return None
        if not user.get("active", True):
            return None
        if not _verify_pw(user.get("password", ""), password):
            return None
        # Migrar hash legacy SHA256 a werkzeug en el próximo login exitoso
        stored = user.get("password", "")
        if not (stored.startswith("pbkdf2:") or stored.startswith("scrypt:")):
            self.user_repo.update_password(username, hash_pw(password))
        return user

    def change_password(self, username: str, current_pw: str, new_pw: str) -> tuple[bool, str]:
        if len(new_pw) < 6:
            return False, "La contraseña debe tener al menos 6 caracteres"
        user = self.user_repo.get_user(username)
        if not user or user.get("password") != hash_pw(current_pw):
            return False, "Contraseña actual incorrecta"
        self.user_repo.update_password(username, hash_pw(new_pw))
        return True, "ok"

    def admin_create_user(self, username: str, password: str, nombre: str) -> tuple[bool, str]:
        username = username.strip().lower()
        if not username or not password or not nombre:
            return False, "Faltan datos"
        if len(password) < 6:
            return False, "Contraseña mínimo 6 caracteres"
        if self.user_repo.get_user(username):
            return False, "El usuario ya existe"
        self.user_repo.create_user(
            username=username,
            password_hash=hash_pw(password),
            nombre=nombre.strip(),
            role="user",
        )
        return True, username

    def admin_reset_password(self, username: str, new_pw: str) -> tuple[bool, str]:
        if len(new_pw) < 6:
            return False, "Mínimo 6 caracteres"
        user = self.user_repo.get_user(username)
        if not user:
            return False, "Usuario no encontrado"
        self.user_repo.update_password(username, hash_pw(new_pw))
        return True, "ok"

    def admin_delete_user(self, username: str, acting_username: str) -> tuple[bool, str]:
        username = (username or "").strip().lower()
        acting_username = (acting_username or "").strip().lower()
        if not username:
            return False, "Falta usuario"
        if username == "admin":
            return False, "No podés borrar al admin"
        if username == acting_username:
            return False, "No podés borrarte a vos mismo"
        user = self.user_repo.get_user(username)
        if not user:
            return False, "Usuario no encontrado"
        deleted = self.user_repo.delete_user(username)
        if not deleted:
            return False, "No se pudo borrar el usuario"
        return True, "ok"
