import re
import time
from typing import Callable, Any


class ScraperService:
    def scrape_property(self, source_url: str, log: Callable[[str], None]) -> dict[str, Any]:
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                return self._scrape_once(source_url, log)
            except Exception as e:
                if self._is_window_closed_error(e) and attempt < max_attempts:
                    log("El navegador se cerro inesperadamente. Reintentando...")
                    time.sleep(1.5)
                    continue
                raise

    def _scrape_once(self, source_url: str, log: Callable[[str], None]) -> dict[str, Any]:
        from seleniumbase import SB

        log("Iniciando robot...")
        try:
            sb_ctx = SB(uc=True, headless=False)
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
            titulo, precio, ubicacion = self._extract_header(sb)
            log(f"Extraido: {titulo[:50]} | {precio}")
            fotos = self._extract_images(sb)
            log(f"Fotos detectadas: {len(fotos)}")
            descripcion = self._extract_description(sb)
            detalles = self._extract_details(sb)
            caracteristicas = self._extract_features(sb)
            info_adicional = self._extract_extra_info(sb)

            return {
                "titulo": titulo or "Propiedad en Venta",
                "precio": precio or "Consultar precio",
                "ubicacion": ubicacion or "Ver en el portal",
                "descripcion": descripcion or "Sin descripcion",
                "detalles": detalles,
                "caracteristicas": caracteristicas,
                "info_adicional": info_adicional,
                "image_urls": fotos[:20],
            }
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

    def _extract_header(self, sb) -> tuple[str, str, str]:
        titulo = sb.execute_script(
            """
            var sels = ['h1[data-qa="POSTING_TITLE"]','[data-qa="POSTING_TITLE"]','h1[class*="posting"]','[class*="PostingTitle"]','h1[class*="title"]','h1'];
            for (var i = 0; i < sels.length; i++) { try { var el = document.querySelector(sels[i]); if (el && el.innerText && el.innerText.trim().length > 10) return el.innerText.trim(); } catch(e){} }
            return 'Propiedad en Venta';
            """
        )
        precio = sb.execute_script(
            """
            var sels = ['[data-qa="POSTING_CARD_PRICE"]','[data-qa*="price"]','[class*="price-value"]','[class*="Price"]','.price'];
            for (var i = 0; i < sels.length; i++) { try { var el=document.querySelector(sels[i]); if(el&&el.innerText) { var t=el.innerText.trim(); if(t.match(/USD|\\$|AR\\$/i)&&t.match(/[\\d.,]+/)) return t.replace(/\\n/g,' ').trim(); } }catch(e){} }
            var txt=document.body.innerText; var m=txt.match(/(?:USD|U\\$S)\\s*[\\d.,]+/i)||txt.match(/\\$\\s*[\\d.,]+/); return m?m[0].trim():'Consultar precio';
            """
        )
        ubicacion = sb.execute_script(
            """
            var sels = ['[data-qa="POSTING_CARD_LOCATION"]','[data-qa*="location"]','[class*="posting-location"]','[class*="PostingLocation"]','[itemprop="address"]','address'];
            for (var i = 0; i < sels.length; i++) { try { var el=document.querySelector(sels[i]); if(el&&el.innerText){ var t=el.innerText.trim(); if(t.length>5&&t.length<150) return t; } }catch(e){} }
            return 'Ver en el portal';
            """
        )
        precio = re.sub(r"\s*(Avisarme|Avisar|si baja|de precio|Venta|Alquiler).*$", "", precio or "", flags=re.I).strip()
        m = re.search(r"(USD|U\$S|AR\$|\$)\s*[\d.,]+", precio or "", re.I)
        if m:
            precio = m.group(0).strip()
        return titulo or "", precio or "", ubicacion or ""

    def _extract_images(self, sb) -> list[str]:
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
        bad_words = [
            "logo", "svg", "icon", "avatar", "banner", "tracking", "pixel", "marker",
            "whatsapp", "facebook", "twitter", "instagram", "youtube", "badge", "leaflet",
            "cdnjs", "anunciante", "premium", "navent",
        ]
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
            if any(p in url.lower() for p in bad_words):
                continue
            out.append(url)
        return list(dict.fromkeys(out))

    def _extract_description(self, sb) -> str:
        return sb.execute_script(
            """
            var sels = ['[data-qa="POSTING_DESCRIPTION"]','[data-qa="posting-description"]','#posting-description','.posting-description','[class*="PostingDescription"]','[class*="postingDescription"]','[class*="description-content"]','[class*="section-description"]','[id*="description"]'];
            for (var i = 0; i < sels.length; i++) { try { var el=document.querySelector(sels[i]); if(el){ var t=el.innerText; t=t?t.trim():''; if(t.length>30&&t.length<5000&&t.indexOf('iniciar sesión')===-1&&t.indexOf('cookie')===-1) return t; } }catch(e){} }
            return '';
            """
        ) or ""

    def _extract_details(self, sb) -> dict[str, str | None]:
        details = sb.execute_script(
            """
            var d={ambientes:null,banos:null,metros_totales:null,metros_cubiertos:null};
            var txt=document.body.innerText.toLowerCase();
            var ma=txt.match(/(\\d+)\\s*(?:ambientes?|amb\\.?)/i); if(ma) d.ambientes=ma[1];
            if(!d.ambientes){var m=txt.match(/(\\d+)\\s*(?:dormitorios?|habitacion)/i); if(m) d.ambientes=m[1];}
            var mb=txt.match(/(\\d+)\\s*(?:baños?|banos?)/i); if(mb) d.banos=mb[1];
            var mt=txt.match(/(?:sup\\.?\\s*(?:total|tot\\.?)?|superficie\\s*total)\\s*:?\\s*([\\d.,]+)\\s*m[²2]/i)||txt.match(/([\\d.,]+)\\s*m[²2]\\s*(?:totales?|total)/i)||txt.match(/([\\d.,]+)\\s*m[²2]/i); if(mt) d.metros_totales=mt[1];
            var mc=txt.match(/(?:sup\\.?\\s*(?:cubierta|cub\\.?)?|superficie\\s*cubierta)\\s*:?\\s*([\\d.,]+)\\s*m[²2]/i)||txt.match(/([\\d.,]+)\\s*m[²2]\\s*(?:cubiertos?|cubierta)/i); if(mc) d.metros_cubiertos=mc[1];
            return d;
            """
        ) or {}
        return {
            "ambientes": details.get("ambientes"),
            "banos": details.get("banos"),
            "metros_totales": details.get("metros_totales"),
            "metros_cubiertos": details.get("metros_cubiertos"),
        }

    def _extract_features(self, sb) -> list[str]:
        features = sb.execute_script(
            """
            var car=new Set();
            var exc=['cód','cod','anunciante','zonaprop','ver más','contactar','whatsapp','compartir','favorito','publicar','ingresar','registrate','iniciar sesión','buscar','filtrar'];
            function esValida(t){ var l=t.toLowerCase().trim(); if(l.length<3||l.length>30) return false; for(var i=0;i<exc.length;i++) if(l.includes(exc[i])) return false;
                var fmts=[/^\\d+\\s*m[²2]\\s*(tot|cub)\\.?$/i,/^\\d+\\s*amb\\.?$/i,/^\\d+\\s*baños?$/i,/^\\d+\\s*dorm\\.?$/i,/^\\d+\\s*toilettes?$/i,/^a\\s*estrenar$/i,/^\\d+\\s*cocheras?$/i,/^\\d+\\s*dormitorios?$/i,/^con\\s+(balcon|terraza|patio|cochera|pileta)/i,/^(balcon|terraza|patio|cochera|pileta|quincho|parrilla|gimnasio|sum|laundry|lavadero|baulera|ascensor|seguridad|portero)$/i];
                return fmts.some(function(p){return p.test(l)}); }
            ['[class*="posting-features"] span','[class*="PostingFeatures"] span','[class*="icon-feature"] span','[class*="main-features"] span'].forEach(function(s){ try{document.querySelectorAll(s).forEach(function(el){var t=el.innerText; t=t?t.trim():''; if(esValida(t)) car.add(t);})}catch(e){} });
            if(car.size<4) document.body.innerText.split('\\n').forEach(function(line){var t=line.trim();if(esValida(t)) car.add(t);});
            return Array.from(car).slice(0,10);
            """
        ) or []
        seen: dict[str, str] = {}
        for f in features:
            key = (f or "").lower().strip()
            if key and key not in seen:
                seen[key] = f
        return list(seen.values())

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
