import importlib.util
import json
import os
import re
import sys
import time
from typing import Callable, Any


class ScraperService:
    def _debug_log(self, run_id: str, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
        payload = {
            "sessionId": "5ab736",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "debug-5ab736.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def scrape_property(self, source_url: str, log: Callable[[str], None]) -> dict[str, Any]:
        run_id = f"run-{int(time.time() * 1000)}"
        # region agent log
        self._debug_log(
            run_id=run_id,
            hypothesis_id="H1",
            location="services/scraper_service.py:scrape_property",
            message="Scrape entrypoint",
            data={
                "source_url_prefix": (source_url or "")[:120],
                "python_executable": sys.executable,
                "seleniumbase_spec_found": bool(importlib.util.find_spec("seleniumbase")),
            },
        )
        # endregion
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                return self._scrape_once(source_url, log)
            except Exception as e:
                # region agent log
                self._debug_log(
                    run_id=run_id,
                    hypothesis_id="H4",
                    location="services/scraper_service.py:scrape_property:except",
                    message="Scrape attempt exception",
                    data={
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "error_type": type(e).__name__,
                        "error": str(e)[:300],
                    },
                )
                # endregion
                if self._is_window_closed_error(e) and attempt < max_attempts:
                    log("El navegador se cerro inesperadamente. Reintentando...")
                    time.sleep(1.5)
                    continue
                raise

    def _scrape_once(self, source_url: str, log: Callable[[str], None]) -> dict[str, Any]:
        run_id = f"run-{int(time.time() * 1000)}"
        try:
            from seleniumbase import SB
            # region agent log
            self._debug_log(
                run_id=run_id,
                hypothesis_id="H2",
                location="services/scraper_service.py:_scrape_once:import",
                message="seleniumbase import ok",
                data={"imported": True},
            )
            # endregion
        except Exception as import_err:
            # region agent log
            self._debug_log(
                run_id=run_id,
                hypothesis_id="H2",
                location="services/scraper_service.py:_scrape_once:import",
                message="seleniumbase import failed",
                data={
                    "python_executable": sys.executable,
                    "seleniumbase_spec_found": bool(importlib.util.find_spec("seleniumbase")),
                    "error_type": type(import_err).__name__,
                    "error": str(import_err)[:300],
                },
            )
            # endregion
            raise

        log("Iniciando robot...")
        try:
            # Ejecuta oculto por defecto para no abrir ventana visible del navegador.
            sb_ctx = SB(uc=True, headless=True)
            sb = sb_ctx.__enter__()
            # region agent log
            self._debug_log(
                run_id=run_id,
                hypothesis_id="H3",
                location="services/scraper_service.py:_scrape_once:sb_init",
                message="Browser context initialized",
                data={"initialized": True},
            )
            # endregion
        except Exception as e:
            # region agent log
            self._debug_log(
                run_id=run_id,
                hypothesis_id="H3",
                location="services/scraper_service.py:_scrape_once:sb_init",
                message="Browser context init failed",
                data={"error_type": type(e).__name__, "error": str(e)[:300]},
            )
            # endregion
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
            # region agent log
            self._debug_log(
                run_id=run_id,
                hypothesis_id="H8",
                location="services/scraper_service.py:_scrape_once:header_values",
                message="Header extracted",
                data={
                    "titulo": (titulo or "")[:160],
                    "precio": precio,
                    "ubicacion": ubicacion,
                    "header_debug": getattr(self, "_last_header_debug", {}),
                },
            )
            # endregion
            log(f"Extraido: {titulo[:50]} | {precio}")
            fotos = self._extract_images(sb)
            log(f"Fotos detectadas: {len(fotos)}")
            descripcion = self._extract_description(sb)
            caracteristicas = self._extract_features(sb)
            # region agent log
            self._debug_log(
                run_id=run_id,
                hypothesis_id="H9",
                location="services/scraper_service.py:_scrape_once:features_values",
                message="Features extracted",
                data={
                    "caracteristicas": caracteristicas,
                    "features_debug": getattr(self, "_last_features_debug", {}),
                },
            )
            # endregion
            detalles = self._extract_details(sb, caracteristicas)
            # region agent log
            self._debug_log(
                run_id=run_id,
                hypothesis_id="H10",
                location="services/scraper_service.py:_scrape_once:details_values",
                message="Details extracted",
                data={"detalles": detalles},
            )
            # endregion
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
        precio_data = sb.execute_script(
            """
            function clean(t){ return (t||'').replace(/\\s+/g,' ').trim(); }
            function validPrice(t){
              var l=clean(t);
              return /(USD|U\\$S|AR\\$|\\$)\\s*[\\d.]+(?:,[\\d]+)?/i.test(l);
            }
            var candidates = [];
            var selectedSource = 'fallback';
            var sels = [
              '[data-qa="POSTING_CARD_PRICE"]',
              '[data-qa="POSTING_PRICE"]',
              '[data-qa*="PRICE"]',
              '[data-qa*="price"]',
              '[class*="posting-price"]',
              '[class*="price-value"]',
              '[class*="Price"]',
              '.price'
            ];
            for (var i = 0; i < sels.length; i++) {
              try {
                document.querySelectorAll(sels[i]).forEach(function(el){
                  var t = clean(el && el.innerText);
                  if (t && validPrice(t)) candidates.push({text:t, source:sels[i]});
                });
              } catch(e){}
            }
            for (var j = 0; j < candidates.length; j++) {
              var p = candidates[j].text;
              if (/(venta|alquiler)/i.test(p)) {
                selectedSource = candidates[j].source;
                return {value:p, candidates:candidates.slice(0,10), selected_source:selectedSource};
              }
            }
            for (var k = 0; k < candidates.length; k++) {
              var p2 = candidates[k].text;
              if (!/expensas?/i.test(p2)) {
                selectedSource = candidates[k].source;
                return {value:p2, candidates:candidates.slice(0,10), selected_source:selectedSource};
              }
            }

            var txt=document.body.innerText || '';
            var mVenta = txt.match(/(?:Venta|Alquiler)\\s+(?:USD|U\\$S|AR\\$|\\$)\\s*[\\d.]+(?:,[\\d]+)?/i);
            if (mVenta) return {value:clean(mVenta[0]), candidates:candidates.slice(0,10), selected_source:'body_venta'};
            var m=txt.match(/(?:USD|U\\$S|AR\\$|\\$)\\s*[\\d.]+(?:,[\\d]+)?/i);
            return {value:(m ? clean(m[0]) : 'Consultar precio'), candidates:candidates.slice(0,10), selected_source:'body_any'};
            """
        ) or {}
        precio = (precio_data.get("value") if isinstance(precio_data, dict) else str(precio_data or "")) or "Consultar precio"
        ubicacion_data = sb.execute_script(
            """
            function clean(t){ return (t||'').replace(/\\s+/g,' ').trim(); }
            function looksAddress(t){
              var v = clean(t);
              if (!v || v.length < 8 || v.length > 150) return false;
              if (!v.includes(',')) return false;
              if (!/\\d/.test(v)) return false;
              var bad = /(contact|anunciante|ingresar|publicar|favorito|compartir|terminos|condiciones|politica|expensas|ambientes?|dorm|bañ|m²|metros?|superficie)/i;
              return !bad.test(v);
            }
            var sels = [
              '[data-qa="POSTING_CARD_LOCATION"]',
              '[data-qa="POSTING_LOCATION"]',
              '[data-qa*="LOCATION"]',
              '[data-qa*="location"]',
              '[class*="posting-location"]',
              '[class*="PostingLocation"]',
              '[itemprop="address"]',
              'address'
            ];
            var candidates = [];
            for (var i = 0; i < sels.length; i++) {
              try {
                var nodes = document.querySelectorAll(sels[i]);
                for (var n = 0; n < nodes.length; n++) {
                  var t = clean(nodes[n] && nodes[n].innerText);
                  if (looksAddress(t)) {
                    candidates.push({text:t, source:sels[i]});
                    return {value:t, candidates:candidates.slice(0,12), selected_source:sels[i]};
                  }
                }
              } catch(e){}
            }

            var lines = (document.body.innerText || '').split('\\n').map(clean).filter(Boolean);
            var selectedPrice = clean(arguments[0] || '');
            var normalizedPrice = selectedPrice.replace(/^\\s*(venta|alquiler)\\s*/i,'').trim();
            var selectedIdx = lines.findIndex(function(line){
              var c = clean(line);
              if (!c) return false;
              if (selectedPrice && c.indexOf(selectedPrice) !== -1) return true;
              if (normalizedPrice && c.indexOf(normalizedPrice) !== -1) return true;
              return false;
            });
            if (selectedIdx >= 0) {
              for (var s = selectedIdx + 1; s < Math.min(lines.length, selectedIdx + 10); s++) {
                if (looksAddress(lines[s])) {
                  candidates.push({text:lines[s], source:'after_selected_price_line'});
                  return {value:lines[s], candidates:candidates.slice(0,12), selected_source:'after_selected_price_line'};
                }
              }
            }

            var priceIdx = lines.findIndex(function(line){
              return /(?:Venta|Alquiler)\\s+(?:USD|U\\$S|AR\\$|\\$)\\s*[\\d.]+(?:,[\\d]+)?/i.test(line);
            });
            if (priceIdx >= 0) {
              for (var j = priceIdx + 1; j < Math.min(lines.length, priceIdx + 8); j++) {
                if (looksAddress(lines[j])) {
                  candidates.push({text:lines[j], source:'after_price_line'});
                  return {value:lines[j], candidates:candidates.slice(0,12), selected_source:'after_price_line'};
                }
              }
            }
            for (var k = 0; k < lines.length; k++) {
              if (looksAddress(lines[k])) {
                candidates.push({text:lines[k], source:'all_lines'});
                return {value:lines[k], candidates:candidates.slice(0,12), selected_source:'all_lines'};
              }
            }
            return {value:'Ver en el portal', candidates:candidates.slice(0,12), selected_source:'none'};
            """
        , precio) or {}
        ubicacion = (ubicacion_data.get("value") if isinstance(ubicacion_data, dict) else str(ubicacion_data or "")) or "Ver en el portal"
        precio = re.sub(r"\s*(Avisarme|Avisar|si baja|de precio).*$", "", precio or "", flags=re.I).strip()
        m_venta = re.search(r"(Venta|Alquiler)\s*(USD|U\$S|AR\$|\$)\s*[\d.,]+", precio or "", re.I)
        if m_venta:
            precio = re.sub(r"\s+", " ", m_venta.group(0)).strip()
        m = re.search(r"(USD|U\$S|AR\$|\$)\s*[\d.,]+", precio or "", re.I)
        if m:
            if m_venta:
                simbolo_valor = m.group(0).strip()
                prefijo = "Venta" if re.search(r"venta", precio, re.I) else "Alquiler"
                precio = f"{prefijo} {simbolo_valor}"
            else:
                precio = m.group(0).strip()
        self._last_header_debug = {
            "precio_selected_source": precio_data.get("selected_source") if isinstance(precio_data, dict) else "unknown",
            "precio_candidates": precio_data.get("candidates", []) if isinstance(precio_data, dict) else [],
            "ubicacion_selected_source": ubicacion_data.get("selected_source") if isinstance(ubicacion_data, dict) else "unknown",
            "ubicacion_candidates": ubicacion_data.get("candidates", []) if isinstance(ubicacion_data, dict) else [],
        }
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

    def _extract_details(self, sb, features: list[str] | None = None) -> dict[str, str | None]:
        parsed_from_features = self._parse_details_from_features(features or [])
        details = sb.execute_script(
            """
            var d={ambientes:null,banos:null,metros_totales:null,metros_cubiertos:null};
            var blockSelectors=[
              '[data-qa="POSTING_MAIN_FEATURES"]',
              '[data-qa*="MAIN_FEATURE"]',
              '[class*="main-features"]',
              '[class*="posting-main-features"]',
              '[class*="postingFeaturesIcons"]',
              '[class*="icon-feature"]'
            ];
            var txt='';
            for(var i=0;i<blockSelectors.length;i++){
              try{
                var root=document.querySelector(blockSelectors[i]);
                if(root && root.innerText){
                  txt += '\\n' + root.innerText;
                }
              }catch(e){}
            }
            txt=(txt||'').toLowerCase();
            var ma=txt.match(/(\\d+)\\s*(?:ambientes?|amb\\.?)/i); if(ma) d.ambientes=ma[1];
            var mb=txt.match(/(\\d+)\\s*(?:baños?|banos?)/i); if(mb) d.banos=mb[1];
            var mt=txt.match(/([\\d.,]+)\\s*m[²2]\\s*(?:totales?|total|tot\\.?)/i); if(mt) d.metros_totales=mt[1];
            var mc=txt.match(/([\\d.,]+)\\s*m[²2]\\s*(?:cubiertos?|cubierta|cub\\.?)/i); if(mc) d.metros_cubiertos=mc[1];
            return d;
            """
        ) or {}
        return {
            "ambientes": parsed_from_features.get("ambientes") or details.get("ambientes"),
            "banos": parsed_from_features.get("banos") or details.get("banos"),
            "metros_totales": parsed_from_features.get("metros_totales") or details.get("metros_totales"),
            "metros_cubiertos": parsed_from_features.get("metros_cubiertos") or details.get("metros_cubiertos"),
        }

    def _extract_features(self, sb) -> list[str]:
        features_data = sb.execute_script(
            """
            var car=[];
            var seen={};
            var debugContainers = [];
            var exc=['cód','cod','anunciante','zonaprop','ver más','contactar','whatsapp','compartir','favorito','publicar','ingresar','registrate','iniciar sesión','buscar','filtrar'];
            function esValida(t){
                var l=(t||'').toLowerCase().trim().replace(/\\s+/g,' ');
                if(l.length<3||l.length>40) return false;
                for(var i=0;i<exc.length;i++) if(l.includes(exc[i])) return false;
                var fmts=[
                    /^\\d+[\\.,]?\\d*\\s*m[²2]\\s*(tot|tot\\.|total|totales)\\.?$/i,
                    /^\\d+[\\.,]?\\d*\\s*m[²2]\\s*(cub|cub\\.|cubiertos|cubierto|cubierta)\\.?$/i,
                    /^\\d+\\s*(?:amb|amb\\.|ambientes?)$/i,
                    /^\\d+\\s*(?:baños?|banos?)$/i,
                    /^\\d+\\s*(?:dorm|dorm\\.|dormitorios?)$/i,
                    /^\\d+\\s*(?:años?|anios?)$/i,
                    /^\\d+\\s*(?:toilettes?)$/i,
                    /^\\d+\\s*(?:cocheras?)$/i,
                    /^(?:a\\s*estrenar|nuevo)$/i,
                    /^(?:balcon|terraza|patio|cochera|pileta|quincho|parrilla|gimnasio|sum|laundry|lavadero|baulera|ascensor|seguridad|portero|contrafrente|frente)$/i
                ];
                return fmts.some(function(p){return p.test(l);});
            }
            function categoryOf(text){
                var l=(text||'').toLowerCase().trim().replace(/\\s+/g,' ');
                if (/\\bm[²2]\\s*(tot|tot\\.|total|totales)\\b/i.test(l)) return 'metros_totales';
                if (/\\bm[²2]\\s*(cub|cub\\.|cubiertos|cubierto|cubierta)\\b/i.test(l)) return 'metros_cubiertos';
                if (/\\b(?:amb|amb\\.|ambientes?)\\b/i.test(l)) return 'ambientes';
                if (/\\b(?:baños?|banos?)\\b/i.test(l)) return 'banos';
                if (/\\b(?:dorm|dorm\\.|dormitorios?)\\b/i.test(l)) return 'dormitorios';
                if (/\\b(?:cocheras?|coch\\.)\\b/i.test(l)) return 'cocheras';
                if (/\\b(?:años?|anios?)\\b/i.test(l)) return 'antiguedad';
                if (/\\b(?:frente|contrafrente)\\b/i.test(l)) return 'orientacion';
                if (/\\b(?:toilettes?)\\b/i.test(l)) return 'toilettes';
                if (/^(?:balcon|terraza|patio|pileta|quincho|parrilla|gimnasio|sum|laundry|lavadero|baulera|ascensor|seguridad|portero)$/i.test(l)) return l;
                return null;
            }
            var seenCategory = {};
            function pushClean(text){
                var t=(text||'').trim().replace(/\\s+/g,' ');
                var key=t.toLowerCase();
                if(!esValida(t) || seen[key]) return;
                var cat = categoryOf(t);
                if (cat && seenCategory[cat]) return;
                seen[key]=true;
                if (cat) seenCategory[cat] = true;
                car.push(t);
            }

            var blockSelectors=[
              '[data-qa="POSTING_MAIN_FEATURES"]',
              '[data-qa*="MAIN_FEATURE"]',
              '[class*="main-features"]',
              '[class*="posting-main-features"]',
              '[class*="postingFeaturesIcons"]',
              '[class*="icon-feature"]'
            ];
            var itemSelectors='li, span, p, div';
            for(var i=0;i<blockSelectors.length;i++){
              try{
                var roots = document.querySelectorAll(blockSelectors[i]);
                roots.forEach(function(root){
                  var used = 0;
                  var sampleLines = [];
                  var lines = (root && root.innerText ? root.innerText.split('\\n') : [])
                    .map(function(x){ return (x||'').trim(); })
                    .filter(function(x){ return !!x; });
                  lines.forEach(function(line){
                    if (sampleLines.length < 12) sampleLines.push(line);
                    var before = car.length;
                    pushClean(line);
                    if (car.length > before) used += 1;
                  });
                  var directNodes = root.querySelectorAll(':scope > * ' + itemSelectors).length;
                  debugContainers.push({
                    selector:blockSelectors[i],
                    roots_count:roots.length,
                    lines_count:lines.length,
                    accepted_nodes:used,
                    direct_nodes:directNodes,
                    sample_lines:sampleLines
                  });
                });
              }catch(e){}
            }
            return {features:car.slice(0,10), containers:debugContainers};
            """
        ) or {}
        features = features_data.get("features", []) if isinstance(features_data, dict) else (features_data or [])
        seen: dict[str, str] = {}
        for f in features:
            key = (f or "").lower().strip()
            if key and key not in seen:
                seen[key] = f
        deduped = list(seen.values())
        self._last_features_debug = {
            "raw_count": len(features),
            "deduped_count": len(deduped),
            "containers": features_data.get("containers", []) if isinstance(features_data, dict) else [],
        }
        return deduped

    def _parse_details_from_features(self, features: list[str]) -> dict[str, str | None]:
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
