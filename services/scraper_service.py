import os
import re
import json
import unicodedata
from html import unescape
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from firecrawl import Firecrawl


MAX_IMAGES = 60
MIN_PRIMARY_GALLERY_IMAGES = 6


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

        log("Procesando contenido estructurado desde Firecrawl...")
        extracted = self._extract_structured_data(markdown, html, raw_html, source_url, log)
        validation_error = self._validate_extracted_listing(
            portal=portal,
            source_url=source_url,
            markdown=markdown,
            html=raw_html or html,
            extracted=extracted,
            log=log,
        )
        if validation_error:
            raise RuntimeError(validation_error)

        image_urls_llm = extracted.pop("image_urls", []) or []
        caracteristicas_raw = extracted.pop("caracteristicas", [])
        image_urls = self._select_image_urls(
            portal=portal,
            markdown=markdown,
            html=raw_html or html,
            llm_urls=image_urls_llm,
            firecrawl_urls=firecrawl_images,
        )

        detalles = {
            "ambientes":        extracted.pop("ambientes", None),
            "banos":            extracted.pop("banos", None),
            "dormitorios":      extracted.pop("dormitorios", None),
            "metros_totales":   extracted.pop("metros_totales", None),
            "metros_cubiertos": extracted.pop("metros_cubiertos", None),
            "estado":           extracted.pop("estado", None),
            "disposicion":      extracted.pop("disposicion", None),
            "orientacion":      extracted.pop("orientacion", None),
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
    # Paso 1: Firecrawl → Markdown
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
                formats=["markdown", "html", "rawHtml", "images"],
                only_main_content=False,
                wait_for=1500,
                timeout=30000,
                location={"country": "AR", "languages": ["es-AR", "es"]},
                actions=self._actions_for_portal(portal),
            )
        except Exception as e:
            raise RuntimeError(f"Error llamando a Firecrawl: {e}") from e

        if isinstance(result, dict):
            markdown = (result.get("markdown") or "").strip()
            images = result.get("images") or []
            html = (result.get("html") or "").strip()
            raw_html = (result.get("rawHtml") or result.get("raw_html") or "").strip()
        else:
            markdown = (getattr(result, "markdown", "") or "").strip()
            images = getattr(result, "images", None) or []
            html = (getattr(result, "html", "") or "").strip()
            raw_html = (getattr(result, "rawHtml", None) or getattr(result, "raw_html", "") or "").strip()
        if not markdown:
            raise RuntimeError("Firecrawl devolvió Markdown vacío")

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
        if raw_html or html:
            log(f"HTML obtenido desde Firecrawl: {len(raw_html or html)} caracteres")
        return {
            "markdown": markdown,
            "images": image_urls,
            "html": html,
            "raw_html": raw_html,
        }

    # ──────────────────────────────────────────────
    # Paso 2: Markdown → dict estructurado
    # ──────────────────────────────────────────────

    def _extract_structured_data(
        self, markdown: str, html: str, raw_html: str, source_url: str, log: Callable[[str], None]
    ) -> dict[str, Any]:
        log("Usando extracción heurística mejorada desde Markdown de Firecrawl.")
        return self._build_fallback_from_content(markdown, raw_html or html, source_url)

    # ──────────────────────────────────────────────
    # Extracción heurística
    # ──────────────────────────────────────────────

    def _build_fallback_from_content(self, markdown: str, html: str, source_url: str) -> dict[str, Any]:
        focused_markdown = self._focus_listing_content(markdown)
        focused_html = self._focus_listing_content(html)
        source_text = self._merge_sources(focused_markdown, focused_html)
        listing_payload = self._extract_listing_payload_from_html(html, source_url)
        trusted_html = focused_html if listing_payload.get("_listing_id_found") else ""
        price_match = re.search(r"(?:USD|U\$S|AR\$|\$)\s*[\d.,]+", focused_markdown or markdown, re.I)
        titulo_html = listing_payload.get("titulo") or self._extract_title_from_html(trusted_html)
        precio_html = listing_payload.get("precio") or self._extract_price_from_html(trusted_html)
        descripcion = listing_payload.get("descripcion") or self._extract_description(source_text, focused_html) or self._best_text_block(source_text)
        caracteristicas = self._extract_features(source_text, focused_html)
        detalles = self._extract_detail_candidates(source_text)
        detalles_html = self._extract_detail_candidates_from_html(focused_html)
        detalles.update({k: v for k, v in detalles_html.items() if v})
        detalles.update({k: v for k, v in (listing_payload.get("detalles") or {}).items() if v})

        # FIX: ubicación — intentar primero desde HTML (dirección completa)
        ubicacion = (
            listing_payload.get("ubicacion")
            or self._extract_location_from_html(trusted_html)
            or self._extract_location(source_text)
            or "Ver en el portal"
        )

        return {
            "titulo":          titulo_html or self._extract_title(source_text) or "Propiedad en Venta",
            "precio":          precio_html or (price_match.group(0) if price_match else "Consultar precio"),
            "ubicacion":       ubicacion,
            "descripcion":     descripcion,
            "ambientes":       detalles.get("ambientes"),
            "banos":           detalles.get("banos"),
            "dormitorios":     detalles.get("dormitorios"),
            "metros_totales":  detalles.get("metros_totales"),
            "metros_cubiertos": detalles.get("metros_cubiertos"),
            "cocheras":        detalles.get("cocheras"),
            "antiguedad":      detalles.get("antiguedad"),
            "expensas":        detalles.get("expensas"),
            "estado":          detalles.get("estado"),
            "disposicion":     detalles.get("disposicion"),
            "orientacion":     detalles.get("orientacion"),
            "caracteristicas": self._merge_feature_lists(caracteristicas, self._details_to_features(detalles)),
            "image_urls":      (listing_payload.get("image_urls") or []) + self._extract_contextual_image_urls_from_html(focused_html),
            "_listing_id_found": bool(listing_payload.get("_listing_id_found")),
        }

    def _select_image_urls(
        self,
        *,
        portal: str,
        markdown: str,
        html: str,
        llm_urls: list[str],
        firecrawl_urls: list[str],
    ) -> list[str]:
        focused_markdown = self._focus_listing_content(markdown)
        focused_html = self._focus_listing_content(html)
        gallery_limit = self._extract_gallery_limit(focused_markdown, focused_html)

        primary_candidates: list[str] = []
        for group in (
            self._extract_contextual_image_urls_from_html(focused_html),
            self._extract_image_urls_from_html(focused_html),
            self._extract_image_urls_from_markdown(focused_markdown),
        ):
            self._append_unique_urls(primary_candidates, group)

        dominant_primary = self._keep_dominant_image_group(primary_candidates)
        if portal == "zonaprop" and len(dominant_primary) >= MIN_PRIMARY_GALLERY_IMAGES:
            primary_candidates = dominant_primary

        if portal == "zonaprop" and primary_candidates:
            merged: list[str] = []
            preferred_group = self._image_group_key(primary_candidates[0])
            self._append_unique_urls(merged, primary_candidates, preferred_group=preferred_group)
            for group in (
                self._filter_image_urls(llm_urls),
                self._filter_image_urls(firecrawl_urls),
                self._extract_contextual_image_urls_from_html(html),
            ):
                self._append_unique_urls(merged, group, preferred_group=preferred_group)
            return merged[: gallery_limit or MAX_IMAGES]

        merged: list[str] = []
        for group in (
            primary_candidates,
            self._filter_image_urls(llm_urls),
            self._filter_image_urls(firecrawl_urls),
            self._extract_contextual_image_urls_from_html(html),
            self._extract_image_urls_from_html(html),
            self._extract_image_urls_from_markdown(markdown),
        ):
            self._append_unique_urls(merged, group)
        return merged[: gallery_limit or MAX_IMAGES]

    @staticmethod
    def _append_unique_urls(target: list[str], urls: list[str], preferred_group: str | None = None) -> None:
        for url in urls:
            if preferred_group and ScraperService._image_group_key(url) != preferred_group:
                continue
            if url not in target:
                target.append(url)

    @staticmethod
    def _focus_listing_content(text: str) -> str:
        if not text:
            return ""
        boundaries = [
            r"Propiedades similares",
            r"Tambi[eé]n te puede interesar",
            r"Te puede interesar",
            r"Publicaciones del anunciante",
            r"Preguntas para la inmobiliaria",
            r"Denunciar aviso",
            r"Aviso legal",
        ]
        end_positions = [match.start() for pattern in boundaries for match in [re.search(pattern, text, re.I)] if match]
        if not end_positions:
            return text
        return text[: min(end_positions)]

    @staticmethod
    def _extract_gallery_limit(markdown: str, html: str) -> int | None:
        haystack = "\n".join(part for part in (markdown, ScraperService._html_to_text(html)) if part)
        patterns = [
            r"ver(?:\s+todas)?\s+las?\s+(\d{1,3})\s+fotos",
            r"galer[ií]a(?:\s+de)?\s+(\d{1,3})\s+fotos",
            r"(\d{1,3})\s+fotos?\s+y\s+\d+\s+videos?",
            r"(\d{1,3})\s+fotos?\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, haystack, re.I)
            if not match:
                continue
            count = int(match.group(1))
            if 4 <= count <= MAX_IMAGES:
                return count
        return None

    @staticmethod
    def _extract_contextual_image_urls_from_html(html: str) -> list[str]:
        if not html:
            return []

        candidates: list[str] = []
        patterns = (
            r"""https?://[^\s"'<>]+""",
            r"""https?:\\/\\/[^\s"'<>]+""",
        )
        required_tokens = (
            "image", "images", "photo", "photos", "gallery",
            "carousel", "cover", "slide", "multimedia",
        )
        blocked_tokens = (
            "logo", "favicon", "sprite", "placeholder",
            "floorplan", "planos", "plano", "mapa", "staticmap",
            "agent", "broker", "banner",
        )

        for pattern in patterns:
            for match in re.finditer(pattern, html, re.I):
                raw_url = match.group(0)
                url = raw_url.replace("\\/", "/")
                start = max(0, match.start() - 220)
                end = min(len(html), match.end() + 220)
                context = html[start:end].lower()
                if any(token in context for token in blocked_tokens):
                    continue
                if not any(token in context for token in required_tokens):
                    continue
                candidates.append(url)

        return ScraperService._filter_image_urls(candidates)

    @staticmethod
    def _keep_dominant_image_group(urls: list[str]) -> list[str]:
        if not urls:
            return []

        counts: dict[str, int] = {}
        for url in urls:
            key = ScraperService._image_group_key(url)
            counts[key] = counts.get(key, 0) + 1

        if not counts:
            return urls

        dominant_key = max(counts, key=counts.get)
        dominant = [url for url in urls if ScraperService._image_group_key(url) == dominant_key]
        return dominant or urls

    @staticmethod
    def _image_group_key(url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        parts = [part for part in (parsed.path or "").lower().split("/") if part]
        cleaned_parts: list[str] = []
        for part in parts[:-1]:
            if re.fullmatch(r"(?:fit-in|crop|thumb|thumbnail|small|medium|large)", part):
                continue
            if re.fullmatch(r"(?:w|h)?\d{2,4}x\d{2,4}", part):
                continue
            cleaned_parts.append(part)
        prefix = "/".join(cleaned_parts[:4])
        return f"{parsed.netloc.lower()}|{prefix}"

    @staticmethod
    def _decode_json_string(value: str) -> str:
        if not value:
            return ""
        try:
            return json.loads(f'"{value}"')
        except Exception:
            return unescape(value.replace("\\/", "/"))

    @staticmethod
    def _extract_listing_id_from_url(source_url: str) -> str:
        match = re.search(r"-(\d{6,})\.html", source_url or "", re.I)
        return match.group(1) if match else ""

    @staticmethod
    def _extract_listing_context(html: str, listing_id: str) -> str:
        listing_object = ScraperService._extract_listing_object(html, listing_id)
        if listing_object:
            try:
                return json.dumps(listing_object, ensure_ascii=False)
            except Exception:
                pass
        if not html or not listing_id:
            return ""

        positions = [match.start() for match in re.finditer(re.escape(listing_id), html)]
        if not positions:
            return ""

        best_window = html
        best_score = -1
        for position in positions:
            start = max(0, position - 30000)
            end = min(len(html), position + 30000)
            window = html[start:end]
            score = 0
            for token in (
                "titleLocation", "streetAddress", "price", "description",
                "m2", "cub", "tot", "bath", "room", "dorm", "image",
            ):
                score += window.lower().count(token.lower())
            if score > best_score:
                best_score = score
                best_window = window
        return best_window

    @staticmethod
    def _extract_listing_object(html: str, listing_id: str) -> dict[str, Any]:
        if not html or not listing_id:
            return {}

        script_matches = re.finditer(
            r'<script[^>]*>\s*(.*?)\s*</script>',
            html,
            re.I | re.S,
        )
        for match in script_matches:
            script_content = unescape(match.group(1) or "")
            if listing_id not in script_content:
                continue

            candidates: list[Any] = []
            stripped = script_content.strip().rstrip(";")
            for candidate in (stripped,):
                try:
                    candidates.append(json.loads(candidate))
                except Exception:
                    pass

            object_text = ScraperService._extract_json_object_containing(script_content, listing_id)
            if object_text:
                try:
                    candidates.append(json.loads(object_text))
                except Exception:
                    pass

            for candidate in candidates:
                listing_node = ScraperService._find_listing_node(candidate, listing_id)
                if listing_node:
                    return listing_node

        return {}

    @staticmethod
    def _extract_json_object_containing(text: str, needle: str) -> str:
        position = text.find(needle)
        if position == -1:
            return ""

        start = position
        depth = 0
        in_string = False
        escape = False
        while start >= 0:
            ch = text[start]
            if ch == '"' and not escape:
                in_string = not in_string
            if not in_string:
                if ch == '{':
                    depth -= 1
                    if depth <= 0:
                        break
                elif ch == '}':
                    depth += 1
            escape = (ch == '\\' and not escape)
            start -= 1

        if start < 0 or text[start] != '{':
            return ""

        end = start
        depth = 0
        in_string = False
        escape = False
        while end < len(text):
            ch = text[end]
            if ch == '"' and not escape:
                in_string = not in_string
            if not in_string:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        return text[start:end + 1]
            escape = (ch == '\\' and not escape)
            end += 1
        return ""

    @staticmethod
    def _find_listing_node(node: Any, listing_id: str) -> dict[str, Any]:
        listing_id = str(listing_id)
        if isinstance(node, dict):
            values = [str(value) for value in node.values() if isinstance(value, (str, int))]
            if listing_id in values or any(listing_id in value for value in values):
                return node
            for value in node.values():
                found = ScraperService._find_listing_node(value, listing_id)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = ScraperService._find_listing_node(item, listing_id)
                if found:
                    return found
        return {}

    @staticmethod
    def _extract_listing_payload_from_html(html: str, source_url: str) -> dict[str, Any]:
        listing_id = ScraperService._extract_listing_id_from_url(source_url)
        context = ScraperService._extract_listing_context(html, listing_id)
        payload: dict[str, Any] = {
            "detalles": {},
            "image_urls": [],
            "_listing_id": listing_id,
            "_listing_id_found": bool(context),
        }
        if not context:
            return payload

        def extract_string(*keys: str, min_len: int = 1, max_len: int = 4000) -> str:
            for key in keys:
                match = re.search(fr'"{re.escape(key)}"\s*:\s*"([^"]{{{min_len},{max_len}}})"', context, re.I)
                if match:
                    value = re.sub(r"\s+", " ", ScraperService._decode_json_string(match.group(1))).strip(" ,")
                    if value:
                        return value
            return ""

        payload["titulo"] = extract_string("title", "postingTitle", "seoTitle", "publicationTitle", min_len=8, max_len=220)
        payload["precio"] = extract_string("formattedPrice", "priceFormatted", min_len=4, max_len=80)
        payload["ubicacion"] = extract_string("titleLocation", "locationName", "postingLocation", min_len=8, max_len=220)
        payload["descripcion"] = extract_string("description", "descriptionText", min_len=40, max_len=6000)

        if not payload["ubicacion"]:
            street = extract_string("streetAddress", min_len=4, max_len=180)
            locality = extract_string("addressLocality", min_len=2, max_len=80)
            region = extract_string("addressRegion", min_len=2, max_len=80)
            parts = [part for part in (street, locality, region) if part]
            if parts:
                payload["ubicacion"] = ", ".join(dict.fromkeys(parts))

        if not payload["precio"]:
            amount_match = re.search(r'"(?:price|priceAmount)"\s*:\s*"?([\d.,]+)"?', context, re.I)
            currency_match = re.search(r'"(?:priceCurrency|currency)"\s*:\s*"?(USD|U\$S|ARS|AR\$|\$)"?', context, re.I)
            if amount_match:
                currency = (currency_match.group(1) if currency_match else "USD").upper().replace("U$S", "USD")
                prefix = "USD" if currency == "USD" else "$"
                payload["precio"] = f"{prefix} {amount_match.group(1)}"

        detail_patterns = {
            "metros_totales": [r'"(?:surfaceTotal|totalArea|coveredSurfaceTotal|areaTotal)"\s*:\s*"?(\d+(?:[.,]\d+)?)"?'],
            "metros_cubiertos": [r'"(?:surfaceCovered|coveredArea|area)"\s*:\s*"?(\d+(?:[.,]\d+)?)"?'],
            "ambientes": [r'"(?:rooms|ambiences|roomAmount)"\s*:\s*"?(\d+)"?'],
            "banos": [r'"(?:bathrooms|bathroomsAmount|bathRoomAmount)"\s*:\s*"?(\d+)"?'],
            "dormitorios": [r'"(?:bedrooms|bedroomsAmount|bedroomAmount)"\s*:\s*"?(\d+)"?'],
            "antiguedad": [r'"(?:age|antiquity|propertyAge)"\s*:\s*"?(.*?)"?(?:,|\})'],
            "disposicion": [r'"(?:disposition|layout)"\s*:\s*"([^"]{2,40})"'],
            "orientacion": [r'"(?:orientation)"\s*:\s*"([^"]{1,20})"'],
            "estado": [r'"(?:condition|state|propertyState)"\s*:\s*"([^"]{2,60})"'],
            "expensas": [r'"(?:expenses|expensas)"\s*:\s*"?(?:\$|AR\$|USD|U\$S)?\s*([\d.,]+)"?'],
        }
        for key, patterns in detail_patterns.items():
            for pattern in patterns:
                match = re.search(pattern, context, re.I)
                if match:
                    value = re.sub(r"\s+", " ", ScraperService._decode_json_string(match.group(1))).strip(" ,")
                    if value:
                        payload["detalles"][key] = value
                        break

        payload["image_urls"] = ScraperService._filter_image_urls(
            re.findall(r"""https?://[^\s"'<>]+""", context, re.I)
            + [
                ScraperService._decode_json_string(url)
                for url in re.findall(r"""https?:\\/\\/[^\s"'<>]+""", context, re.I)
            ]
        )
        return payload

    def _validate_extracted_listing(
        self,
        *,
        portal: str,
        source_url: str,
        markdown: str,
        html: str,
        extracted: dict[str, Any],
        log: Callable[[str], None],
    ) -> str:
        if portal != "zonaprop":
            return ""

        listing_id = self._extract_listing_id_from_url(source_url)
        listing_id_found = bool(extracted.get("_listing_id_found"))
        html_has_listing_id = bool(listing_id and listing_id in (html or ""))
        markdown_has_listing_id = bool(listing_id and listing_id in (markdown or ""))

        if listing_id and not html_has_listing_id and not markdown_has_listing_id:
            log(f"Advertencia: Firecrawl no devolvió el ID {listing_id} dentro del HTML/Markdown del aviso.")

        extracted_text = " ".join(
            str(extracted.get(key) or "")
            for key in ("titulo", "ubicacion", "descripcion", "precio")
        )
        url_tokens = self._source_url_tokens(source_url)
        matched_tokens = [token for token in url_tokens if token in self._normalize_text(extracted_text)]

        if matched_tokens:
            log(f"Validación URL-contenido OK: {', '.join(matched_tokens[:4])}")
            return ""

        if listing_id and listing_id_found:
            return ""

        if url_tokens:
            tokens_preview = ", ".join(url_tokens[:5])
            reason = (
                "Zonaprop/Firecrawl devolvió contenido que no coincide con la URL del aviso. "
                f"Tokens esperados según la URL: {tokens_preview}."
            )
        else:
            reason = "Zonaprop/Firecrawl devolvió contenido que no coincide con la URL del aviso."

        if listing_id and not html_has_listing_id and not markdown_has_listing_id:
            reason += f" El ID {listing_id} no apareció en el contenido devuelto."

        reason += " No se guardó la ficha para evitar mezclar otra propiedad."
        return reason

    @staticmethod
    def _source_url_tokens(source_url: str) -> list[str]:
        path = urllib.parse.urlsplit(source_url or "").path
        slug = urllib.parse.unquote(os.path.basename(path or ""))
        slug = re.sub(r"-(\d{6,})\.html$", "", slug, flags=re.I)
        normalized = ScraperService._normalize_text(slug)
        raw_tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
        anchor_tokens = {
            "departamento", "departamentos", "depto", "casa", "ph",
            "monoambiente", "ambiente", "ambientes", "local", "oficina",
            "terreno", "lote",
        }
        for index, token in enumerate(raw_tokens):
            if token in anchor_tokens:
                raw_tokens = raw_tokens[index + 1:]
                break
        stopwords = {
            "de", "del", "la", "las", "los", "al", "en", "y", "entre",
            "propiedad", "propiedades", "clasificado", "departamento", "departamentos",
            "depto", "casa", "ph", "monoambiente", "ambiente", "ambientes",
            "venta", "av", "avenida", "calle", "capital", "federal", "argentina",
            "zona", "zonaprop", "usd", "u", "s",
        }
        tokens: list[str] = []
        for token in raw_tokens:
            if token in stopwords:
                continue
            if token.isdigit():
                continue
            if len(token) < 4:
                continue
            if token not in tokens:
                tokens.append(token)
        return tokens

    @staticmethod
    def _normalize_text(value: str) -> str:
        value = (value or "").strip().lower()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        return re.sub(r"\s+", " ", value)

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
            "floorplan", "planos", "plano", "staticmap", "mapa",
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
            if re.search(r"^\[.+\]\(https?://", line):
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
                if re.search(r"Preguntas para la inmobiliaria|Seleccioná una o más preguntas", l, re.I):
                    break
                if not l:
                    if collected:
                        collected.append("")
                    continue
                if re.fullmatch(r"[A-ZÁÉÍÓÚÑ ]{4,}", l) and len(collected) > 3:
                    break
                if _is_noise(l):
                    continue
                if l.strip().lower() == "completa":
                    continue
                collected.append(l)
                if sum(len(x) for x in collected) > 2500:
                    break
            text_result = "\n".join(collected).strip()
            if text_result:
                return text_result

        candidate = ScraperService._best_text_block(text)
        return candidate.strip()

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
    def _extract_meta_content(html: str, *keys: str) -> str:
        if not html:
            return ""
        for key in keys:
            escaped = re.escape(key)
            patterns = [
                rf'<meta[^>]+(?:property|name)=["\']{escaped}["\'][^>]+content=["\']([^"\']+)["\']',
                rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{escaped}["\']',
            ]
            for pattern in patterns:
                match = re.search(pattern, html, re.I)
                if match:
                    return re.sub(r"\s+", " ", unescape(match.group(1))).strip()
        return ""

    @staticmethod
    def _extract_listing_json_ld(html: str) -> dict[str, Any]:
        if not html:
            return {}

        def _iter_items(node: Any) -> list[dict[str, Any]]:
            if isinstance(node, list):
                items: list[dict[str, Any]] = []
                for item in node:
                    items.extend(_iter_items(item))
                return items
            if isinstance(node, dict):
                graph = node.get("@graph")
                if isinstance(graph, list):
                    items = [node]
                    for item in graph:
                        items.extend(_iter_items(item))
                    return items
                return [node]
            return []

        for match in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S):
            raw = unescape(match.group(1) or "").strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            for item in _iter_items(payload):
                if not isinstance(item, dict):
                    continue
                if item.get("offers") or item.get("address") or item.get("image"):
                    return item
        return {}

    @staticmethod
    def _extract_title_from_html(html: str) -> str:
        title = ScraperService._extract_meta_content(html, "og:title", "twitter:title")
        if not title:
            json_ld = ScraperService._extract_listing_json_ld(html)
            title = (json_ld.get("name") or "").strip() if isinstance(json_ld, dict) else ""
        if not title:
            match = re.search(r"<title>\s*(.*?)\s*</title>", html, re.I | re.S)
            if match:
                title = re.sub(r"\s+", " ", unescape(match.group(1))).strip()
        if not title:
            return ""
        title = re.sub(r"\s+\|\s+Zonaprop.*$", "", title, flags=re.I)
        return ScraperService._clean_title(title)

    @staticmethod
    def _extract_price_from_html(html: str) -> str:
        meta_amount = ScraperService._extract_meta_content(html, "product:price:amount")
        meta_currency = ScraperService._extract_meta_content(html, "product:price:currency")
        if meta_amount:
            amount = re.sub(r"[^\d.,]", "", meta_amount)
            currency = (meta_currency or "USD").upper()
            prefix = "USD" if currency in {"USD", "U$S"} else "$"
            return f"{prefix} {amount}"

        json_ld = ScraperService._extract_listing_json_ld(html)
        offers = json_ld.get("offers") if isinstance(json_ld, dict) else None
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if isinstance(offers, dict):
            amount = re.sub(r"[^\d.,]", "", str(offers.get("price") or ""))
            currency = str(offers.get("priceCurrency") or "USD").upper()
            if amount:
                prefix = "USD" if currency in {"USD", "U$S"} else "$"
                return f"{prefix} {amount}"

        match = re.search(r'(?:"price"|priceAmount)\s*[:=]\s*"?(USD|U\$S|AR\$|\$)?\s*([\d.,]+)"?', html, re.I)
        if match:
            prefix = (match.group(1) or "USD").upper().replace("U$S", "USD")
            prefix = "USD" if prefix == "USD" else "$"
            return f"{prefix} {match.group(2)}"
        return ""

    @staticmethod
    def _extract_image_urls_from_html(html: str) -> list[str]:
        if not html:
            return []
        urls = re.findall(r"""https?://[^\s"'<>]+""", html, re.I)
        urls.extend(
            ScraperService._decode_json_string(url)
            for url in re.findall(r"""https?:\\/\\/[^\s"'<>]+""", html, re.I)
        )
        return ScraperService._filter_image_urls(urls)

    # FIX: extrae la dirección completa desde el HTML
    @staticmethod
    def _extract_location_from_html(html: str) -> str:
        if not html:
            return ""
        meta_location = ScraperService._extract_meta_content(html, "og:street-address", "street-address")
        if meta_location and len(meta_location) >= 10:
            return meta_location

        json_ld = ScraperService._extract_listing_json_ld(html)
        if isinstance(json_ld, dict):
            address = json_ld.get("address")
            if isinstance(address, dict):
                street = re.sub(r"\s+", " ", str(address.get("streetAddress") or "")).strip(" ,")
                locality_parts = [
                    re.sub(r"\s+", " ", str(address.get(key) or "")).strip(" ,")
                    for key in ("addressLocality", "addressRegion")
                ]
                locality_parts = [part for part in locality_parts if part and part.lower() not in street.lower()]
                value = ", ".join([street] + locality_parts).strip(" ,")
                if len(value) >= 10:
                    return value
        html = ScraperService._focus_listing_content(html)
        # ZonaProp: <h2 class="title-location">Av. Independencia 1977...</h2>
        m = re.search(r'<h2[^>]*class="[^"]*title-location[^"]*"[^>]*>\s*([^<]{10,200})\s*</h2>', html, re.I)
        if m:
            return re.sub(r"\s+", " ", unescape(m.group(1))).strip()
        # ZonaProp: dirección en un span/div con class que contiene "location" o "address"
        m = re.search(r'<(?:span|div|p)[^>]*class="[^"]*(?:location|address|ubicacion)[^"]*"[^>]*>\s*([^<]{10,200})\s*</(?:span|div|p)>', html, re.I)
        if m:
            return re.sub(r"\s+", " ", unescape(m.group(1))).strip()
        # Argenprop / MercadoLibre: data-testid o class con "address"
        m = re.search(r'data-testid="[^"]*address[^"]*"[^>]*>\s*([^<]{10,200})\s*<', html, re.I)
        if m:
            return re.sub(r"\s+", " ", unescape(m.group(1))).strip()
        # Fallback: buscar patrón de dirección argentina en el texto del HTML
        full_location_patterns = [
            r'"titleLocation"\s*:\s*"([^"]{10,220})"',
            r'"locationName"\s*:\s*"([^"]{10,220})"',
            r'"postingLocation"\s*:\s*"([^"]{10,220})"',
            r'"location"\s*:\s*"([^"]{10,220}(?:Capital Federal|Buenos Aires|CABA)[^"]*)"',
        ]
        for pattern in full_location_patterns:
            match = re.search(pattern, html, re.I)
            if not match:
                continue
            value = re.sub(r"\s+", " ", ScraperService._decode_json_string(match.group(1))).strip(" ,")
            if len(value) >= 15:
                return value

        street_match = re.search(r'"streetAddress"\s*:\s*"([^"]{6,180})"', html, re.I)
        if street_match:
            street = re.sub(r"\s+", " ", ScraperService._decode_json_string(street_match.group(1))).strip(" ,")
            locality_parts: list[str] = []
            for key in ("addressLocality", "addressRegion"):
                match = re.search(fr'"{key}"\s*:\s*"([^"]{{2,80}})"', html, re.I)
                if not match:
                    continue
                part = re.sub(r"\s+", " ", ScraperService._decode_json_string(match.group(1))).strip(" ,")
                if part and part.lower() not in street.lower():
                    locality_parts.append(part)
            value = ", ".join([street] + locality_parts)
            if len(value) >= 15:
                return value

        text = ScraperService._html_to_text(html)
        m = re.search(
            r'((?:Av(?:enida)?|Calle|Bv|Blvd|Ruta|Pasaje|Pje)\.?\s+[A-ZÁÉÍÓÚÑ][^\n]{5,100}'
            r'(?:,\s*(?:entre|esq|y)\s+[^\n]{5,60})?'
            r'(?:,\s*[A-ZÁÉÍÓÚÑ][a-záéíóúñ ]{3,40})*)',
            text, re.I
        )
        if m:
            value = re.sub(r"\s+", " ", m.group(1)).strip(" ,")
            if len(value) >= 15:
                return value
        return ""

    @staticmethod
    def _extract_description_from_html(html: str) -> str:
        text = ScraperService._html_to_text(html)
        if not text:
            return ""
        patterns = [
            r"Descripci[oó]n\s*(?:completa\s*)?(.+?)(?:Preguntas para la inmobiliaria|Conocé más sobre|Leer menos|Características|Servicios|Ubicación|Mapa|Propiedades similares)",
            r"(Venta de .*?(?:Capital Federal|Buenos Aires)\..+?)(?:LEPORE|AVISO LEGAL|XINTEL|Leer menos|Preguntas para la inmobiliaria)",
            r"(Departamento .*?(?:Capital Federal|Buenos Aires)\..+?)(?:LEPORE|AVISO LEGAL|XINTEL|Leer menos|Preguntas para la inmobiliaria)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.I | re.S)
            if match:
                candidate = re.sub(r"\s+\n", "\n", match.group(1)).strip()
                candidate = ScraperService._clean_description(candidate)
                if len(candidate) >= 80:
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
        text = re.sub(r"\bEsta unidad es apta para personas.*$", "", text, flags=re.I | re.S)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip(" .\n")

    @staticmethod
    def _extract_features_from_html(html: str) -> list[str]:
        if not html:
            return []

        # FIX: extraer específicamente los <li class="icon-feature"> de ZonaProp
        # Ejemplo: <li class="icon-feature"><span>104 m² tot.</span></li>
        icon_features: list[str] = []
        li_matches = re.findall(
            r'<li[^>]*class="[^"]*icon-feature[^"]*"[^>]*>(.*?)</li>',
            html, re.I | re.S
        )
        for li_html in li_matches:
            # quitar tags internos y dejar solo el texto
            text = re.sub(r"<[^>]+>", " ", li_html)
            text = unescape(text)
            text = re.sub(r"\s+", " ", text).strip()
            if text and len(text) >= 2 and len(text) <= 80:
                icon_features.append(text)

        if icon_features:
            return icon_features

        # Fallback: extracción por palabras clave del texto plano
        text = ScraperService._html_to_text(html)
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
        # limpiar links markdown antes de buscar
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
        # FIX: filtrar links markdown [texto](url)
        if re.search(r"\[[^\]]+\]\(https?://", line):
            return True
        if re.fullmatch(r"[#>*\-\s\d|.:/]+", line):
            return True
        if not re.search(r"[A-Za-zÁÉÍÓÚáéíóúñ]", line):
            return True
        return False

    @staticmethod
    def _extract_detail_candidates(markdown: str) -> dict[str, str | None]:
        # limpiar links markdown antes de aplicar regex
        clean_md = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", markdown)
        patterns = {
            "ambientes": [
                r"(\d+)\s+ambientes?",
                r"Ambientes?\s*:?\s*(\d+)",
            ],
            "dormitorios": [
                r"(\d+)\s+dorm(?:itorios?|\.?)",
                r"Dormitorios?\s*:?\s*(\d+)",
            ],
            "banos": [
                r"(\d+)\s+bañ[oa]s?",
                r"Baños?\s*:?\s*(\d+)",
            ],
            "metros_totales": [
                r"(\d+(?:[.,]\d+)?)\s*m[²2](?:\s*tot\.?|\s*totales?)",
                r"Sup(?:erficie)?\s*total\s*:?\s*(\d+(?:[.,]\d+)?)\s*m[²2]",
            ],
            "metros_cubiertos": [
                r"(\d+(?:[.,]\d+)?)\s*m[²2](?:\s*cub\.?|\s*cubiertos?)",
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
            "disposicion": [
                r"\b(Frente|Contrafrente|Interno|Lateral)\b",
            ],
            "orientacion": [
                r"\b(Norte|Sur|Este|Oeste|NE|NO|SE|SO)\b",
            ],
            "estado": [
                r"\b(A estrenar|Excelente estado|Muy buen estado|Buen estado|En construcción)\b",
            ],
        }
        out: dict[str, str | None] = {key: None for key in patterns}
        for key, regexes in patterns.items():
            for regex in regexes:
                match = re.search(regex, clean_md, re.I)
                if match:
                    out[key] = re.sub(r"\s+", " ", match.group(1)).strip(" -|")
                    break
        split_values = ScraperService._extract_split_detail_candidates(clean_md)
        for key, value in split_values.items():
            if value and not out.get(key):
                out[key] = value
        return out

    @staticmethod
    def _extract_split_detail_candidates(text: str) -> dict[str, str | None]:
        values: dict[str, str | None] = {
            "metros_totales": None,
            "metros_cubiertos": None,
            "ambientes": None,
            "banos": None,
            "dormitorios": None,
        }
        lines = [re.sub(r"\s+", " ", line).strip(" -|") for line in text.splitlines()]
        for index, line in enumerate(lines):
            number_match = re.fullmatch(r"(\d+(?:[.,]\d+)?)", line)
            if not number_match:
                continue
            label_candidates = []
            if index + 1 < len(lines):
                label_candidates.append(lines[index + 1].lower())
            if index + 2 < len(lines):
                label_candidates.append(f"{lines[index + 1]} {lines[index + 2]}".lower())

            for label in label_candidates:
                normalized_label = re.sub(r"\s+", " ", label).strip(" .").lower()
                normalized_label = normalized_label.replace("\u00c2", "").replace("\u00b2", "2")
                normalized_label = normalized_label.replace("\u00c3\u00b1", "n").replace("\u00f1", "n")
                if not values["metros_totales"] and normalized_label.startswith("m2") and "tot" in normalized_label:
                    values["metros_totales"] = number_match.group(1)
                if not values["metros_cubiertos"] and normalized_label.startswith("m2") and "cub" in normalized_label:
                    values["metros_cubiertos"] = number_match.group(1)
                if not values["ambientes"] and normalized_label.startswith("amb"):
                    values["ambientes"] = number_match.group(1)
                if not values["banos"] and normalized_label.startswith("ban"):
                    values["banos"] = number_match.group(1)
                if not values["dormitorios"] and normalized_label.startswith("dorm"):
                    values["dormitorios"] = number_match.group(1)
        return values

    @staticmethod
    def _infer_feature_lines(markdown: str) -> list[str]:
        clean_md = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", markdown)
        candidates: list[str] = []
        for line in [l.strip(" -*#\t") for l in clean_md.splitlines()]:
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
        # FIX: primero intentar parsear <li class="icon-feature"> de ZonaProp
        # que contienen texto como "104 m² tot.", "73 m² cub.", "4 amb.", "2 baños", etc.
        values: dict[str, str | None] = {
            "metros_totales": None, "metros_cubiertos": None,
            "ambientes": None, "banos": None, "dormitorios": None,
            "estado": None, "disposicion": None, "orientacion": None,
        }

        if html:
            li_matches = re.findall(
                r'<li[^>]*class="[^"]*icon-feature[^"]*"[^>]*>(.*?)</li>',
                html, re.I | re.S
            )
            for li_html in li_matches:
                t = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", li_html))).strip()
                if not t:
                    continue
                if not values["metros_totales"]:
                    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m[²2]\s*tot", t, re.I)
                    if m: values["metros_totales"] = m.group(1)
                if not values["metros_cubiertos"]:
                    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m[²2]\s*cub", t, re.I)
                    if m: values["metros_cubiertos"] = m.group(1)
                if not values["ambientes"]:
                    m = re.search(r"(\d+)\s*amb", t, re.I)
                    if m: values["ambientes"] = m.group(1)
                if not values["banos"]:
                    m = re.search(r"(\d+)\s*bañ[oa]s?", t, re.I)
                    if m: values["banos"] = m.group(1)
                if not values["dormitorios"]:
                    m = re.search(r"(\d+)\s*dorm", t, re.I)
                    if m: values["dormitorios"] = m.group(1)
                if not values["estado"]:
                    m = re.search(r"\b(A estrenar|Excelente estado|Muy buen estado|Buen estado|En construcción)\b", t, re.I)
                    if m: values["estado"] = m.group(1)
                if not values["disposicion"]:
                    m = re.search(r"\b(Frente|Contrafrente|Interno|Lateral)\b", t, re.I)
                    if m: values["disposicion"] = m.group(1)
                if not values["orientacion"]:
                    m = re.search(r"\b(Norte|Sur|Este|Oeste|NE|NO|SE|SO|^[NSEO]$)\b", t, re.I)
                    if m: values["orientacion"] = m.group(1)

            split_values = ScraperService._extract_split_detail_candidates(ScraperService._html_to_text(html))
            for key, value in split_values.items():
                if value and not values.get(key):
                    values[key] = value

            # Si encontramos algo con icon-feature, devolver ya
            if any(v for v in values.values()):
                return values

        # Fallback: texto plano del HTML
        text = ScraperService._html_to_text(html)
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
        for line in lines:
            if not line:
                continue
            if not values["metros_totales"]:
                m = re.search(r"(\d+)\s*m[²2]\s*tot\.?", line, re.I)
                if m: values["metros_totales"] = m.group(1)
            if not values["metros_cubiertos"]:
                m = re.search(r"(\d+)\s*m[²2]\s*cub\.?", line, re.I)
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
                m = re.search(r"\b(Norte|Sur|Este|Oeste|NE|NO|SE|SO)\b", line, re.I)
                if m: values["orientacion"] = m.group(1)
        split_values = ScraperService._extract_split_detail_candidates(text)
        for key, value in split_values.items():
            if value and not values.get(key):
                values[key] = value
        return values

    @staticmethod
    def _details_to_features(detalles: dict[str, str | None]) -> list[str]:
        features: list[str] = []
        mappings = [
            ("metros_totales", "m² totales"),
            ("metros_cubiertos", "m² cubiertos"),
            ("ambientes", "ambientes"),
            ("banos", "baños"),
            ("dormitorios", "dormitorios"),
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
            if not value:
                continue
            normalized = value.lower()
            if len(value) < 2:
                continue
            if normalized in {
                "amb", "amb.", "ambientes", "ambiente",
                "dorm", "dorm.", "dormitorios",
                "baños", "banos", "m² tot", "m² cub", "m2 tot", "m2 cub",
                "departamentos", "propiedades",
            }:
                continue
            if re.search(r"\bdepartamentos?\s+en\s+venta\b", normalized):
                continue
            if re.search(r"\b(?:san\s+crist[oó]bal|capital\s+federal|ver\s+datos)\b", normalized):
                continue
            if re.search(r"\b(publicado|actualizado|favorito|compartir|notas personales)\b", normalized):
                continue
            if re.fullmatch(r"\d+(?:[.,]\d+)?", normalized):
                continue
            if re.search(r"\bdepartamento\b", normalized) and re.search(r"\bamb", normalized):
                continue
            if re.search(r"\b(?:av|avenida|calle|pasaje|pje|ruta|boulevard|blvd|bv)\b", normalized) and re.search(r"\d{3,5}", normalized):
                continue
            if re.fullmatch(r"\d+\s+bañ[oa]s?", normalized):
                continue
            if re.fullmatch(r"\d+\s*m[²2]\s*(tot(?:ales?)?|cub(?:iertos?)?)?", normalized):
                continue
            normalized = re.sub(r"^ambientes?\s+", "", normalized).strip()
            if normalized in seen_normalized:
                continue
            seen_normalized.add(normalized)
            cleaned_features.append(value)

        return cleaned_features[:20]
