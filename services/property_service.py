import os
import re
import shutil
import urllib.parse
import urllib.request
from email.message import Message
from typing import Any

from repositories.property_repository import PropertyRepository


MAX_IMAGES = 60


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
                filename = f"{index:02d}{ext}"
                file_path = os.path.join(target_dir, filename)
                with open(file_path, "wb") as f:
                    f.write(data)
                saved.append(f"/static/properties/{property_id}/{filename}")
            except Exception:
                failed += 1
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
            "User-Agent": "Mozilla/5.0",
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
