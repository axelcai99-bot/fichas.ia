import urllib.parse
import re
import time
from typing import Callable, Any


class ScraperService:
    def scrape_property(self, source_url: str, log: Callable[[str], None]) -> dict[str, Any]:
        portal = self._detect_portal(source_url)
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                return self._scrape_once(source_url, portal, log)
            except Exception as e:
                if portal == "zonaprop" and self._is_antibot_error(e):
                    log("ZonaProp bloqueo el acceso con anti-bot. Se generara una ficha basica.")
                    return self._build_fallback_scraped(source_url, portal, str(e))
                if self._is_window_closed_error(e) and attempt < max_attempts:
                    log("El navegador se cerro inesperadamente. Reintentando...")
                    time.sleep(1.5)
                    continue
                raise

    def _scrape_once(self, source_url: str, portal: str, log: Callable[[str], None]) -> dict[str, Any]:
        from seleniumbase import SB
        log("Iniciando robot...")
        try:
            sb_ctx = SB(uc=True, headless=True)
            sb = sb_ctx.__enter__()
        except Exception as e:
            raise RuntimeError(f"Error iniciando navegador: {e}") from e

        try:
            log("Abriendo pagina...")
            sb.uc_open_with_reconnect(source_url, reconnect_time=8)

            title = sb.get_title()
            if "just a moment" in title.lower():
                log("Resolviendo proteccion anti-bot...")
                try:
                    sb.uc_gui_click_captcha()
                    time.sleep(5)
                except Exception:
                    pass
                if "just a moment" in sb.get_title().lower():
                    raise RuntimeError("No se pudo resolver proteccion anti-bot")

            page_title = sb.get_title()
            body_len = len(sb.get_page_source())
            img_count = sb.execute_script("return document.querySelectorAll('img').length")
            log(f"Pagina: {page_title[:60]} ({body_len} chars, {img_count} imgs)")

            self._ensure_browser_ready(sb)
            self._warm_page(sb)
            if portal == "zonaprop":
                data = self._extract_zonaprop(sb)
            elif portal == "argenprop":
                data = self._extract_argenprop(sb)
            elif portal == "mercadolibre":
                data = self._extract_mercadolibre(sb)
            else:
                data = self._extract_generic(sb)

            log(f"Extraido: {(data.get('titulo') or '')[:50]} | {data.get('precio') or ''}")
            data["source_portal"] = portal
            return data
        finally:
            try:
                sb_ctx.__exit__(None, None, None)
            except Exception:
                pass

    def _ensure_browser_ready(self, sb) -> None:
        try:
            handles = getattr(sb.driver, "window_handles", [])
        except Exception as e:
            raise RuntimeError(f"Navegador no disponible: {e}") from e
        if not handles:
            raise RuntimeError("Navegador sin ventanas activas")

    @staticmethod
    def _is_window_closed_error(err: Exception) -> bool:
        text = str(err).lower()
        patterns = [
            "no such window",
            "target window already closed",
            "web view not found",
            "disconnected: not connected to devtools",
            "chrome not reachable",
        ]
        return any(p in text for p in patterns)

    @staticmethod
    def _is_antibot_error(err: Exception) -> bool:
        text = str(err).lower()
        patterns = [
            "proteccion anti-bot",
            "just a moment",
            "security verification",
            "captcha",
        ]
        return any(p in text for p in patterns)

    def _build_fallback_scraped(self, source_url: str, portal: str, reason: str) -> dict[str, Any]:
        title = self._guess_title_from_url(source_url) or "Propiedad en Venta"
        descripcion = (
            "No se pudo extraer automaticamente por proteccion anti-bot del portal. "
            "Revisa el aviso original para completar los datos faltantes."
        )
        return {
            "titulo": title,
            "precio": "Consultar precio",
            "ubicacion": "Ver en el portal",
            "descripcion": descripcion,
            "detalles": {},
            "caracteristicas": [],
            "info_adicional": {"aviso": f"fallback_antibot:{reason[:120]}"},
            "image_urls": [],
            "source_portal": portal,
        }

    @staticmethod
    def _guess_title_from_url(source_url: str) -> str:
        try:
            path = urllib.parse.urlparse(source_url).path or ""
            slug = urllib.parse.unquote(path).rstrip("/").rsplit("/", 1)[-1]
        except Exception:
            return ""
        slug = re.sub(r"\.(?:html?|php)$", "", slug, flags=re.I)
        slug = re.sub(r"-\d{5,}$", "", slug)
        slug = re.sub(r"[_-]+", " ", slug)
        slug = re.sub(r"\s+", " ", slug).strip()
        return slug[:100].capitalize() if slug else ""

    def _warm_page(self, sb) -> None:
        sb.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.3)")
        time.sleep(1.5)
        sb.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.6)")
        time.sleep(1.5)
        sb.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1.5)
        sb.execute_script("window.scrollTo(0, 0)")
        time.sleep(1.5)

        for sel in ['[data-qa="POSTING_GALLERY"]', '[data-qa*="gallery"]', '[data-qa*="GALLERY"]']:
            try:
                if sb.is_element_visible(sel):
                    sb.click(sel)
                    time.sleep(1)
                    break
            except Exception:
                continue

    def _detect_portal(self, source_url: str) -> str:
        host = urllib.parse.urlparse(source_url).netloc.lower()
        if "zonaprop" in host:
            return "zonaprop"
        if "argenprop" in host:
            return "argenprop"
        if "mercadolibre" in host:
            return "mercadolibre"
        return "unknown"

    def _extract_zonaprop(self, sb) -> dict[str, Any]:
        titulo = self._pick_text(sb, ['h1[data-qa="POSTING_TITLE"]', '[data-qa="POSTING_TITLE"]', "h1"])
        precio = self._pick_price(sb, ['[data-qa="POSTING_PRICE"]', '[data-qa="POSTING_CARD_PRICE"]', '[class*="price"]'])
        ubicacion = self._pick_text(sb, ['[data-qa="POSTING_LOCATION"]', '[data-qa="POSTING_CARD_LOCATION"]', "address"])
        descripcion = self._pick_text(sb, ['[data-qa="POSTING_DESCRIPTION"]', "#posting-description", '[class*="description"]'])
        feature_lines = self._collect_lines(sb, ['[data-qa="POSTING_MAIN_FEATURES"]', '[class*="main-features"]'])
        caracteristicas = self._normalize_features(feature_lines)
        detalles = self._extract_details_from_features(caracteristicas)
        return {
            "titulo": titulo or "Propiedad en Venta",
            "precio": precio or "Consultar precio",
            "ubicacion": ubicacion or "Ver en el portal",
            "descripcion": descripcion or "Sin descripcion",
            "detalles": detalles,
            "caracteristicas": caracteristicas,
            "info_adicional": self._extract_extra_info(sb),
            "image_urls": self._extract_images(sb, "zonaprop")[:20],
        }

    def _extract_argenprop(self, sb) -> dict[str, Any]:
        titulo = self._pick_text(sb, ['h1[data-testid*="title"]', 'h1[class*="title"]', "h1"])
        precio = self._pick_price(sb, ['[data-testid*="price"]', '[class*="price"]'])
        ubicacion = self._pick_text(sb, ['[data-testid*="location"]', '[class*="location"]', "address"])
        descripcion = self._pick_text(sb, ['[data-testid*="description"]', '[class*="description"]'])
        feature_lines = self._collect_lines(sb, ['[class*="feature"]', '[class*="caracter"]', '[class*="main"]'])
        caracteristicas = self._normalize_features(feature_lines)
        detalles = self._extract_details_from_features(caracteristicas)
        return {
            "titulo": titulo or "Propiedad en Venta",
            "precio": precio or "Consultar precio",
            "ubicacion": ubicacion or "Ver en el portal",
            "descripcion": descripcion or "Sin descripcion",
            "detalles": detalles,
            "caracteristicas": caracteristicas,
            "info_adicional": self._extract_extra_info(sb),
            "image_urls": self._extract_images(sb, "argenprop")[:20],
        }

    def _extract_mercadolibre(self, sb) -> dict[str, Any]:
        titulo = self._pick_text(sb, ["h1.ui-pdp-title", "h1"])
        precio = self._pick_price(
            sb,
            [
                '[data-testid="price-part"]',
                ".andes-money-amount__fraction",
                '[class*="price"]',
            ],
        )
        ubicacion = self._pick_text(
            sb,
            ['[data-testid*="location"]', '[class*="ui-pdp-media__title"] a', '[class*="location"]', "address"],
        )
        ubicacion = self._clean_mercadolibre_text(ubicacion)
        descripcion = self._pick_text(sb, ['[data-testid="description-content"]', '[class*="description"]'])
        descripcion = self._clean_mercadolibre_text(descripcion)
        feature_lines = self._collect_lines(
            sb,
            ['[class*="highlighted-specs"]', '[class*="specs"]', '[class*="attributes"]'],
        )
        caracteristicas = self._normalize_features(feature_lines)
        detalles = self._extract_details_from_features(caracteristicas)
        return {
            "titulo": titulo or "Propiedad en Venta",
            "precio": precio or "Consultar precio",
            "ubicacion": ubicacion or titulo or "Ver en el portal",
            "descripcion": descripcion or "Sin descripcion",
            "detalles": detalles,
            "caracteristicas": caracteristicas,
            "info_adicional": self._extract_extra_info(sb),
            "image_urls": self._extract_images(sb, "mercadolibre")[:20],
        }

    def _extract_generic(self, sb) -> dict[str, Any]:
        titulo = self._pick_text(sb, ["h1", '[class*="title"]'])
        precio = self._pick_price(sb, ['[class*="price"]'])
        ubicacion = self._pick_text(sb, ["address", '[class*="location"]'])
        descripcion = self._pick_text(sb, ['[class*="description"]'])
        caracteristicas = self._normalize_features(self._collect_lines(sb, ['[class*="feature"]']))
        return {
            "titulo": titulo or "Propiedad en Venta",
            "precio": precio or "Consultar precio",
            "ubicacion": ubicacion or "Ver en el portal",
            "descripcion": descripcion or "Sin descripcion",
            "detalles": self._extract_details_from_features(caracteristicas),
            "caracteristicas": caracteristicas,
            "info_adicional": self._extract_extra_info(sb),
            "image_urls": self._extract_images(sb, "unknown")[:20],
        }

    def _pick_text(self, sb, selectors: list[str]) -> str:
        script = """
        const selectors = arguments[0] || [];
        const clean = (s) => (s || '').replace(/\\s+/g,' ').trim();
        for (const sel of selectors) {
          try {
            const nodes = document.querySelectorAll(sel);
            for (const node of nodes) {
              const t = clean(node && node.innerText);
              if (t && t.length > 2) return t;
            }
          } catch (e) {}
        }
        return '';
        """
        return (sb.execute_script(script, selectors) or "").strip()

    def _pick_price(self, sb, selectors: list[str]) -> str:
        script = """
        const selectors = arguments[0] || [];
        const clean = (s) => (s || '').replace(/\\s+/g,' ').trim();
        const isPrice = (t) => /(USD|U\\$S|AR\\$|\\$)\\s*[\\d.]+(?:,[\\d]+)?/i.test(t) || /^\\d{2,3}(?:\\.\\d{3})+$/i.test(t);
        for (const sel of selectors) {
          try {
            const nodes = document.querySelectorAll(sel);
            for (const node of nodes) {
              const t = clean(node && node.innerText);
              if (t && isPrice(t) && !/expensas?/i.test(t)) return t;
            }
          } catch (e) {}
        }
        const txt = document.body && document.body.innerText ? document.body.innerText : '';
        const m = txt.match(/(?:Venta|Alquiler)?\\s*(?:USD|U\\$S|AR\\$|\\$)\\s*[\\d.]+(?:,[\\d]+)?/i);
        return m ? clean(m[0]) : '';
        """
        raw = (sb.execute_script(script, selectors) or "").strip()
        raw = re.sub(r"\s*(Avisarme|Avisar|si baja|de precio).*$", "", raw, flags=re.I).strip()
        m = re.search(r"(?:Venta|Alquiler)?\s*(USD|U\$S|AR\$|\$)\s*[\d.,]+", raw, re.I)
        return re.sub(r"\s+", " ", m.group(0)).strip() if m else raw

    def _collect_lines(self, sb, selectors: list[str]) -> list[str]:
        script = """
        const selectors = arguments[0] || [];
        const clean = (s) => (s || '').replace(/\\s+/g,' ').trim();
        const out = [];
        const seen = new Set();
        for (const sel of selectors) {
          try {
            const roots = document.querySelectorAll(sel);
            for (const root of roots) {
              const lines = (root && root.innerText ? root.innerText.split('\\n') : []);
              for (const line of lines) {
                const t = clean(line);
                const key = t.toLowerCase();
                if (!t || seen.has(key)) continue;
                seen.add(key);
                out.push(t);
              }
            }
          } catch (e) {}
        }
        return out.slice(0, 120);
        """
        lines = sb.execute_script(script, selectors) or []
        return [str(x).strip() for x in lines if str(x).strip()]

    def _extract_images(self, sb, portal: str) -> list[str]:
        if portal == "argenprop":
            fotos_raw = sb.execute_script(
                """
                var imgs = new Set();
                var sels = [
                  '[class*="carousel"] img',
                  '[class*="gallery"] img',
                  '[class*="fotorama"] img',
                  '[class*="slick"] img'
                ];
                sels.forEach(function(sel){
                  try{
                    document.querySelectorAll(sel).forEach(function(img){
                      ['currentSrc','src','data-src','data-lazy-src','data-original'].forEach(function(a){
                        var u = img.getAttribute(a);
                        if(u && u.includes('http')) imgs.add(u);
                      });
                    });
                  }catch(e){}
                });
                if (imgs.size < 2) {
                  var og = document.querySelector('meta[property="og:image"]');
                  if (og && og.content) imgs.add(og.content);
                }
                return Array.from(imgs);
                """
            ) or []
        elif portal == "mercadolibre":
            fotos_raw = sb.execute_script(
                """
                var imgs = new Set();
                var sels = [
                  '[class*="ui-pdp-gallery"] img',
                  '[class*="ui-pdp-thumbnail"] img',
                  '[data-testid*="gallery"] img',
                  'img[data-zoom]'
                ];
                sels.forEach(function(sel){
                  try{
                    document.querySelectorAll(sel).forEach(function(img){
                      var w = img.naturalWidth || img.width || 0;
                      var h = img.naturalHeight || img.height || 0;
                      if (w > 0 && h > 0 && (w < 520 || h < 340)) return;
                      var u = img.getAttribute('data-zoom') || img.currentSrc || img.getAttribute('src') || img.getAttribute('data-src');
                      if(!u || !u.includes('http')) return;
                      var lu = u.toLowerCase();
                      if (lu.includes('banner') || lu.includes('logo') || lu.includes('ads')) return;
                      if (lu.includes('/d_q_np_2x_') || lu.includes('/d_q_np_') || lu.includes('thumbnail')) return;
                      imgs.add(u);
                    });
                  }catch(e){}
                });
                if (imgs.size < 2) {
                  var og = document.querySelector('meta[property="og:image"]');
                  if (og && og.content) imgs.add(og.content);
                }
                return Array.from(imgs);
                """
            ) or []
        else:
            fotos_raw = sb.execute_script(
            """
            var imgs = new Set();
            document.querySelectorAll('img').forEach(function(img) {
                ['src','data-src','data-lazy-src','data-original'].forEach(function(a) { var u=img.getAttribute(a); if(u&&u.includes('http')&&u.length>30&&!u.includes('logo')&&!u.includes('icon')&&!u.includes('svg')) imgs.add(u); });
                var ss=img.getAttribute('srcset'); if(ss) ss.split(',').forEach(function(s) { var u=s.trim().split(' ')[0]; if(u&&u.includes('http')) imgs.add(u); });
            });
            document.querySelectorAll('[style*="background-image"]').forEach(function(d) { var m=d.getAttribute('style').match(/url\\(['"]?(https?[^'")]+)/i); if(m) imgs.add(m[1]); });
            var html=document.documentElement.outerHTML;
            var matches = html.match(/https:\\/\\/[^"'\\s<>)]+\\.(jpg|jpeg|png|webp)/gi) || [];
            matches.forEach(function(u) { u = u.replace(/[),;'"]+$/, ''); if(u.length>40) imgs.add(u); });
            var bad=['logo','icon','svg','qr','footer','button','app-store','avatar','badge','tracking','ads','marker','map','pin','emoji','sprite','pixel','spacer','share','leaflet','layers','cdnjs','anunciante','premium','navent'];
            return Array.from(imgs).filter(function(u) {
                for (var i = 0; i < bad.length; i++) { if (u.toLowerCase().includes(bad[i])) return false; }
                return u.match(/\\.(jpg|jpeg|png|webp)/i) && u.length>50;
            });
            """
            ) or []

        out: list[str] = []
        normalized_seen: set[str] = set()
        bad_words = [
            "logo", "svg", "icon", "avatar", "banner", "tracking", "pixel", "marker", "watermark",
            "whatsapp", "facebook", "twitter", "instagram", "youtube", "badge", "leaflet",
            "cdnjs", "premium", "ads",
        ]
        if portal == "argenprop":
            bad_words.extend(["anunciante", "castromil", "inmobiliaria", "broker", "brand"])
        for url in fotos_raw:
            url = re.sub(r"[),;'\"]+$", "", url)
            if "zonapropcdn.com" in url or "zonaprop" in url:
                url = re.sub(r"/\d+x\d+/", "/1200x1200/", url)
            elif "cloudinary" in url:
                url = re.sub(r"/w_\d+/", "/w_1920/", url)
                url = re.sub(r"/h_\d+/", "/h_1080/", url)
                url = re.sub(r",w_\d+", ",w_1920", url)
                url = re.sub(r",h_\d+", ",h_1080", url)
                url = url.replace("/c_limit/", "/c_fill/").replace("/c_scale/", "/c_fill/").replace("/c_thumb/", "/c_fill/")
                url = re.sub(r"/q_\d+/", "/q_90/", url)
            if portal == "mercadolibre":
                url = self._upgrade_mercadolibre_image_url(url)
            if any(p in url.lower() for p in bad_words):
                continue
            if "data:image" in url.lower():
                continue
            norm_key = self._normalize_image_url_for_dedupe(url, portal)
            if norm_key in normalized_seen:
                continue
            normalized_seen.add(norm_key)
            out.append(url)
        return out

    @staticmethod
    def _normalize_image_url_for_dedupe(url: str, portal: str) -> str:
        low = (url or "").strip().lower()
        low = low.split("?", 1)[0]
        low = re.sub(r"/\d+x\d+/", "/SIZE/", low)
        low = re.sub(r"([_-])\d{2,4}x\d{2,4}([._-])", r"\1SIZExSIZE\2", low)
        if portal == "argenprop":
            low = re.sub(r"/(thumb|small|medium|large|original)/", "/SIZE/", low)
            low = re.sub(r"-\d+\.(jpg|jpeg|png|webp)$", r".\1", low)
            low = re.sub(r"(_copy|_dup|_clone)\.(jpg|jpeg|png|webp)$", r".\2", low)
            low = re.sub(r"/[a-f0-9]{8,}/", "/HASH/", low)
        if portal == "mercadolibre":
            low = re.sub(r"/[a-z]_[a-z0-9_]+_", "/SIZE_", low)
            low = re.sub(r"-[a-z]\.[a-z]{3,4}$", ".img", low)
        return low

    @staticmethod
    def _clean_mercadolibre_text(value: str) -> str:
        text = (value or "").strip()
        if not text:
            return ""
        bad = [
            "nunca te pediremos contraseñas",
            "pin o códigos de verificación",
            "a través de whatsapp",
            "teléfono, sms o email",
            "desde mercado libre",
        ]
        low = text.lower()
        if any(b in low for b in bad):
            return ""
        text = re.sub(r"^ubicaci[oó]n\s*", "", text, flags=re.I).strip()
        text = re.sub(r"\s*ver informaci[oó]n de la zona.*$", "", text, flags=re.I).strip()
        return text

    @staticmethod
    def _upgrade_mercadolibre_image_url(url: str) -> str:
        if not url:
            return url
        upgraded = re.sub(r"-[A-Z]\.(jpg|jpeg|png|webp)(\?.*)?$", r"-O.\1\2", url, flags=re.I)
        upgraded = re.sub(r"/D_[A-Z0-9_]+_", "/D_NQ_NP_", upgraded, flags=re.I)
        return upgraded

    def _normalize_features(self, lines: list[str]) -> list[str]:
        patterns = {
            "metros_totales": r"^[\d.,]+\s*m[²2]\s*(?:tot|tot\.|total|totales)\.?$",
            "metros_cubiertos": r"^[\d.,]+\s*m[²2]\s*(?:cub|cub\.|cubiertos?|cubierta)\.?$",
            "ambientes": r"^\d+\s*(?:amb|amb\.|ambientes?)$",
            "banos": r"^\d+\s*(?:baños?|banos?)$",
            "dormitorios": r"^\d+\s*(?:dorm|dorm\.|dormitorios?)$",
            "cocheras": r"^\d+\s*(?:cocheras?|coch\.)$",
            "antiguedad": r"^\d+\s*(?:años?|anios?)$",
            "orientacion": r"^(?:frente|contrafrente)$",
        }
        seen_cat: dict[str, str] = {}
        seen_txt: set[str] = set()
        for raw in lines:
            txt = re.sub(r"\s+", " ", (raw or "").strip())
            low = txt.lower()
            if not txt or len(txt) < 3 or low in seen_txt:
                continue
            seen_txt.add(low)

            matched_cat = None
            for cat, pat in patterns.items():
                if re.match(pat, low, flags=re.I):
                    matched_cat = cat
                    break
            if matched_cat and matched_cat in seen_cat:
                continue
            if matched_cat:
                seen_cat[matched_cat] = txt
                continue

            if re.match(r"^(?:balcon|terraza|patio|pileta|quincho|parrilla|gimnasio|sum|laundry|lavadero|baulera|ascensor|seguridad|portero)$", low, flags=re.I):
                seen_cat[f"extra_{low}"] = txt
        out = list(seen_cat.values())
        return out[:10]

    def _extract_details_from_features(self, features: list[str]) -> dict[str, str | None]:
        details: dict[str, str | None] = {
            "ambientes": None,
            "banos": None,
            "metros_totales": None,
            "metros_cubiertos": None,
        }
        for feature in features:
            txt = (feature or "").lower().strip()
            if not txt:
                continue
            m_amb = re.search(r"(\d+)\s*(?:amb|amb\.|ambientes?)\b", txt)
            if m_amb and not details["ambientes"]:
                details["ambientes"] = m_amb.group(1)
            m_banos = re.search(r"(\d+)\s*(?:baños?|banos?)\b", txt)
            if m_banos and not details["banos"]:
                details["banos"] = m_banos.group(1)
            m_tot = re.search(r"([\d.,]+)\s*m[²2]\s*(?:tot|tot\.|total|totales)\b", txt)
            if m_tot and not details["metros_totales"]:
                details["metros_totales"] = m_tot.group(1)
            m_cub = re.search(r"([\d.,]+)\s*m[²2]\s*(?:cub|cub\.|cubiertos?|cubierta)\b", txt)
            if m_cub and not details["metros_cubiertos"]:
                details["metros_cubiertos"] = m_cub.group(1)
        return details

    def _extract_extra_info(self, sb) -> dict[str, str | None]:
        info = sb.execute_script(
            """
            var i={antiguedad:null,expensas:null}; var txt=document.body.innerText;
            var ma=txt.match(/(?:antigüedad|antiguedad)\\s*[:\\-]?\\s*(\\d+\\s*a[ñn]os?|a\\s*estrenar|en\\s*construcción|nuevo)/i); if(ma) i.antiguedad=ma[1];
            var me=txt.match(/expensas?\\s*[:\\-]?\\s*(USD|\\$)?\\s*([\\d.,]+)/i); if(me) i.expensas=me[0];
            return i;
            """
        ) or {}
        return {
            "antiguedad": info.get("antiguedad"),
            "expensas": info.get("expensas"),
        }
