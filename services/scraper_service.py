import re
import urllib.parse
import urllib.request
import urllib.error
import json
import os
from typing import Callable, Any

from firecrawl import Firecrawl


EXTRACTION_PROMPT = """
Sos un extractor de datos de propiedades inmobiliarias argentinas.
Se te dará el contenido Markdown de una página de listing.
El Markdown puede incluir mucho ruido de interfaz (botones como Favorito, Compartir,
Notas personales, etc.). Ignorá todo lo que no sea información real de la propiedad.

Devolvé ÚNICAMENTE un objeto JSON válido con exactamente estas claves:

{
  "titulo": "string — título principal de la propiedad",
  "precio": "string — precio tal como aparece, ej: USD 150.000 o $ 80.000.000",
  "ubicacion": "string — dirección o barrio",
  "descripcion": "string — descripción completa y legible de la propiedad (al menos 200 caracteres si es posible)",
  "ambientes": "string o null — número de ambientes si aparece",
  "banos": "string o null — número de baños",
  "metros_totales": "string o null — m² totales",
  "metros_cubiertos": "string o null — m² cubiertos",
  "cocheras": "string o null",
  "antiguedad": "string o null",
  "expensas": "string o null — expensas mensuales si aparecen",
  "caracteristicas": ["array de strings — amenities, terminaciones, servicios, orientación, etc. (cada item corto y descriptivo)"],
  "image_urls": ["array de hasta 20 URLs de fotos grandes de la propiedad (interiores/exteriores). Excluí logos, íconos, favicons, placeholders o marcas de agua evidentes."]
}

Reglas importantes:
- Ignorá texto de UI como Favorito, Compartir, Notas personales, botones o menús.
- Para "descripcion", usá el texto corrido que describa la propiedad (ambientes, estado, amenities, ubicación, etc.).
- Para "caracteristicas", devolvé una lista de bullets limpios (sin numeración ni texto de interfaz).
- Para "image_urls", incluí solo fotos de la propiedad (interior, exterior, planos). Excluí cualquier logo de inmobiliaria, favicon, ícono pequeño o sprite.

Si un campo no está disponible, usá null (no omitas la clave).
No incluyas explicaciones, solo el JSON.

Contenido Markdown:
"""


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
        markdown = self._fetch_markdown(source_url, log)

        log("Extrayendo datos estructurados con LLM...")
        extracted = self._extract_with_llm(markdown, log)

        # Separamos image_urls del resto para que PropertyService las descargue
        image_urls_llm = extracted.pop("image_urls", []) or []
        caracteristicas_raw = extracted.pop("caracteristicas", [])

        # Completamos y filtramos imágenes a partir del Markdown bruto.
        image_urls_from_markdown = self._extract_image_urls_from_markdown(markdown)
        merged_image_urls: list[str] = []
        for url in image_urls_llm + image_urls_from_markdown:
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

    def _fetch_markdown(
        self, url: str, log: Callable[[str], None]
    ) -> str:
        api_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "FIRECRAWL_API_KEY no está configurada. "
                "Creá un API key de Firecrawl y ponelo en la variable de entorno FIRECRAWL_API_KEY."
            )

        app = Firecrawl(api_key=api_key)

        try:
            # Pedimos específicamente Markdown limpio.
            result = app.scrape(url, formats=["markdown"])
        except Exception as e:
            raise RuntimeError(f"Error llamando a Firecrawl: {e}") from e

        # El SDK puede devolver un dict o un objeto Document.
        if isinstance(result, dict):
            markdown = (result.get("markdown") or "").strip()
        else:
            markdown = (getattr(result, "markdown", "") or "").strip()
        if not markdown:
            raise RuntimeError("Firecrawl devolvió Markdown vacío")

        log(f"Markdown obtenido desde Firecrawl: {len(markdown)} caracteres")
        return markdown

    # ──────────────────────────────────────────────
    # Paso 2: LLM → dict estructurado
    # ──────────────────────────────────────────────

    def _extract_with_llm(
        self, markdown: str, log: Callable[[str], None]
    ) -> dict[str, Any]:
        # Por ahora no usamos LLM: solo heurísticas sobre el Markdown de Firecrawl.
        log("LLM deshabilitado; usando extracción heurística desde Markdown.")
        return self._build_fallback_from_markdown(markdown)

    # ──────────────────────────────────────────────
    # Fallback si el LLM falla
    # ──────────────────────────────────────────────

    def _build_fallback_from_markdown(self, markdown: str) -> dict[str, Any]:
        price_match = re.search(
            r"(?:USD|U\$S|AR\$|\$)\s*[\d.,]+", markdown, re.I
        )
        descripcion = self._extract_description_from_markdown(markdown) or markdown[:800]
        caracteristicas = self._extract_features_from_markdown(markdown)
        return {
            "titulo":         self._first_h1(markdown) or "Propiedad en Venta",
            "precio":         price_match.group(0) if price_match else "Consultar precio",
            "ubicacion":      "Ver en el portal",
            "descripcion":    descripcion,
            "ambientes":      None,
            "banos":          None,
            "metros_totales": None,
            "metros_cubiertos": None,
            "cocheras":       None,
            "antiguedad":     None,
            "expensas":       None,
            "caracteristicas": caracteristicas,
            "image_urls":     self._extract_image_urls_from_markdown(markdown),
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
        raw_urls = re.findall(
            r"https?://[^\s)]+?\.(?:jpg|jpeg|png|webp)(?:\?[^\s)]*)?",
            markdown,
            re.I,
        )
        urls = md_image_urls + raw_urls
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
            lu = url.lower()
            if any(bad in lu for bad in blacklist_substrings):
                continue
            # descartamos recursos no-foto comunes
            if not re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", lu):
                continue
            if url not in filtered:
                filtered.append(url)

        return filtered[:20]

    @staticmethod
    def _extract_description_from_markdown(markdown: str) -> str:
        """
        Intenta extraer la descripción real desde secciones típicas del listing.
        """
        lines = [l.strip() for l in markdown.splitlines()]
        # Buscamos un encabezado tipo "DESCRIPCION" / "Descripción"
        start_idx = -1
        for i, l in enumerate(lines):
            if re.fullmatch(r"(DESCRIPCION|DESCRIPCIÓN|DESCRIPCION:|DESCRIPCIÓN:)", l, re.I):
                start_idx = i + 1
                break
        if start_idx == -1:
            return ""

        collected: list[str] = []
        for l in lines[start_idx:]:
            if not l:
                if collected:
                    collected.append("")  # preserva párrafos
                continue
            # Cortamos si parece otra sección
            if re.fullmatch(r"[A-ZÁÉÍÓÚÑ ]{4,}", l) and len(collected) > 3:
                break
            # Filtramos ruido típico
            if re.search(r"\b(Favorito|Compartir|Notas personales|Ocultar aviso)\b", l, re.I):
                continue
            collected.append(l)

            if sum(len(x) for x in collected) > 2500:
                break

        text = "\n".join(collected).strip()
        return text

    @staticmethod
    def _extract_features_from_markdown(markdown: str) -> list[str]:
        """
        Intenta extraer características/amenities como lista a partir del Markdown.
        """
        lines = [l.strip() for l in markdown.splitlines()]
        start_idx = -1
        for i, l in enumerate(lines):
            if re.fullmatch(r"(CARACTERISTICAS|CARACTERÍSTICAS|CARACTERISTICAS:|CARACTERÍSTICAS:)", l, re.I):
                start_idx = i + 1
                break
        if start_idx == -1:
            return []

        feats: list[str] = []
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

        return feats

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