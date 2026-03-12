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
Devolvé ÚNICAMENTE un objeto JSON válido con exactamente estas claves:

{
  "titulo": "string — título principal de la propiedad",
  "precio": "string — precio tal como aparece, ej: USD 150.000 o $ 80.000.000",
  "ubicacion": "string — dirección o barrio",
  "descripcion": "string — descripción completa de la propiedad",
  "ambientes": "string o null — número de ambientes si aparece",
  "banos": "string o null — número de baños",
  "metros_totales": "string o null — m² totales",
  "metros_cubiertos": "string o null — m² cubiertos",
  "cocheras": "string o null",
  "antiguedad": "string o null",
  "expensas": "string o null — expensas mensuales si aparecen",
  "caracteristicas": ["array de strings — amenities, orientación, etc."],
  "image_urls": ["array de URLs de imágenes que aparezcan en el Markdown"]
}

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

        log("Obteniendo contenido vía Cloudflare Browser Rendering...")
        markdown = self._fetch_markdown(source_url, log)

        log("Extrayendo datos estructurados con LLM...")
        extracted = self._extract_with_llm(markdown, log)

        # Separamos image_urls del resto para que PropertyService las descargue
        image_urls = extracted.pop("image_urls", [])
        caracteristicas_raw = extracted.pop("caracteristicas", [])

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
            result = app.scrape(url, params={"formats": ["markdown"]})
        except Exception as e:
            raise RuntimeError(f"Error llamando a Firecrawl: {e}") from e

        markdown = (result.get("markdown") or "").strip()
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
        # Truncamos a ~25k chars para no exceder el contexto
        truncated = markdown[:25_000]

        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            log("GEMINI_API_KEY no está configurada; usando fallback simple sin LLM.")
            return self._build_fallback_from_markdown(markdown)

        try:
            import google.generativeai as genai  # pip install google-generativeai
        except ImportError:
            log("No está instalado google-generativeai; usando fallback simple sin LLM.")
            return self._build_fallback_from_markdown(markdown)

        genai.configure(api_key=api_key)

        try:
            # Usamos un modelo Flash de la familia 2.5 (buena calidad/precio).
            model = genai.GenerativeModel("gemini-2.5-flash")
            response = model.generate_content(EXTRACTION_PROMPT + truncated)
            raw_text = (response.text or "").strip()
        except Exception as e:
            log(f"Error llamando a Gemini: {e}. Usando fallback simple.")
            return self._build_fallback_from_markdown(markdown)

        # Limpieza defensiva por si el LLM envuelve en ```json ... ```
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text)

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            log(f"Warning: LLM devolvió JSON inválido, usando fallback. Error: {e}")
            data = self._build_fallback_from_markdown(markdown)

        return data

    # ──────────────────────────────────────────────
    # Fallback si el LLM falla
    # ──────────────────────────────────────────────

    def _build_fallback_from_markdown(self, markdown: str) -> dict[str, Any]:
        price_match = re.search(
            r"(?:USD|U\$S|AR\$|\$)\s*[\d.,]+", markdown, re.I
        )
        return {
            "titulo":         self._first_h1(markdown) or "Propiedad en Venta",
            "precio":         price_match.group(0) if price_match else "Consultar precio",
            "ubicacion":      "Ver en el portal",
            "descripcion":    markdown[:500],
            "ambientes":      None,
            "banos":          None,
            "metros_totales": None,
            "metros_cubiertos": None,
            "cocheras":       None,
            "antiguedad":     None,
            "expensas":       None,
            "caracteristicas": [],
            "image_urls":     re.findall(r"https?://\S+\.(?:jpg|jpeg|png|webp)", markdown, re.I),
        }

    @staticmethod
    def _first_h1(markdown: str) -> str:
        m = re.search(r"^#\s+(.+)$", markdown, re.M)
        return m.group(1).strip() if m else ""

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