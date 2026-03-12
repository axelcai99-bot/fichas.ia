import re
import urllib.parse
import urllib.request
import json
from typing import Callable, Any


CLOUDFLARE_ACCOUNT_ID = "TU_ACCOUNT_ID"
CLOUDFLARE_API_TOKEN  = "TU_API_TOKEN"
CF_MARKDOWN_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/"
    f"{CLOUDFLARE_ACCOUNT_ID}/browser-rendering/markdown"
)


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
    # Paso 1: Cloudflare /scrape → Markdown
    # ──────────────────────────────────────────────

    def _fetch_markdown(
        self, url: str, log: Callable[[str], None]
    ) -> str:
        payload = json.dumps({
            "url": url,
            # Para sitios con mucho JS esperamos a que quede casi sin tráfico de red.
            "gotoOptions": {
                "waitUntil": "networkidle0",
            },
        }).encode()

        req = urllib.request.Request(
            CF_MARKDOWN_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
                "Content-Type":  "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read())
        except Exception as e:
            raise RuntimeError(f"Error en Cloudflare /markdown: {e}") from e

        if not body.get("success"):
            errors = body.get("errors", [])
            raise RuntimeError(f"Cloudflare /markdown falló: {errors}")

        # En el endpoint /markdown, result es directamente un string con el Markdown.
        markdown = body.get("result") or ""
        if not markdown:
            raise RuntimeError("Cloudflare devolvió Markdown vacío")

        log(f"Markdown obtenido: {len(markdown)} caracteres")
        return markdown

    # ──────────────────────────────────────────────
    # Paso 2: LLM → dict estructurado
    # ──────────────────────────────────────────────

    def _extract_with_llm(
        self, markdown: str, log: Callable[[str], None]
    ) -> dict[str, Any]:
        # Truncamos a ~12k chars para no exceder el contexto
        truncated = markdown[:12_000]

        import anthropic  # pip install anthropic
        client = anthropic.Anthropic()   # lee ANTHROPIC_API_KEY del entorno

        message = client.messages.create(
            model="claude-3-5-sonnet-20241022",   # modelo actual de alta calidad
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": EXTRACTION_PROMPT + truncated,
            }],
        )
        raw_text = message.content[0].text.strip()

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