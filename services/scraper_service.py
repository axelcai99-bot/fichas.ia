import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from firecrawl import Firecrawl


class ScraperService:

    # ──────────────────────────────────────────────
    # Punto de entrada público
    # ──────────────────────────────────────────────

    def scrape_property(
        self, source_url: str, log: Callable[[str], None]
    ) -> dict[str, Any]:
        portal = self._detect_portal(source_url)
        log(f"Portal detectado: {portal}")

        log("Obteniendo contenido vía Firecrawl...")
        markdown, firecrawl_images = self._fetch_content(source_url, log)

        log("Procesando contenido estructurado desde Firecrawl...")
        extracted = self._extract_structured_data(markdown, log)

        # Separamos image_urls del resto para que PropertyService las descargue
        image_urls_llm = extracted.pop("image_urls", []) or []
        caracteristicas_raw = extracted.pop("caracteristicas", [])

        # Completamos y filtramos imágenes a partir del Markdown bruto + la lista "images" de Firecrawl.
        image_urls_from_markdown = self._extract_image_urls_from_markdown(markdown)
        image_urls_from_firecrawl = self._filter_image_urls(firecrawl_images)
        merged_image_urls: list[str] = []
        for url in image_urls_llm + image_urls_from_firecrawl + image_urls_from_markdown:
            if url not in merged_image_urls:
                merged_image_urls.append(url)
        image_urls = merged_image_urls[:20]

        detalles = {
            "ambientes":       extracted.pop("ambientes", None),
            "banos":           extracted.pop("banos", None),
            "metros_totales":  extracted.pop("metros_totales", None),
            "metros_cubiertos": extracted.pop("metros_cubiertos", None),
        }
        info_adicional = {
            "antiguedad": extracted.pop("antiguedad", None),
            "expensas":   extracted.pop("expensas", None),
            "cocheras":   extracted.pop("cocheras", None),
        }

        return {
            "titulo":        extracted.get("titulo") or "Propiedad en Venta",
            "precio":        extracted.get("precio") or "Consultar precio",
            "ubicacion":     extracted.get("ubicacion") or "Ver en el portal",
            "descripcion":   extracted.get("descripcion") or "Sin descripción",
            "detalles":      detalles,
            "caracteristicas": [c for c in caracteristicas_raw if c],
            "info_adicional": info_adicional,
            "image_urls":    image_urls,
            "source_portal": portal,
        }

    # ──────────────────────────────────────────────
    # Paso 1: Firecrawl → Markdown
    # ──────────────────────────────────────────────

    def _fetch_content(
        self, url: str, log: Callable[[str], None]
    ) -> tuple[str, list[str]]:
        api_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "FIRECRAWL_API_KEY no está configurada. "
                "Creá un API key de Firecrawl y ponelo en la variable de entorno FIRECRAWL_API_KEY."
            )

        app = Firecrawl(api_key=api_key)

        try:
            # Pedimos Markdown + imágenes detectadas.
            result = app.scrape(url, formats=["markdown", "images"])
        except Exception as e:
            raise RuntimeError(f"Error llamando a Firecrawl: {e}") from e

        # El SDK puede devolver un dict o un objeto Document.
        if isinstance(result, dict):
            markdown = (result.get("markdown") or "").strip()
            images = result.get("images") or []
        else:
            markdown = (getattr(result, "markdown", "") or "").strip()
            images = getattr(result, "images", None) or []
        if not markdown:
            raise RuntimeError("Firecrawl devolvió Markdown vacío")

        # Normalizamos images a lista de strings
        image_urls: list[str] = []
        if isinstance(images, list):
            for item in images:
                if isinstance(item, str):
                    image_urls.append(item)
                elif isinstance(item, dict):
                    u = item.get("url") or item.get("src")
                    if isinstance(u, str):
                        image_urls.append(u)

        log(f"Markdown obtenido desde Firecrawl: {len(markdown)} caracteres")
        log(f"Imágenes detectadas por Firecrawl: {len(image_urls)}")
        return markdown, image_urls

    # ──────────────────────────────────────────────
    # Paso 2: Markdown → dict estructurado
    # ──────────────────────────────────────────────

    def _extract_structured_data(
        self, markdown: str, log: Callable[[str], None]
    ) -> dict[str, Any]:
        log("Usando extracción heurística mejorada desde Markdown de Firecrawl.")
        return self._build_fallback_from_markdown(markdown)

    # ──────────────────────────────────────────────
    # Extracción heurística
    # ──────────────────────────────────────────────

    def _build_fallback_from_markdown(self, markdown: str) -> dict[str, Any]:
        price_match = re.search(r"(?:USD|U\$S|AR\$|\$)\s*[\d.,]+", markdown, re.I)
        descripcion = self._extract_description_from_markdown(markdown) or self._best_text_block(markdown)
        caracteristicas = self._extract_features_from_markdown(markdown)
        detalles = self._extract_detail_candidates(markdown)
        return {
            "titulo": self._extract_title(markdown) or "Propiedad en Venta",
            "precio": price_match.group(0) if price_match else "Consultar precio",
            "ubicacion": self._extract_location(markdown) or "Ver en el portal",
            "descripcion": descripcion,
            "ambientes": detalles.get("ambientes"),
            "banos": detalles.get("banos"),
            "metros_totales": detalles.get("metros_totales"),
            "metros_cubiertos": detalles.get("metros_cubiertos"),
            "cocheras": detalles.get("cocheras"),
            "antiguedad": detalles.get("antiguedad"),
            "expensas": detalles.get("expensas"),
            "caracteristicas": caracteristicas,
            "image_urls": self._extract_image_urls_from_markdown(markdown),
        }

    @staticmethod
    def _first_h1(markdown: str) -> str:
        m = re.search(r"^#\s+(.+)$", markdown, re.M)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_image_urls_from_markdown(markdown: str) -> list[str]:
        """
        Extrae hasta 20 URLs de imágenes desde el Markdown, filtrando logos, íconos y placeholders.
        """
        # 1) URLs en sintaxis Markdown de imagen: ![alt](url)
        md_image_urls = re.findall(r"!\[[^\]]*\]\((https?://[^\s)]+)\)", markdown, re.I)
        # 2) URLs sueltas, permitiendo querystring: .jpg?... .png?... etc
        raw_urls = re.findall(r"https?://[^\s)\"']+", markdown, re.I)
        urls = md_image_urls + raw_urls
        return ScraperService._filter_image_urls(urls)

    @staticmethod
    def _filter_image_urls(urls: list[str]) -> list[str]:
        """
        Filtra URLs de imágenes (logos/íconos) y deja hasta 20.
        """
        blacklist_substrings = (
            "logo",
            "favicon",
            "icon",
            "sprite",
            "placeholder",
            "watermark",
            "notesicon",
            "fav-",
            "fav_icon",
            ".svg",
        )
        filtered: list[str] = []
        for url in urls:
            if not isinstance(url, str):
                continue
            clean_url = url.strip().rstrip(").,")
            lu = clean_url.lower()
            if any(bad in lu for bad in blacklist_substrings):
                continue
            if not (lu.startswith("http://") or lu.startswith("https://")):
                continue
            if re.search(r"\.(css|js|svg|gif|ico|woff2?)(\?|$)", lu):
                continue
            looks_like_image = (
                re.search(r"\.(jpg|jpeg|png|webp|avif)(\?|$)", lu)
                or any(token in lu for token in ("/images/", "/image/", "/photos/", "/photo/", "img=", "image=", "photo="))
            )
            if not looks_like_image:
                continue
            if clean_url not in filtered:
                filtered.append(clean_url)
        return filtered[:20]

    @staticmethod
    def _extract_description_from_markdown(markdown: str) -> str:
        """
        Intenta extraer la descripción real desde secciones típicas del listing.
        """
        lines = [l.strip() for l in markdown.splitlines()]

        def _is_noise(line: str) -> bool:
            if not line:
                return False
            # Texto típico de UI de Zonaprop
            if re.search(r"\b(Favorito|Compartir|Notas personales|Ocultar aviso|Ver menos)\b", line, re.I):
                return True
            # Líneas que son casi todo imágenes markdown
            if re.fullmatch(r"!?(\[[^\]]*\])?!?\[[^\]]*\]\([^)]+\)\s*", line):
                return True
            if re.search(r"!\[[^\]]*\]\(https?://", line):
                return True
            # Si no tiene letras (solo números/símbolos), la descartamos
            if not re.search(r"[A-Za-zÁÉÍÓÚáéíóúñ]", line):
                return True
            return False

        # 1) Intentamos usar una sección marcada como "DESCRIPCION"
        start_idx = -1
        for i, l in enumerate(lines):
            if re.fullmatch(r"(#+\s*)?(DESCRIPCION|DESCRIPCIÓN)\s*:?\s*", l, re.I):
                start_idx = i + 1
                break

        collected: list[str] = []
        if start_idx != -1:
            for l in lines[start_idx:]:
                if not l:
                    if collected:
                        collected.append("")  # preserva párrafos
                    continue
                # Cortamos si parece otra sección
                if re.fullmatch(r"[A-ZÁÉÍÓÚÑ ]{4,}", l) and len(collected) > 3:
                    break
                if _is_noise(l):
                    continue
                collected.append(l)
                if sum(len(x) for x in collected) > 2500:
                    break
            text = "\n".join(collected).strip()
            if text:
                return text

        candidate = ScraperService._best_text_block(markdown)
        return candidate.strip()

    @staticmethod
    def _extract_features_from_markdown(markdown: str) -> list[str]:
        """
        Intenta extraer características/amenities como lista a partir del Markdown.
        """
        lines = [l.strip() for l in markdown.splitlines()]
        start_idx = -1
        for i, l in enumerate(lines):
            if re.fullmatch(r"(#+\s*)?(CARACTERISTICAS|CARACTERÍSTICAS)\s*:?\s*", l, re.I):
                start_idx = i + 1
                break
        feats: list[str] = []
        if start_idx == -1:
            return ScraperService._infer_feature_lines(markdown)

        for l in lines[start_idx:]:
            if not l:
                continue
            if re.fullmatch(r"[A-ZÁÉÍÓÚÑ ]{4,}", l) and len(feats) >= 5:
                break
            if re.search(r"\b(Favorito|Compartir|Notas personales|Ocultar aviso)\b", l, re.I):
                continue
            m = re.match(r"^[-*]\s+(.+)$", l)
            if m:
                val = m.group(1).strip()
            else:
                # algunas páginas listan características en líneas sueltas
                val = l.strip()
            if not val or len(val) > 120:
                continue
            if val not in feats:
                feats.append(val)
            if len(feats) >= 40:
                break

        inferred = ScraperService._infer_feature_lines(markdown)
        for item in inferred:
            if item not in feats:
                feats.append(item)
        return feats[:40]

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    @staticmethod
    def _detect_portal(source_url: str) -> str:
        host = urllib.parse.urlparse(source_url).netloc.lower()
        if "zonaprop"     in host: return "zonaprop"
        if "argenprop"    in host: return "argenprop"
        if "mercadolibre" in host: return "mercadolibre"
        return "unknown"

    @staticmethod
    def _extract_title(markdown: str) -> str:
        title = ScraperService._first_h1(markdown)
        if title:
            return title
        for line in [l.strip("# ").strip() for l in markdown.splitlines()]:
            if len(line) < 12 or len(line) > 140:
                continue
            if re.search(r"(USD|U\$S|AR\$|\$)", line, re.I):
                continue
            if re.search(r"\b(favorito|compartir|publicado|actualizado)\b", line, re.I):
                continue
            if re.search(r"[A-Za-zÁÉÍÓÚáéíóúñ]", line):
                return line
        return ""

    @staticmethod
    def _extract_location(markdown: str) -> str:
        patterns = [
            r"(?:Dirección|Ubicación)\s*:?\s*(.+)",
            r"(?m)^[>#\-\*\s]*([A-ZÁÉÍÓÚÑ][^\n]{6,120},\s*[A-ZÁÉÍÓÚÑa-záéíóúñ ]{3,80})$",
            r"(?m)^[>#\-\*\s]*([A-ZÁÉÍÓÚÑ][^\n]{6,120}\b(?:CABA|Capital Federal|Buenos Aires|Vicente López|San Isidro|Olivos|Palermo|Belgrano)\b[^\n]*)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, markdown, re.I)
            if match:
                value = re.sub(r"\s+", " ", match.group(1)).strip(" -|")
                if len(value) <= 120:
                    return value
        return ""

    @staticmethod
    def _best_text_block(markdown: str) -> str:
        lines = [l.strip() for l in markdown.splitlines()]
        blocks: list[str] = []
        current: list[str] = []

        def flush() -> None:
            if current:
                blocks.append("\n".join(current).strip())
                current.clear()

        for line in lines:
            if not line:
                flush()
                continue
            if ScraperService._is_noise_line(line):
                continue
            current.append(line)
        flush()

        blocks = [b for b in blocks if len(b) >= 120]
        if not blocks:
            blocks = [b for b in blocks if b]
        if not blocks:
            return markdown[:1200].strip()
        blocks.sort(key=lambda b: (ScraperService._text_score(b), len(b)), reverse=True)
        return blocks[0][:2500].strip()

    @staticmethod
    def _text_score(text: str) -> int:
        score = len(text)
        bonuses = [
            ("ambiente", 40),
            ("propiedad", 35),
            ("cocina", 25),
            ("living", 25),
            ("dormitorio", 25),
            ("baño", 25),
            ("balc", 20),
            ("ubic", 15),
            ("luminos", 15),
        ]
        lower = text.lower()
        for token, bonus in bonuses:
            if token in lower:
                score += bonus
        return score

    @staticmethod
    def _is_noise_line(line: str) -> bool:
        if not line:
            return True
        if re.search(r"\b(Favorito|Compartir|Notas personales|Ocultar aviso|Ver menos|Contactar|Denunciar)\b", line, re.I):
            return True
        if re.search(r"!\[[^\]]*\]\(https?://", line):
            return True
        if re.fullmatch(r"[#>*\-\s\d|.:/]+", line):
            return True
        if not re.search(r"[A-Za-zÁÉÍÓÚáéíóúñ]", line):
            return True
        return False

    @staticmethod
    def _extract_detail_candidates(markdown: str) -> dict[str, str | None]:
        patterns = {
            "ambientes": [
                r"(\d+)\s+ambientes?",
                r"Ambientes?\s*:?\s*(\d+)",
            ],
            "banos": [
                r"(\d+)\s+bañ[oa]s?",
                r"Baños?\s*:?\s*(\d+)",
            ],
            "metros_totales": [
                r"(\d+(?:[.,]\d+)?)\s*m[²2]\s*totales",
                r"Sup(?:erficie)?\s*total\s*:?\s*(\d+(?:[.,]\d+)?)\s*m[²2]",
            ],
            "metros_cubiertos": [
                r"(\d+(?:[.,]\d+)?)\s*m[²2]\s*cubiertos",
                r"Sup(?:erficie)?\s*cubierta\s*:?\s*(\d+(?:[.,]\d+)?)\s*m[²2]",
            ],
            "cocheras": [
                r"(\d+)\s+cocheras?",
                r"Cocheras?\s*:?\s*(\d+)",
            ],
            "antiguedad": [
                r"Antigüedad\s*:?\s*([^\n|]{1,40})",
                r"Antiguedad\s*:?\s*([^\n|]{1,40})",
            ],
            "expensas": [
                r"Expensas\s*:?\s*((?:USD|U\$S|AR\$|\$)\s*[\d.,]+)",
            ],
        }
        out: dict[str, str | None] = {key: None for key in patterns}
        for key, regexes in patterns.items():
            for regex in regexes:
                match = re.search(regex, markdown, re.I)
                if match:
                    out[key] = re.sub(r"\s+", " ", match.group(1)).strip(" -|")
                    break
        return out

    @staticmethod
    def _infer_feature_lines(markdown: str) -> list[str]:
        candidates: list[str] = []
        for line in [l.strip(" -*#\t") for l in markdown.splitlines()]:
            if len(line) < 3 or len(line) > 90:
                continue
            if ScraperService._is_noise_line(line):
                continue
            if re.search(r"(USD|U\$S|AR\$|\$)\s*[\d.,]+", line, re.I):
                continue
            if re.search(r"\b(?:m[²2]|ambientes?|bañ[oa]s?|cocheras?|expensas?)\b", line, re.I):
                candidates.append(line)
                continue
            if re.search(r"\b(balc[oó]n|terraza|patio|parrilla|pileta|lavadero|suite|luminos[oa]|apto cr[eé]dito|seguridad|sum|quincho|jard[ií]n)\b", line, re.I):
                candidates.append(line)
        deduped: list[str] = []
        for item in candidates:
            cleaned = re.sub(r"\s+", " ", item).strip(" -|")
            if cleaned and cleaned not in deduped:
                deduped.append(cleaned)
        return deduped[:20]
