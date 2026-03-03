import os
import re
import shutil
import urllib.request
from typing import Any

from repositories.property_repository import PropertyRepository


class PropertyService:
    def __init__(self, property_repo: PropertyRepository, base_dir: str):
        self.property_repo = property_repo
        self.base_dir = base_dir

    def save_scraped_property(
        self,
        *,
        source_url: str,
        agent_name: str,
        agent_whatsapp: str,
        form_url: str,
        scraped: dict[str, Any],
        log,
    ) -> int:
        property_id = self.property_repo.create_property(
            {
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

        image_paths = self._download_images(property_id, scraped.get("image_urls", []), log)
        self.property_repo.update_image_paths(property_id, image_paths)
        return property_id

    def _download_images(self, property_id: int, image_urls: list[str], log) -> list[str]:
        target_dir = os.path.join(self.base_dir, "static", "properties", str(property_id))
        os.makedirs(target_dir, exist_ok=True)

        saved: list[str] = []
        for index, image_url in enumerate(image_urls, start=1):
            try:
                ext = self._guess_ext(image_url)
                filename = f"{index:02d}{ext}"
                file_path = os.path.join(target_dir, filename)
                req = urllib.request.Request(
                    image_url,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                with urllib.request.urlopen(req, timeout=20) as response:
                    data = response.read()
                with open(file_path, "wb") as f:
                    f.write(data)
                saved.append(f"/static/properties/{property_id}/{filename}")
            except Exception:
                continue

        if saved:
            log(f"Imagenes descargadas: {len(saved)}")
        else:
            log("No se pudieron descargar imagenes, se mostraran placeholders")
            saved = [self._placeholder_svg_url()] * 5
        return saved

    def delete_property(self, property_id: int) -> bool:
        deleted = self.property_repo.delete_property(property_id)
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
    def _guess_ext(url: str) -> str:
        low = url.lower()
        if low.endswith(".png"):
            return ".png"
        if low.endswith(".webp"):
            return ".webp"
        if low.endswith(".jpeg"):
            return ".jpeg"
        if re.search(r"\.jpg($|\?)", low):
            return ".jpg"
        return ".jpg"

    @staticmethod
    def _placeholder_svg_url() -> str:
        return "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='800' height='500'%3E%3Crect fill='%23eeeeee' width='800' height='500'/%3E%3C/svg%3E"
