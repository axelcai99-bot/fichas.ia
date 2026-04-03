import os
import re
import shutil
import urllib.parse
import urllib.request
from email.message import Message
from typing import Any

from repositories.property_repository import PropertyRepository


MAX_IMAGES = 30  # consistente con scraper_service.MAX_IMAGES


class PropertyService:
    def __init__(self, property_repo: PropertyRepository, base_dir: str):
        self.property_repo = property_repo
        self.base_dir = base_dir

    def save_scraped_property(
        self,
        *,
        source_url: str,
        owner_username: str,
        agent_name: str,
        agent_whatsapp: str,
        form_url: str,
        scraped: dict[str, Any],
        log,
    ) -> int:
        source_image_urls = scraped.get("image_urls", []) or []
        property_id = self.property_repo.create_property(
            {
                "owner_username": owner_username,
                "source_portal": scraped.get("source_portal", "zonaprop"),
                "titulo": scraped["titulo"],
                "precio": scraped["precio"],
                "ubicacion": scraped["ubicacion"],
                "descripcion": scraped["descripcion"],
                "detalles": scraped.get("detalles", {}),
                "caracteristicas": scraped.get("caracteristicas", []),
                "info_adicional": scraped.get("info_adicional", {}),
                "image_paths": [],
                "source_image_urls": source_image_urls,
                "agent_name": agent_name,
                "agent_whatsapp": agent_whatsapp,
                "form_url": form_url,
                "source_url": source_url,
            }
        )
        log(f"Propiedad guardada en base de datos (id={property_id})")

        image_paths = self._download_images(
            property_id,
            scraped.get("image_urls", []),
            referer_url=source_url,
            log=log,
        )
        self.property_repo.update_image_paths(property_id, image_paths)
        return property_id

    def _download_images(
        self,
        property_id: int,
        image_urls: list[str],
        *,
        referer_url: str,
        log,
    ) -> list[str]:
        target_dir = os.path.join(self.base_dir, "static", "properties", str(property_id))
        os.makedirs(target_dir, exist_ok=True)

        saved: list[str] = []
        failed = 0
        origin = self._origin_from_url(referer_url)
        # Intentamos descargar todas las imágenes útiles detectadas, con un tope amplio.
        for index, image_url in enumerate(image_urls[:MAX_IMAGES], start=1):
            try:
                last_err: Exception | None = None
                data = b""
                ext = self._guess_ext(image_url)
                header_sets = [
                    self._image_request_headers(referer_url=referer_url, origin=origin, include_referer=True),
                    self._image_request_headers(referer_url=referer_url, origin=origin, include_referer=False),
                ]
                for headers in header_sets:
                    req = urllib.request.Request(image_url, headers=headers)
                    try:
                        with urllib.request.urlopen(req, timeout=30) as response:
                            data = response.read()
                            ext = self._guess_ext(image_url, response.headers)
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e
                if last_err is not None:
                    raise last_err
                # Filtrar imágenes demasiado pequeñas (iconos, badges, UI).
                dims = self._read_image_dimensions(data)
                if dims is not None:
                    w, h = dims
                    if min(w, h) < 250:
                        log(f"Imagen #{index} omitida (resolución {w}x{h}, probable ícono)")
                        continue
                filename = f"{index:02d}{ext}"
                file_path = os.path.join(target_dir, filename)
                with open(file_path, "wb") as f:
                    f.write(data)
                saved.append(f"/static/properties/{property_id}/{filename}")
            except Exception as e:
                failed += 1
                try:
                    preview_url = image_url
                    if len(preview_url) > 140:
                        preview_url = preview_url[:140] + "..."
                    log(f"No se pudo descargar la imagen #{index} ({preview_url}): {type(e).__name__}: {e}")
                except Exception:
                    # Si fallara el propio log, no rompemos el flujo de descarga.
                    pass
                continue

        if saved:
            log(f"Imagenes descargadas: {len(saved)} de {min(len(image_urls), MAX_IMAGES)}")
            if failed:
                log(f"Imagenes no descargadas: {failed}")
        else:
            remote_fallbacks = [url for url in image_urls[:MAX_IMAGES] if isinstance(url, str) and url.strip()]
            if remote_fallbacks:
                log("No se pudieron descargar imagenes localmente, se usaran URLs remotas")
                saved = remote_fallbacks
            else:
                log("No se pudieron descargar imagenes, se mostraran placeholders")
                saved = [self._placeholder_svg_url()] * 5
        return saved

    def save_from_cache(
        self,
        *,
        source_url: str,
        owner_username: str,
        agent_name: str,
        agent_whatsapp: str,
        form_url: str,
        cached: dict[str, Any],
        log,
    ) -> int:
        property_id = self.property_repo.create_property(
            {
                "owner_username": owner_username,
                "source_portal": cached.get("source_portal", "zonaprop"),
                "titulo": cached["titulo"],
                "precio": cached["precio"],
                "ubicacion": cached["ubicacion"],
                "descripcion": cached["descripcion"],
                "detalles": cached.get("detalles", {}),
                "caracteristicas": cached.get("caracteristicas", []),
                "info_adicional": cached.get("info_adicional", {}),
                "image_paths": [],
                "source_image_urls": cached.get("source_image_urls", []),
                "agent_name": agent_name,
                "agent_whatsapp": agent_whatsapp,
                "form_url": form_url,
                "source_url": source_url,
            }
        )
        log(f"Propiedad guardada en base de datos desde caché (id={property_id})")
        image_paths = self._copy_images_from_cache(cached["id"], property_id, cached.get("image_paths", []), log)
        self.property_repo.update_image_paths(property_id, image_paths)
        return property_id

    def _copy_images_from_cache(
        self,
        source_id: int,
        new_id: int,
        cached_image_paths: list[str],
        log,
    ) -> list[str]:
        source_dir = os.path.join(self.base_dir, "static", "properties", str(source_id))
        target_dir = os.path.join(self.base_dir, "static", "properties", str(new_id))

        if not cached_image_paths:
            return []

        # Si las imágenes no están descargadas localmente (son URLs remotas), las reutilizamos tal cual
        local_paths = [p for p in cached_image_paths if p.startswith("/static/")]
        if not local_paths or not os.path.isdir(source_dir):
            log("Imágenes en caché son remotas, reutilizando URLs originales")
            return cached_image_paths

        os.makedirs(target_dir, exist_ok=True)
        new_paths = []
        for old_path in cached_image_paths:
            filename = os.path.basename(old_path)
            src = os.path.join(source_dir, filename)
            dst = os.path.join(target_dir, filename)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
                new_paths.append(f"/static/properties/{new_id}/{filename}")
            else:
                new_paths.append(old_path)
        log(f"Imágenes copiadas desde caché: {len([p for p in new_paths if p.startswith('/static/')])}")
        return new_paths

    def delete_property(self, property_id: int, owner_username: str | None = None) -> bool:
        deleted = self.property_repo.delete_property(property_id, owner_username=owner_username)
        if not deleted:
            return False

        target_dir = os.path.join(self.base_dir, "static", "properties", str(property_id))
        try:
            if os.path.isdir(target_dir):
                shutil.rmtree(target_dir)
        except Exception:
            # Si falla el borrado de imagenes no bloqueamos la eliminacion en BD.
            pass
        return True

    @staticmethod
    def _read_image_dimensions(data: bytes) -> tuple[int, int] | None:
        """Devuelve (ancho, alto) en píxeles para JPEG, PNG y WebP sin librerías externas.
        Retorna None si el formato no es reconocible."""
        if len(data) < 24:
            return None

        # PNG: signature + IHDR chunk
        if data[:8] == b'\x89PNG\r\n\x1a\n' and len(data) >= 24:
            w = int.from_bytes(data[16:20], 'big')
            h = int.from_bytes(data[20:24], 'big')
            return (w, h)

        # WebP: RIFF....WEBP
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP' and len(data) >= 30:
            chunk = data[12:16]
            if chunk == b'VP8 ':   # lossy
                w = int.from_bytes(data[26:28], 'little') & 0x3FFF
                h = int.from_bytes(data[28:30], 'little') & 0x3FFF
                return (w, h)
            if chunk == b'VP8L' and len(data) >= 25:  # lossless
                bits = int.from_bytes(data[21:25], 'little')
                w = (bits & 0x3FFF) + 1
                h = ((bits >> 14) & 0x3FFF) + 1
                return (w, h)
            if chunk == b'VP8X' and len(data) >= 30:  # extended
                w = int.from_bytes(data[24:27], 'little') + 1
                h = int.from_bytes(data[27:30], 'little') + 1
                return (w, h)

        # JPEG: scan for SOF marker
        if data[:2] == b'\xff\xd8':
            i = 2
            while i < len(data) - 3:
                if data[i] != 0xFF:
                    break
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2) and i + 9 <= len(data):
                    h = int.from_bytes(data[i + 5:i + 7], 'big')
                    w = int.from_bytes(data[i + 7:i + 9], 'big')
                    return (w, h)
                seg_len = int.from_bytes(data[i + 2:i + 4], 'big')
                i += 2 + seg_len

        return None

    @staticmethod
    def _guess_ext(url: str, headers: Message | None = None) -> str:
        if headers:
            content_type = (headers.get_content_type() or "").lower()
            if content_type == "image/png":
                return ".png"
            if content_type == "image/webp":
                return ".webp"
            if content_type == "image/avif":
                return ".avif"
            if content_type == "image/jpeg":
                return ".jpg"
        low = url.lower()
        if re.search(r"\.png($|\?)", low):
            return ".png"
        if re.search(r"\.webp($|\?)", low):
            return ".webp"
        if re.search(r"\.avif($|\?)", low):
            return ".avif"
        if re.search(r"\.jpeg($|\?)", low):
            return ".jpeg"
        if re.search(r"\.jpg($|\?)", low):
            return ".jpg"
        return ".jpg"

    @staticmethod
    def _origin_from_url(url: str) -> str:
        parsed = urllib.parse.urlsplit(url or "")
        if not parsed.scheme or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _image_request_headers(*, referer_url: str, origin: str, include_referer: bool) -> dict[str, str]:
        headers = {
            # User-Agent más realista para tratar de evitar algunos bloqueos anti-bot.
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        if include_referer and referer_url:
            headers["Referer"] = referer_url
        if include_referer and origin:
            headers["Origin"] = origin
        return headers

    @staticmethod
    def _placeholder_svg_url() -> str:
        return "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='800' height='500'%3E%3Crect fill='%23eeeeee' width='800' height='500'/%3E%3C/svg%3E"
