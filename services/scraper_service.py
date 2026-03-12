import os
import re
from html import unescape
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from firecrawl import Firecrawl


MAX_IMAGES = 60

_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "titulo":           {"type": "string",  "description": "Título completo de la propiedad, ej: Departamento en Venta 3 ambientes Palermo"},
        "precio":           {"type": "string",  "description": "Precio de venta o alquiler, incluyendo moneda, ej: USD 120.000 o $ 350.000"},
        "ubicacion":        {"type": "string",  "description": "Dirección o zona de la propiedad, ej: Av. Santa Fe 1234, Palermo, CABA"},
        "descripcion":      {"type": "string",  "description": "Descripción larga de la propiedad, tal como aparece en el portal, sin cortar"},
        "ambientes":        {"type": "string",  "description": "Cantidad de ambientes, ej: 3"},
        "banos":            {"type": "string",  "description": "Cantidad de baños, ej: 2"},
        "metros_totales":   {"type": "string",  "description": "Superficie total en m², ej: 85"},
        "metros_cubiertos": {"type": "string",  "description": "Superficie cubierta en m², ej: 70"},
        "cocheras":         {"type": "string",  "description": "Cantidad de cocheras, ej: 1"},
        "antiguedad":       {"type": "string",  "description": "Antigüedad del inmueble, ej: A estrenar, 5 años, 20 años"},
        "expensas":         {"type": "string",  "description": "Monto de expensas, ej: $ 45.000"},
        "caracteristicas":  {
            "type": "array",
            "items": {"type": "string"},
            "description": "Lista de características y amenities de la propiedad, ej: ['Balcón', 'Luminoso', 'Apto crédito', 'Parrilla', 'Sum']"
        },
    },
    "required": ["titulo", "precio", "ubicacion", "descripcion"],
}


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
        payload = self._fetch_content(source_url, portal, log)
        markdown = payload["markdown"]
        firecrawl_images = payload["images"]
        html = payload["html"]
        raw_html = payload["raw_html"]
        extracted_data = payload["extracted"]  # puede ser None si falló

        # Preferimos los datos estructurados de Firecrawl Extract si los tenemos
        if extracted_data:
            log("Datos estructurados obtenidos con Firecrawl Extract ✓")
            extracted = extracted_data
        else:
            log("Usando extracción heurística desde Markdown...")
            extracted = self._build_fallback_from_content(markdown, raw_html or html)

        # Separamos image_urls del resto para que PropertyService las descargue
        image_urls_llm = extracted.pop("image_urls", []) or []
        caracteristicas_raw = extracted.pop("caracteristicas", []) or []

        # Fusionamos imágenes de todas las fuentes
        image_urls_from_markdown = self._extract_image_urls_from_markdown(markdown)
        image_urls_from_firecrawl = self._filter_image_urls(firecrawl_images)
        image_urls_from_html = self._extract_image_urls_from_html(raw_html or html)
        merged_image_urls: list[str] = []
        for url in image_urls_llm + image_urls_from_firecrawl + image_urls_from_html + image_urls_from_markdown:
            if url not in merged_image_urls:
                merged_image_urls.append(url)
        image_urls = merged_image_urls[:MAX_IMAGES]

        detalles = {
            "ambientes":        extracted.pop("ambientes", None),
            "banos":            extracted.pop("banos", None),
            "metros_totales":   extracted.pop("metros_totales", None),
            "metros_cubiertos": extracted.pop("metros_cubiertos", None),
        }
        info_adicional = {
            "antiguedad": extracted.pop("antiguedad", None),
            "expensas":   extracted.pop("expensas", None),
            "cocheras":   extracted.pop("cocheras", None),
        }

        return {
            "titulo":          extracted.get("titulo") or "Propiedad en Venta",
            "precio":          extracted.get("precio") or "Consultar precio",
            "ubicacion":       extracted.get("ubicacion") or "Ver en el portal",
            "descripcion":     extracted.get("descripcion") or "Sin descripción",
            "detalles":        detalles,
            "caracteristicas": [c for c in caracteristicas_raw if c],
            "info_adicional":  info_adicional,
            "image_urls":      image_urls,
            "source_portal":   portal,
        }

    # ──────────────────────────────────────────────
    # Paso 1: Firecrawl → Markdown + Extract
    # ──────────────────────────────────────────────

    def _fetch_content(
        self, url: str, portal: str, log: Callable[[str], None]
    ) -> dict[str, Any]:
        api_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "FIRECRAWL_API_KEY no está configurada. "
                "Creá un API key de Firecrawl y ponelo en la variable de entorno FIRECRAWL_API_KEY."
            )

        app = Firecrawl(api_key=api_key)

        try:
            result = app.scrape(
                url,
                formats=["markdown", "html", "rawHtml", "images", "extract"],
                only_main_content=False,
                wait_for=1500,
                timeout=30000,
                location={"country": "AR", "languages": ["es-AR", "es"]},
                actions=self._actions_for_portal(portal),
                extract={
                    "schema": _EXTRACT_SCHEMA,
                    "systemPrompt": (
                        "Sos un experto en extracción de datos de portales inmobiliarios argentinos. "
                        "Extraé con precisión todos los datos de la propiedad. "
                        "Para la descripción, copiá el texto completo tal como aparece en el portal, sin resumir ni cortar. "
                        "Para características, listá todos los amenities y detalles físicos del inmueble."
                    ),
                },
            )
        except Exception as e:
            raise RuntimeError(f"Error llamando a Firecrawl: {e}") from e

        # El SDK puede devolver un dict o un objeto Document.
        if isinstance(result, dict):
            markdown  = (result.get("markdown") or "").strip()
            images    = result.get("images") or []
            html      = (result.get("html") or "").strip()
            raw_html  = (result.get("rawHtml") or result.get("raw_html") or "").strip()
            extracted = result.get("extract") or None
        else:
            markdown  = (getattr(result, "markdown", "") or "").strip()
            images    = getattr(result, "images", None) or []
            html      = (getattr(result, "html", "") or "").strip()
            raw_html  = (getattr(result, "rawHtml", None) or getattr(result, "raw_html", "") or "").strip()
            extracted = getattr(result, "extract", None) or None

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

        log(f"Markdown obtenido: {len(markdown)} caracteres")
        log(f"Imágenes detectadas: {len(image_urls)}")
        if extracted:
            log(f"Datos estructurados extraídos: {list(extracted.keys())}")

        return {
            "markdown":  markdown,
            "images":    image_urls,
            "html":      html,
            "raw_html":  raw_html,
            "extracted": extracted if isinstance(extracted, dict) else None,
        }

    # ──────────────────────────────────────────────
    # Fallback heurístico (se usa si Extract falla)
    # ──────────────────────────────────────────────

    def _build_fallback_from_content(self, markdown: str, html: str) -> dict[str, Any]:
        source_text = self._merge_sources(markdown, html)
        price_match = re.search(r"(?:USD|U\$S|AR\$|\$)\s*[\d.,]+", markdown, re.I)
        descripcion = self._extract_description(source_text, html) or self._best_text_block(source_text)
        caracteristicas = self._extract_features(source_text, html)
        detalles = self._extract_detail_candidates(source_text)
        detalles_html = self._extract_detail_candidates_from_html(html)
        detalles.update({k: v for k, v in detalles_html.items() if v})
        return {
            "titulo":           self._extract_title(source_text) or "Propiedad en Venta",
            "precio":           price_match.group(0) if price_match else "Consultar precio",
            "ubicacion":        self._extract_location(source_text) or "Ver en el portal",
            "descripcion":      descripcion,
            "ambientes":        detalles.get("ambientes"),
            "banos":            detalles.get("banos"),
            "metros_totales":   detalles.get("metros_totales"),
            "metros_cubiertos": detalles.get("metros_cubiertos"),
            "cocheras":         detalles.get("cocheras"),
            "antiguedad":       detalles.get("antiguedad"),
            "expensas":         detalles.get("expensas"),
            "caracteristicas":  self._merge_feature_lists(caracteristicas, self._details_to_features(detalles)),
            "image_urls":       self._extract_image_urls_from_html(html) + self._extract_image_urls_from_markdown(markdown),
        }

    # ──────────────────────────────────────────────
    # Helpers — sin cambios respecto al original
    # ──────────────────────────────────────────────

    @staticmethod
    def _first_h1(markdown: str) -> str:
        m = re.search(r"^#\s+(.+)$", markdown, re.M)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_image_urls_from_markdown(markdown: str) -> list[str]:
        md_image_urls = re.findall(r"!\[[^\]]*\]\((https?://[^\s)]+)\)", markdown, re.I)
        raw_urls = re.findall(r"https?://[^\s)\"']+", markdown, re.I)
        urls = md_image_urls + raw_urls
        return ScraperService._filter_image_urls(urls)

    @staticmethod
    def _filter_image_urls(urls: list[str]) -> list[str]:
        blacklist_substrings = (
            "logo", "favicon", "icon", "sprite", "placeholder",
            "watermark", "notesicon", "fav-", "fav_icon", ".svg",
        )
        filtered: list[str] = []
        seen_keys: set[str] = set()
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
            dedupe_key = ScraperService._image_dedupe_key(clean_url)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            if clean_url not in filtered:
                filtered.append(clean_url)
        return filtered[:MAX_IMAGES]

    @staticmethod
    def _extract_description(text: str, html: str) -> str:
        html_description = ScraperService._extract_description_from_html(html)
        if html_description:
            return html_description
        lines = [l.strip() for l in text.splitlines()]

        def _is_noise(line: str) -> bool:
            if not line:
                return False
            if re.search(r"\b(Favorito|Compartir|Notas personales|Ocultar aviso|Ver menos)\b", line, re.I):
                return True
            if re.fullmatch(r"!?(\[[^\]]*\])?!?\[[^\]]*\]\([^)]+\)\s*", line):
                return True
            if re.search(r"!\[[^\]]*\]\(https?://", line):
                return True
            if not re.search(r"[A-Za-zÁÉÍÓÚáéíóúñ]", line):
                return True
            return False

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
                        collected.append("")
                    continue
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

        return ScraperService._best_text_block(text).strip()

    @staticmethod
    def _extract_features(text: str, html: str) -> list[str]:
        html_features = ScraperService._extract_features_from_html(html)
        lines = [l.strip() for l in text.splitlines()]
        start_idx = -1
        for i, l in enumerate(lines):
            if re.fullmatch(r"(#+\s*)?(CARACTERISTICAS|CARACTERÍSTICAS)\s*:?\s*", l, re.I):
                start_idx = i + 1
                break
        feats: list[str] = []
        if start_idx == -1:
            inferred = ScraperService._infer_feature_lines(text)
            return ScraperService._filter_feature_noise(
                ScraperService._merge_feature_lists(html_features, inferred)
            )
        for l in lines[start_idx:]:
            if not l:
                continue
            if re.fullmatch(r"[A-ZÁÉÍÓÚÑ ]{4,}", l) and len(feats) >= 5:
                break
            if re.search(r"\b(Favorito|Compartir|Notas personales|Ocultar aviso)\b", l, re.I):
                continue
            m = re.match(r"^[-*]\s+(.+)$", l)
            val = m.group(1).strip() if m else l.strip()
            if not val or len(val) > 120:
                continue
            if val not in feats:
                feats.append(val)
            if len(feats) >= 40:
                break
        inferred = ScraperService._infer_feature_lines(text)
        return ScraperService._filter_feature_noise(
            ScraperService._merge_feature_lists(html_features, feats, inferred)
        )

    @staticmethod
    def _detect_portal(source_url: str) -> str:
        host = urllib.parse.urlparse(source_url).netloc.lower()
        if "zonaprop"     in host: return "zonaprop"
        if "argenprop"    in host: return "argenprop"
        if "mercadolibre" in host: return "mercadolibre"
        return "unknown"

    @staticmethod
    def _actions_for_portal(portal: str) -> list[dict[str, Any]]:
        if portal != "zonaprop":
            return [{"type": "wait", "milliseconds": 1500}]
        return [
            {"type": "wait", "milliseconds": 1800},
            {
                "type": "executeJavascript",
                "script": """
                (() => {
                  const clickByText = (texts) => {
                    const nodes = Array.from(document.querySelectorAll('button, a, span, div'));
                    for (const node of nodes) {
                      const text = (node.innerText || node.textContent || '').trim().toLowerCase();
                      if (texts.some(t => text.includes(t))) {
                        node.click();
                        return true;
                      }
                    }
                    return false;
                  };
                  clickByText(['aceptar', 'entendido']);
                  clickByText(['leer más', 'ver más']);
                  clickByText(['ver todas las fotos', 'ver fotos', 'más fotos']);
                  return 'ok';
                })();
                """,
            },
            {"type": "wait", "milliseconds": 2200},
            {"type": "scroll", "direction": "down"},
            {"type": "wait", "milliseconds": 800},
            {
                "type": "executeJavascript",
                "script": """
                (() => {
                  const clickByText = (texts) => {
                    const nodes = Array.from(document.querySelectorAll('button, a, span, div'));
                    for (const node of nodes) {
                      const text = (node.innerText || node.textContent || '').trim().toLowerCase();
                      if (texts.some(t => text.includes(t))) {
                        node.click();
                        return true;
                      }
                    }
                    return false;
                  };
                  clickByText(['ver todas las fotos', 'ver fotos', 'más fotos']);
                  return 'ok';
                })();
                """,
            },
            {"type": "wait", "milliseconds": 2200},
        ]

    @staticmethod
    def _merge_sources(markdown: str, html: str) -> str:
        html_text = ScraperService._html_to_text(html)
        if not html_text:
            return markdown
        return f"{markdown}\n\n{html_text}"

    @staticmethod
    def _html_to_text(html: str) -> str:
        if not html:
            return ""
        text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
        text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</p>|</div>|</li>|</section>|</article>|</h\d>", "\n", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = unescape(text)
        text = text.replace("\xa0", " ")
        text = re.sub(r"\r", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()

    @staticmethod
    def _extract_image_urls_from_html(html: str) -> list[str]:
        if not html:
            return []
        urls = re.findall(r"""https?://[^\s"'<>]+""", html, re.I)
        return ScraperService._filter_image_urls(urls)

    @staticmethod
    def _extract_description_from_html(html: str) -> str:
        text = ScraperService._html_to_text(html)
        if not text:
            return ""
        patterns = [
            r"Descripción\s*(.+?)(?:Leer menos|Características|Servicios|Ubicación|Mapa|Propiedades similares)",
            r"(Venta de .*?(?:Capital Federal|Buenos Aires)\..+?)(?:LEPORE|AVISO LEGAL|XINTEL|Leer menos)",
            r"(Departamento .*?(?:Capital Federal|Buenos Aires)\..+?)(?:LEPORE|AVISO LEGAL|XINTEL|Leer menos)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.I | re.S)
            if match:
                candidate = re.sub(r"\s+\n", "\n", match.group(1)).strip()
                candidate = ScraperService._clean_description(candidate)
                if len(candidate) >= 120:
                    return candidate
        return ""

    @staticmethod
    def _clean_description(text: str) -> str:
        if not text:
            return ""
        text = re.sub(r"\bVer datos\b\.?", "", text, flags=re.I)
        text = re.sub(r"\bLEPORE SAN CRISTOBAL\b.*$", "", text, flags=re.I | re.S)
        text = re.sub(r"\bLEPORE PROPIEDADES\b.*$", "", text, flags=re.I | re.S)
        text = re.sub(r"\bAVISO LEGAL:.*$", "", text, flags=re.I | re.S)
        text = re.sub(r"\bXINTEL.*$", "", text, flags=re.I | re.S)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip(" .\n")

    @staticmethod
    def _extract_features_from_html(html: str) -> list[str]:
        text = ScraperService._html_to_text(html)
        if not text:
            return []
        candidates: list[str] = []
        for line in [l.strip(" -") for l in text.splitlines()]:
            if len(line) < 3 or len(line) > 100:
                continue
            if re.search(r"\b(Departamento|Venta de|Favorito|Compartir|Notas personales|Ocultar aviso|Leer menos|Ver todas las fotos)\b", line, re.I):
                continue
            if re.search(r"\b(\d+\s*m[²2]|\d+\s+ambientes?|\d+\s+bañ[oa]s?|balc[oó]n|terraza|patio|parrilla|pileta|lavadero|suite|luminos[oa]|apto cr[eé]dito|seguridad|sum|quincho|jard[ií]n|cocina independiente|living-comedor|placard)\b", line, re.I):
                candidates.append(line)
        return ScraperService._merge_feature_lists(candidates)

    @staticmethod
    def _merge_feature_lists(*groups: list[str]) -> list[str]:
        merged: list[str] = []
        for group in groups:
            for item in group:
                cleaned = re.sub(r"\s+", " ", (item or "")).strip(" -|")
                if cleaned and cleaned not in merged:
                    merged.append(cleaned)
        return merged[:40]

    @staticmethod
    def _extract_title(markdown: str) -> str:
        title = ScraperService._first_h1(markdown)
        if title:
            return ScraperService._clean_title(title)
        for line in [l.strip("# ").strip() for l in markdown.splitlines()]:
            if len(line) < 12 or len(line) > 140:
                continue
            if re.search(r"(USD|U\$S|AR\$|\$)", line, re.I):
                continue
            if re.search(r"\b(favorito|compartir|publicado|actualizado)\b", line, re.I):
                continue
            if re.search(r"[A-Za-zÁÉÍÓÚáéíóúñ]", line):
                return ScraperService._clean_title(line)
        return ""

    @staticmethod
    def _extract_location(markdown: str) -> str:
        markdown = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", markdown)
        patterns = [
            r"(?:Dirección|Ubicación)\s*:?\s*(.+)",
            r"(?m)^(Capital Federal|CABA|Buenos Aires|San Cristóbal|San Cristobal|Palermo|Belgrano|Caballito|Recoleta|Vicente López|Vicente Lopez|San Isidro)\s*$",
            r"(?m)^[>#\-\*\s]*([A-ZÁÉÍÓÚÑ][^\n]{6,120},\s*[A-ZÁÉÍÓÚÑa-záéíóúñ ]{3,80})$",
            r"(?m)^[>#\-\*\s]*([A-ZÁÉÍÓÚÑ][^\n]{6,120}\b(?:CABA|Capital Federal|Buenos Aires|Vicente López|San Isidro|Olivos|Palermo|Belgrano)\b[^\n]*)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, markdown, re.I)
            if match:
                value = re.sub(r"\s+", " ", match.group(1)).strip(" -|")
                value = re.sub(r"https?://\S+", "", value).strip()
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
            ("ambiente", 40), ("propiedad", 35), ("cocina", 25),
            ("living", 25), ("dormitorio", 25), ("baño", 25),
            ("balc", 20), ("ubic", 15), ("luminos", 15),
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
            "ambientes":        [r"(\d+)\s+ambientes?", r"Ambientes?\s*:?\s*(\d+)"],
            "dormitorios":      [r"(\d+)\s+dorm(?:itorios?|\.?)", r"Dormitorios?\s*:?\s*(\d+)"],
            "banos":            [r"(\d+)\s+bañ[oa]s?", r"Baños?\s*:?\s*(\d+)"],
            "metros_totales":   [r"(\d+(?:[.,]\d+)?)\s*m[²2]\s*totales", r"Sup(?:erficie)?\s*total\s*:?\s*(\d+(?:[.,]\d+)?)\s*m[²2]"],
            "metros_cubiertos": [r"(\d+(?:[.,]\d+)?)\s*m[²2]\s*cubiertos", r"Sup(?:erficie)?\s*cubierta\s*:?\s*(\d+(?:[.,]\d+)?)\s*m[²2]"],
            "cocheras":         [r"(\d+)\s+cocheras?", r"Cocheras?\s*:?\s*(\d+)"],
            "antiguedad":       [r"Antigüedad\s*:?\s*([^\n|]{1,40})", r"Antiguedad\s*:?\s*([^\n|]{1,40})"],
            "expensas":         [r"Expensas\s*:?\s*((?:USD|U\$S|AR\$|\$)\s*[\d.,]+)"],
            "disposicion":      [r"\b(Frente|Contrafrente|Interno|Lateral)\b"],
            "orientacion":      [r"\b(Norte|Sur|Este|Oeste|NE|NO|SE|SO|N|S|E|O)\b"],
            "estado":           [r"\b(A estrenar|Excelente estado|Muy buen estado|Buen estado|En construcción)\b"],
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
            if re.search(r"\b(?:m[²2]|ambientes?|bañ[oa]s?|cocheras?|expensas?|dorm(?:itorios?|\.?)|a estrenar|frente|contrafrente)\b", line, re.I):
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

    @staticmethod
    def _image_dedupe_key(url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        path = (parsed.path or "").lower()
        path = re.sub(r"/(fit-in|crop|thumb|thumbnail|small|medium|large)/", "/", path)
        path = re.sub(r"[-_](?:\d{2,4}x\d{2,4}|w\d{2,4}|h\d{2,4})", "", path)
        filename = path.rsplit("/", 1)[-1]
        stem = re.sub(r"\.(jpg|jpeg|png|webp|avif)$", "", filename)
        stem = re.sub(r"[-_](?:scaled|thumb|thumbnail|small|medium|large)$", "", stem)
        return stem or path or url.lower()

    @staticmethod
    def _clean_title(title: str) -> str:
        title = re.sub(r"\s+", " ", (title or "")).strip()
        title = title.replace("\\", "")
        title = re.sub(r"\s+\|\s+", " | ", title)
        return title.strip(" -|")

    @staticmethod
    def _extract_detail_candidates_from_html(html: str) -> dict[str, str | None]:
        text = ScraperService._html_to_text(html)
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
        values: dict[str, str | None] = {
            "metros_totales": None, "metros_cubiertos": None,
            "ambientes": None, "banos": None, "dormitorios": None,
            "estado": None, "disposicion": None, "orientacion": None,
        }
        for line in lines:
            if not line:
                continue
            if not values["metros_totales"]:
                m = re.search(r"(\d+)\s*m²\s*tot\.?", line, re.I)
                if m: values["metros_totales"] = m.group(1)
            if not values["metros_cubiertos"]:
                m = re.search(r"(\d+)\s*m²\s*cub\.?", line, re.I)
                if m: values["metros_cubiertos"] = m.group(1)
            if not values["ambientes"]:
                m = re.search(r"(\d+)\s*amb\.?", line, re.I)
                if m: values["ambientes"] = m.group(1)
            if not values["banos"]:
                m = re.search(r"(\d+)\s*bañ[oa]s?", line, re.I)
                if m: values["banos"] = m.group(1)
            if not values["dormitorios"]:
                m = re.search(r"(\d+)\s*dorm\.?", line, re.I)
                if m: values["dormitorios"] = m.group(1)
            if not values["estado"]:
                m = re.search(r"\b(A estrenar|Excelente estado|Muy buen estado|Buen estado|En construcción)\b", line, re.I)
                if m: values["estado"] = m.group(1)
            if not values["disposicion"]:
                m = re.search(r"\b(Frente|Contrafrente|Interno|Lateral)\b", line, re.I)
                if m: values["disposicion"] = m.group(1)
            if not values["orientacion"]:
                m = re.search(r"\b(Norte|Sur|Este|Oeste|NE|NO|SE|SO|N|S|E|O)\b", line, re.I)
                if m: values["orientacion"] = m.group(1)
        return values

    @staticmethod
    def _details_to_features(detalles: dict[str, str | None]) -> list[str]:
        features: list[str] = []
        mappings = [
            ("metros_totales", "m² totales"), ("metros_cubiertos", "m² cubiertos"),
            ("ambientes", "ambientes"), ("banos", "baños"), ("dormitorios", "dormitorios"),
        ]
        for key, suffix in mappings:
            value = (detalles.get(key) or "").strip()
            if value:
                features.append(f"{value} {suffix}")
        for key in ("estado", "disposicion", "orientacion"):
            value = (detalles.get(key) or "").strip()
            if value:
                features.append(value)
        return features

    @staticmethod
    def _filter_feature_noise(features: list[str]) -> list[str]:
        cleaned_features: list[str] = []
        seen_normalized: set[str] = set()
        for feature in features:
            value = re.sub(r"\s+", " ", (feature or "")).strip(" -|:.,")
            if not value or len(value) < 4:
                continue
            normalized = value.lower()
            if normalized in {"ambientes", "ambiente", "departamentos", "propiedades"}:
                continue
            if re.search(r"\bdepartamentos?\s+en\s+venta\b", normalized):
                continue
            if re.search(r"\b(?:san\s+crist[oó]bal|capital\s+federal|ver\s+datos)\b", normalized):
                continue
            if re.search(r"\b(publicado|actualizado|favorito|compartir|notas personales)\b", normalized):
                continue
            if re.fullmatch(r"\d+\s+ambientes?", normalized):
                continue
            if re.fullmatch(r"\d+\s+dorm(?:itorios?)?", normalized):
                continue
            if re.fullmatch(r"\d+\s+bañ[oa]s?", normalized):
                continue
            if re.fullmatch(r"\d+\s*m²\s*(tot(?:ales?)?|cub(?:iertos?)?)?", normalized):
                continue
            normalized = re.sub(r"^ambientes?\s+", "", normalized).strip()
            if normalized in seen_normalized:
                continue
            seen_normalized.add(normalized)
            cleaned_features.append(value)
        return cleaned_features[:20]