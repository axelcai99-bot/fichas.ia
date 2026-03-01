import asyncio
import hashlib
import json
import os
import queue
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from functools import wraps
from flask import (Flask, render_template, request, jsonify,
                   Response, stream_with_context, session, redirect, url_for, abort)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fichas-ia-secret-2024-cambiar-en-produccion")

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(BASE_DIR, "users.json")
JOBS: dict = {}  # job_id -> {"queue", "status", "result_url", "user"}

# ─── Helpers de usuarios ───────────────────────────────────────────────────────

def load_users():
    if not os.path.exists(USERS_FILE):
        # Crear admin por defecto
        default = {
            "admin": {
                "password": hash_pw("admin123"),
                "role": "admin",
                "nombre": "Administrador",
                "whatsapp": "",
                "logo": "",
                "form_url": "",
                "netlify_token": "",
                "created": datetime.now().isoformat(),
                "active": True,
            }
        }
        save_users(default)
        return default
    with open(USERS_FILE, "r") as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)

def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def get_user(username: str):
    return load_users().get(username)

# ─── Decoradores de auth ───────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        user = get_user(session["username"])
        if not user or user.get("role") != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated

# ─── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/")
def root():
    if "username" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "").strip()
        user = get_user(username)
        if user and user.get("active", True) and user["password"] == hash_pw(password):
            session["username"] = username
            session["role"] = user.get("role", "user")
            return redirect(url_for("dashboard"))
        error = "Usuario o contraseña incorrectos"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ─── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    username = session["username"]
    user = get_user(username)
    users_all = load_users() if user.get("role") == "admin" else {}
    return render_template("dashboard.html",
        username=username,
        user=user,
        is_admin=(user.get("role") == "admin"),
        users_all=users_all,
    )

# ─── Perfil / config del usuario ──────────────────────────────────────────────

@app.route("/api/perfil", methods=["POST"])
@login_required
def guardar_perfil():
    username = session["username"]
    data = request.json or {}
    users = load_users()
    for campo in ["nombre", "whatsapp", "logo", "form_url", "netlify_token"]:
        if campo in data:
            users[username][campo] = data[campo]
    save_users(users)
    return jsonify({"ok": True})

@app.route("/api/cambiar_password", methods=["POST"])
@login_required
def cambiar_password():
    username = session["username"]
    data = request.json or {}
    pw_actual = data.get("pw_actual", "")
    pw_nueva  = data.get("pw_nueva", "")
    if len(pw_nueva) < 6:
        return jsonify({"error": "La contraseña debe tener al menos 6 caracteres"}), 400
    users = load_users()
    if users[username]["password"] != hash_pw(pw_actual):
        return jsonify({"error": "Contraseña actual incorrecta"}), 400
    users[username]["password"] = hash_pw(pw_nueva)
    save_users(users)
    return jsonify({"ok": True})

# ─── Admin: gestión de usuarios ───────────────────────────────────────────────

@app.route("/api/admin/usuarios", methods=["GET"])
@admin_required
def listar_usuarios():
    users = load_users()
    result = []
    for u, data in users.items():
        result.append({
            "username": u,
            "nombre": data.get("nombre", u),
            "role": data.get("role", "user"),
            "active": data.get("active", True),
            "created": data.get("created", ""),
        })
    return jsonify(result)

@app.route("/api/admin/crear_usuario", methods=["POST"])
@admin_required
def crear_usuario():
    data = request.json or {}
    username = data.get("username", "").strip().lower()
    password = data.get("password", "").strip()
    nombre   = data.get("nombre", "").strip()
    if not username or not password or not nombre:
        return jsonify({"error": "Faltan datos"}), 400
    if len(password) < 6:
        return jsonify({"error": "Contraseña mínimo 6 caracteres"}), 400
    users = load_users()
    if username in users:
        return jsonify({"error": "El usuario ya existe"}), 400
    users[username] = {
        "password": hash_pw(password),
        "role": "user",
        "nombre": nombre,
        "whatsapp": "",
        "logo": "",
        "form_url": "",
        "netlify_token": "",
        "created": datetime.now().isoformat(),
        "active": True,
    }
    save_users(users)
    return jsonify({"ok": True, "username": username})

@app.route("/api/admin/toggle_usuario", methods=["POST"])
@admin_required
def toggle_usuario():
    data = request.json or {}
    username = data.get("username", "")
    if username == "admin":
        return jsonify({"error": "No podés desactivar al admin"}), 400
    users = load_users()
    if username not in users:
        return jsonify({"error": "Usuario no encontrado"}), 404
    users[username]["active"] = not users[username].get("active", True)
    save_users(users)
    return jsonify({"ok": True, "active": users[username]["active"]})

@app.route("/api/admin/reset_password", methods=["POST"])
@admin_required
def reset_password():
    data = request.json or {}
    username = data.get("username", "")
    new_pw = data.get("password", "")
    if len(new_pw) < 6:
        return jsonify({"error": "Mínimo 6 caracteres"}), 400
    users = load_users()
    if username not in users:
        return jsonify({"error": "Usuario no encontrado"}), 404
    users[username]["password"] = hash_pw(new_pw)
    save_users(users)
    return jsonify({"ok": True})

# ─── Generador de fichas ───────────────────────────────────────────────────────

@app.route("/api/generar", methods=["POST"])
@login_required
def generar():
    username = session["username"]
    user     = get_user(username)
    data     = request.json or {}
    url_prop = data.get("url", "").strip()
    if not url_prop:
        return jsonify({"error": "Falta el link"}), 400

    nombre   = data.get("nombre", user.get("nombre", "")).strip()
    whatsapp = data.get("whatsapp", user.get("whatsapp", "")).strip()
    logo     = data.get("logo", user.get("logo", "")).strip()
    form_url = data.get("form_url", user.get("form_url", "")).strip()
    netlify  = data.get("netlify_token", user.get("netlify_token", "")).strip()

    job_id    = uuid.uuid4().hex
    log_queue = queue.Queue()
    JOBS[job_id] = {"queue": log_queue, "status": "running", "result_url": None, "user": username}

    threading.Thread(
        target=_run_scraping,
        args=(job_id, url_prop, nombre, whatsapp, logo, form_url, netlify),
        daemon=True,
    ).start()

    return jsonify({"job_id": job_id})

@app.route("/api/stream/<job_id>")
@login_required
def stream(job_id):
    job = JOBS.get(job_id)
    if not job or job.get("user") != session["username"]:
        abort(403)

    def generate():
        while True:
            try:
                msg = job["queue"].get(timeout=30)
                if msg == "__DONE__":
                    result = job.get("result_url") or ""
                    yield f"event: done\ndata: {result}\n\n"
                    break
                elif msg == "__ERROR__":
                    yield "event: error\ndata: Error\n\n"
                    break
                else:
                    yield f"data: {msg.replace(chr(10), ' ')}\n\n"
            except queue.Empty:
                yield "data: ⏳\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ─── Scraping ─────────────────────────────────────────────────────────────────

def _run_scraping(job_id, url, nombre, whatsapp, logo, form_url, netlify_token):
    job = JOBS[job_id]
    q   = job["queue"]
    try:
        asyncio.run(_scraping_async(q, job_id, url, nombre, whatsapp, logo, form_url, netlify_token))
    except Exception as e:
        q.put(f"❌ Error: {e}")
        job["status"] = "error"
        q.put("__ERROR__")


async def _scraping_async(q, job_id, url_propiedad, tu_nombre, tu_whatsapp, url_logo, form_url, netlify_token):
    from playwright.async_api import async_playwright
    job = JOBS[job_id]

    def log(msg): q.put(msg)

    log("🚀 Iniciando robot...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page    = await context.new_page()

        log(f"🌐 Abriendo página...")
        await page.goto(url_propiedad, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)

        log("📸 Buscando galería de fotos...")
        for sel in ['button:has-text("fotos")', 'button:has-text("Fotos")',
                    '[data-qa*="gallery"]', '[data-qa*="GALLERY"]',
                    '[class*="gallery"] button', '[class*="photo-count"]']:
            try:
                await page.wait_for_selector(sel, timeout=2000)
                await page.locator(sel).first.click()
                await asyncio.sleep(2)
                break
            except: continue

        titulo = precio = ubicacion = descripcion = "—"
        fotos = []; detalles = {"ambientes":None,"banos":None,"metros_totales":None,"metros_cubiertos":None}
        caracteristicas = []; info_adicional = {"antiguedad":None,"expensas":None}

        try:
            log("📊 Extrayendo datos de la propiedad...")

            titulo = await page.evaluate("""() => {
                const sels = ['h1[data-qa="POSTING_TITLE"]','[data-qa="POSTING_TITLE"]','h1[class*="posting"]','[class*="PostingTitle"]','h1[class*="title"]','h1'];
                for (const s of sels) { try { const el = document.querySelector(s); if (el && el.innerText && el.innerText.trim().length > 10) return el.innerText.trim(); } catch(e){} }
                let t = 'Propiedad en Venta';
                document.querySelectorAll('script[type="application/ld+json"]').forEach(s => { try { const j=JSON.parse(s.textContent); if(j.name&&j.name.length>10) t=j.name; }catch(e){} });
                return t;
            }""")

            precio = await page.evaluate("""() => {
                const sels = ['[data-qa="POSTING_CARD_PRICE"]','[data-qa*="price"]','[class*="price-value"]','[class*="Price"]','.price'];
                for (const s of sels) { try { const el=document.querySelector(s); if(el&&el.innerText) { const t=el.innerText.trim(); if(t.match(/USD|\\$|AR\\$/i)&&t.match(/[\\d.,]+/)) return t.replace(/\\n/g,' ').trim(); } }catch(e){} }
                const txt=document.body.innerText; const m=txt.match(/(?:USD|U\\$S)\\s*[\\d.,]+/i)||txt.match(/\\$\\s*[\\d.,]+/); return m?m[0].trim():'Consultar precio';
            }""")

            ubicacion = await page.evaluate("""() => {
                const sels = ['[data-qa="POSTING_CARD_LOCATION"]','[data-qa*="location"]','[class*="posting-location"]','[class*="PostingLocation"]','[itemprop="address"]','address'];
                for (const s of sels) { try { const el=document.querySelector(s); if(el&&el.innerText){ const t=el.innerText.trim(); if(t.length>5&&t.length<150) return t; } }catch(e){} }
                return 'Ver en el portal';
            }""")

            log(f"✅ {titulo[:50]} | {precio}")

            log("🖼️  Capturando fotos en alta resolución...")
            fotos_raw = await page.evaluate("""() => {
                const imgs = new Set();
                document.querySelectorAll('img').forEach(img => {
                    ['src','data-src','data-lazy-src','data-original'].forEach(a => { const u=img.getAttribute(a); if(u&&u.includes('http')&&u.length>30&&!u.includes('logo')&&!u.includes('icon')&&!u.includes('svg')) imgs.add(u); });
                    const ss=img.getAttribute('srcset'); if(ss) ss.split(',').forEach(s => { const u=s.trim().split(' ')[0]; if(u&&u.includes('http')) imgs.add(u); });
                });
                document.querySelectorAll('[style*="background-image"]').forEach(d => { const m=d.getAttribute('style').match(/url\\(['"]?(https?[^'")]+)/i); if(m) imgs.add(m[1]); });
                const html=document.documentElement.innerHTML;
                (html.match(/https:\\/\\/[^"'\\s]+cloudinary[^"'\\s]+\\.(jpg|jpeg|png|webp)/gi)||[]).forEach(u=>imgs.add(u));
                (html.match(/https:\\/\\/[^"'\\s]+zonaprop[^"'\\s]+\\.(jpg|jpeg|png|webp)/gi)||[]).forEach(u=>imgs.add(u));
                document.querySelectorAll('script#__NEXT_DATA__').forEach(sc => { try { const findU=(o)=>{ if(!o||typeof o!=='object') return; if(typeof o==='string'&&o.match(/https?:.*\\.(jpg|jpeg|png|webp)/i)){imgs.add(o);return;} if(Array.isArray(o)) o.forEach(findU); else Object.values(o).forEach(findU); }; findU(JSON.parse(sc.textContent)); }catch(e){} });
                return [...imgs].filter(u => {
                    const bad=['logo','icon','svg','qr','footer','button','app-store','avatar','badge','tracking','ads','marker','map','pin','emoji','sprite','pixel','spacer','share'];
                    if(bad.some(w=>u.toLowerCase().includes(w))) return false;
                    const ok=u.includes('cloudinary')||u.includes('zonaprop')||u.includes('inmueble')||u.includes('property')||(u.match(/\\.(jpg|jpeg|png|webp)/i)&&u.length>80);
                    if(u.includes('w_')||u.includes('h_')){ const w=parseInt((u.match(/w_(\\d+)/)||[0,9999])[1]); const h=parseInt((u.match(/h_(\\d+)/)||[0,9999])[1]); return (w>=200||h>=200)&&ok; }
                    return ok&&u.length>50;
                });
            }""")

            fotos = []
            for u in fotos_raw:
                if "cloudinary" in u:
                    u = re.sub(r'/w_\d+/', '/w_1920/', u)
                    u = re.sub(r'/h_\d+/', '/h_1080/', u)
                    u = re.sub(r',w_\d+', ',w_1920', u)
                    u = re.sub(r',h_\d+', ',h_1080', u)
                    u = u.replace('/c_limit/','/c_fill/').replace('/c_scale/','/c_fill/').replace('/c_thumb/','/c_fill/')
                    u = re.sub(r'/q_\d+/', '/q_90/', u)
                fotos.append(u)
            fotos = list(dict.fromkeys(fotos))
            bad_words = ['logo','svg','icon','avatar','banner','tracking','pixel','marker','whatsapp','facebook','twitter','instagram','youtube','badge']
            fotos = [u for u in fotos if not any(p in u.lower() for p in bad_words)]
            log(f"✅ {len(fotos)} fotos capturadas")

            descripcion = await page.evaluate("""() => {
                const sels = ['[data-qa="POSTING_DESCRIPTION"]','[data-qa="posting-description"]','#posting-description','.posting-description','[class*="PostingDescription"]','[class*="postingDescription"]','[class*="description-content"]'];
                for (const s of sels) { try { const el=document.querySelector(s); if(el){ const t=el.innerText?.trim()||''; if(t.length>50&&t.length<5000&&!t.includes('iniciar sesión')&&!t.includes('cookie')) return t; } }catch(e){} }
                let best=''; document.querySelectorAll('p, div > span').forEach(p => { const t=p.innerText?.trim()||''; if(t.length>80&&t.length<4000){ const kw=['ambiente','baño','cocina','living','dormitorio','metros','m²','departamento','propiedad','piso','balcón','terraza']; if(kw.some(k=>t.toLowerCase().includes(k))&&t.length>best.length) best=t; } });
                return (best||'Ver detalles en el portal').replace(/Ver más$/i,'').replace(/Leer más$/i,'').replace(/\\s+/g,' ').trim();
            }""")

            detalles_raw = await page.evaluate("""() => {
                const d={ambientes:null,banos:null,metros_totales:null,metros_cubiertos:null};
                const txt=document.body.innerText.toLowerCase();
                const ma=txt.match(/(\\d+)\\s*(?:ambientes?|amb\\.?)/i); if(ma) d.ambientes=ma[1];
                if(!d.ambientes){const m=txt.match(/(\\d+)\\s*(?:dormitorios?|habitacion)/i); if(m) d.ambientes=m[1];}
                const mb=txt.match(/(\\d+)\\s*(?:baños?|banos?)/i); if(mb) d.banos=mb[1];
                const mt=txt.match(/(?:sup\\.?\\s*(?:total|tot\\.?)?|superficie\\s*total)\\s*:?\\s*([\\d.,]+)\\s*m[²2]/i)||txt.match(/([\\d.,]+)\\s*m[²2]\\s*(?:totales?|total)/i)||txt.match(/([\\d.,]+)\\s*m[²2]/i); if(mt) d.metros_totales=mt[1];
                const mc=txt.match(/(?:sup\\.?\\s*(?:cubierta|cub\\.?)?|superficie\\s*cubierta)\\s*:?\\s*([\\d.,]+)\\s*m[²2]/i)||txt.match(/([\\d.,]+)\\s*m[²2]\\s*(?:cubiertos?|cubierta)/i); if(mc) d.metros_cubiertos=mc[1];
                return d;
            }""")
            detalles.update(detalles_raw)

            caracteristicas = await page.evaluate("""() => {
                const car=new Set();
                const exc=['cód','cod','anunciante','zonaprop','ver más','contactar','whatsapp','compartir','favorito','publicar','ingresar','registrate','iniciar sesión','barracas','palermo','recoleta','belgrano','caballito','flores','almagro','villa','san telmo','puerto madero','buscar','filtrar'];
                const esValida=(t)=>{ const l=t.toLowerCase().trim(); if(l.length<3||l.length>30) return false; for(const e of exc) if(l.includes(e)) return false; if(l.match(/\\d+\\s*ambiente[s]?\\s*:/)) return false;
                    const fmts=[/^\\d+\\s*m[²2]\\s*(tot|cub)\\.?$/i,/^\\d+\\s*amb\\.?$/i,/^\\d+\\s*baños?$/i,/^\\d+\\s*dorm\\.?$/i,/^\\d+\\s*toilettes?$/i,/^a\\s*estrenar$/i,/^\\d+\\s*cocheras?$/i,/^\\d+\\s*dormitorios?$/i,/^con\\s+(balcon|terraza|patio|cochera|pileta)/i,/^(balcon|terraza|patio|cochera|pileta|quincho|parrilla|gimnasio|sum|laundry|lavadero|baulera|ascensor|seguridad|portero)$/i];
                    return fmts.some(p=>p.test(l)); };
                ['[class*="posting-features"] span','[class*="PostingFeatures"] span','[class*="icon-feature"] span','[class*="main-features"] span'].forEach(s=>{ try{document.querySelectorAll(s).forEach(el=>{const t=el.innerText?.trim()||'';if(esValida(t)) car.add(t);})}catch(e){} });
                if(car.size<4) document.body.innerText.split('\\n').forEach(line=>{const t=line.trim();if(esValida(t)) car.add(t);});
                return [...car].slice(0,10);
            }""")
            seen={}
            for c in caracteristicas:
                k=c.lower().strip()
                if k not in seen: seen[k]=c
            caracteristicas = list(seen.values())

            info_raw = await page.evaluate("""() => {
                const i={antiguedad:null,expensas:null}; const txt=document.body.innerText;
                const ma=txt.match(/(?:antigüedad|antiguedad)\\s*[:\\-]?\\s*(\\d+\\s*a[ñn]os?|a\\s*estrenar|en\\s*construcción|nuevo)/i); if(ma) i.antiguedad=ma[1];
                const me=txt.match(/expensas?\\s*[:\\-]?\\s*(USD|\\$)?\\s*([\\d.,]+)/i); if(me) i.expensas=me[0];
                return i;
            }""")
            info_adicional.update(info_raw)

        except Exception as e:
            log(f"⚠️ Error extrayendo: {e}")

        log("🎨 Generando ficha HTML...")
        html = generar_html(titulo, precio, ubicacion, descripcion, fotos[:20],
                            detalles, caracteristicas, info_adicional,
                            tu_nombre, tu_whatsapp, url_logo, form_url)

        if netlify_token:
            log("☁️  Subiendo a Netlify...")
            url_online = subir_a_netlify(html.encode("utf-8"), netlify_token, ubicacion)
            if url_online:
                job["result_url"] = url_online
                log(f"🔗 {url_online}")
            else:
                log("⚠️ Error subiendo a Netlify. Revisá el token.")
        else:
            log("⚠️ Sin token de Netlify — configurá uno en tu perfil.")

        log("✅ ¡Proceso completado!")
        await browser.close()
        job["status"] = "done"
        job["queue"].put("__DONE__")


# ─── Netlify ───────────────────────────────────────────────────────────────────

def subir_a_netlify(html_bytes, token, nombre_sitio=""):
    import unicodedata
    def slug(t):
        t = unicodedata.normalize("NFKD", t).encode("ascii","ignore").decode("ascii")
        t = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:50].strip("-")
        return t
    sha1 = hashlib.sha1(html_bytes).hexdigest()
    site_name = f"{slug(nombre_sitio)}-{uuid.uuid4().hex[:4]}" if nombre_sitio else f"ficha-{uuid.uuid4().hex[:4]}"
    try:
        req = urllib.request.Request("https://api.netlify.com/api/v1/sites",
            data=json.dumps({"name":site_name}).encode(),
            headers={"Content-Type":"application/json","Authorization":f"Bearer {token}"}, method="POST")
        sitio = json.loads(urllib.request.urlopen(req,timeout=30).read())
        site_id = sitio.get("site_id") or sitio.get("id")
        site_url = sitio.get("ssl_url") or sitio.get("url")
        req2 = urllib.request.Request(f"https://api.netlify.com/api/v1/sites/{site_id}/deploys",
            data=json.dumps({"files":{"/index.html":sha1}}).encode(),
            headers={"Content-Type":"application/json","Authorization":f"Bearer {token}"}, method="POST")
        deploy = json.loads(urllib.request.urlopen(req2,timeout=30).read())
        deploy_id = deploy.get("id"); required = deploy.get("required",[])
        if sha1 in required:
            req3 = urllib.request.Request(f"https://api.netlify.com/api/v1/deploys/{deploy_id}/files/index.html",
                data=html_bytes, headers={"Content-Type":"application/octet-stream","Authorization":f"Bearer {token}"}, method="PUT")
            urllib.request.urlopen(req3,timeout=60)
        for _ in range(15):
            time.sleep(2)
            try:
                req4 = urllib.request.Request(f"https://api.netlify.com/api/v1/deploys/{deploy_id}",
                    headers={"Authorization":f"Bearer {token}"})
                estado = json.loads(urllib.request.urlopen(req4,timeout=15).read())
                if estado.get("state") == "ready": return site_url
                if estado.get("state") == "error": return None
            except: pass
        return site_url
    except Exception as e:
        print(f"Netlify error: {e}"); return None


# ─── Generador HTML de la ficha ───────────────────────────────────────────────

def generar_html(titulo, precio, ubicacion, descripcion, fotos, detalles, caracteristicas, info_adicional, nombre_agente, whatsapp, url_logo, form_url=""):
    def esc(t):
        if t is None: return ""
        return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;").replace("'","&#39;")
    logo_fallback = url_logo or "https://w7.pngwing.com/pngs/402/497/png-transparent-re-max-llc-estate-agent-re-max-alliance-pender-real-estate-house-house-balloon-logo-property.png"
    while len(fotos) < 3: fotos.append(logo_fallback)
    todas_fotos = "".join(f'<div id="foto-{i}" class="mb-3"><img src="{f}" class="w-full h-auto rounded-xl shadow-lg cursor-pointer hover:opacity-90 transition-all" onclick="window.open(this.src,\'_blank\')" onerror="this.src=\'{logo_fallback}\'" alt="Foto {i+1}"><p class="text-center text-white/30 text-[10px] mt-1">{i+1}/{len(fotos)}</p></div>' for i,f in enumerate(fotos))
    thumbs = "".join(f'<img src="{f}" class="w-full h-full object-cover cursor-pointer rounded hover:opacity-80 transition" onclick="abrirModalFoto({i})" onerror="this.style.display=\'none\'" alt="foto {i+1}">' for i,f in enumerate(fotos[:12]))
    seen={}
    for c in (caracteristicas or []):
        k=c.lower().strip()
        if k not in seen: seen[k]=c
    car=list(seen.values())
    cl=" ".join(car).lower()
    if detalles.get("metros_totales") and "tot" not in cl: car.insert(0,f"{detalles['metros_totales']} m² tot.")
    if detalles.get("metros_cubiertos") and "cub" not in cl: car.insert(1 if detalles.get("metros_totales") else 0,f"{detalles['metros_cubiertos']} m² cub.")
    if detalles.get("ambientes") and "amb" not in cl: car.append(f"{detalles['ambientes']} amb.")
    if detalles.get("banos") and "baño" not in cl and "bano" not in cl: car.append(f"{detalles['banos']} baños")
    car_html = ""
    if car:
        items="".join(f'<li class="flex items-center gap-2 px-3 py-2.5 rounded-lg text-sm" style="background:rgba(0,61,165,.05)"><span class="w-1.5 h-1.5 rounded-full flex-shrink-0" style="background:#DC1C2E"></span><span style="color:#404041">{esc(c)}</span></li>' for c in car[:12])
        car_html=f'<div class="bg-white p-6 rounded-2xl shadow-sm" style="border:1px solid #C4C6C8"><h2 class="text-xs font-bold mb-4 uppercase tracking-[.2em]" style="color:#949CA1">Características</h2><ul class="grid grid-cols-2 md:grid-cols-3 gap-2">{items}</ul></div>'
    info_sb=""
    ii=[]
    if info_adicional.get("antiguedad"): ii.append(f'<div class="flex justify-between"><span class="text-[10px] font-semibold uppercase tracking-wider" style="color:#949CA1">Antigüedad</span><span class="text-xs font-bold" style="color:#404041">{esc(info_adicional["antiguedad"])}</span></div>')
    if info_adicional.get("expensas"): ii.append(f'<div class="flex justify-between"><span class="text-[10px] font-semibold uppercase tracking-wider" style="color:#949CA1">Expensas</span><span class="text-xs font-bold" style="color:#404041">{esc(info_adicional["expensas"])}</span></div>')
    if ii: info_sb=f'<div class="mt-5 pt-5" style="border-top:1px solid #C4C6C8">{"".join(ii)}</div>'
    enc_html=""
    if form_url:
        sep="&" if "?" in form_url else "?"
        enc_html=f'<div class="mt-4"><a href="{form_url}{sep}entry.0={urllib.parse.quote(ubicacion or titulo or "")}" target="_blank" class="block w-full text-center py-3 rounded-xl font-semibold text-sm" style="background:#f0f4ff;color:#1A3668;border:1.5px solid #003DA5">👀 ¿Ya visitaste la propiedad?</a></div>'
    desc_p="".join(f'<p class="mb-3">{esc(p.strip())}</p>' for p in descripcion.split("\n") if p.strip()) or f"<p>{esc(descripcion)}</p>"
    nombre_h=esc(nombre_agente)
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(titulo)}</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
body{{font-family:'Inter',sans-serif;background:#F5F5F0;color:#404041}}
.gradient-precio{{background:linear-gradient(135deg,#003DA5,#DC1C2E);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.modal{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.9);z-index:1000;overflow-y:auto;padding:20px}}
.modal.active{{display:block}}
.desc-col{{max-height:8rem;overflow:hidden}}
.desc-exp{{max-height:none}}
</style></head><body>
<div class="max-w-5xl mx-auto px-4 py-8">
<div class="flex items-center gap-4 mb-6 bg-white p-4 rounded-2xl shadow-sm" style="border:1px solid #C4C6C8">
<img src="{logo_fallback}" class="h-10 object-contain" style="max-width:100px" onerror="this.style.display='none'" alt="logo">
<div><p class="font-bold text-sm" style="color:#1A3668">{nombre_h}</p><p class="text-xs" style="color:#DC1C2E">Asesor Inmobiliario</p></div></div>
<div class="mb-6"><h1 class="text-2xl md:text-3xl font-extrabold mb-1" style="color:#1A3668">{esc(titulo)}</h1>
<p class="text-sm" style="color:#949CA1">{esc(ubicacion)}</p>
<p class="text-3xl font-extrabold gradient-precio mt-2">{esc(precio)}</p></div>
<div class="mb-6 rounded-2xl overflow-hidden cursor-pointer" onclick="abrirModalFoto(0)">
<img src="{fotos[0]}" class="w-full h-72 md:h-96 object-cover" onerror="this.src='{logo_fallback}'" alt="Foto principal"></div>
<div class="grid grid-cols-4 gap-2 mb-8" style="height:90px">{thumbs}</div>
<div class="grid md:grid-cols-3 gap-6">
<div class="md:col-span-2 space-y-6">
<div class="bg-white p-6 rounded-2xl shadow-sm" style="border:1px solid #C4C6C8">
<h2 class="text-xs font-bold mb-4 uppercase tracking-[.2em]" style="color:#949CA1">Descripción</h2>
<div id="dt" class="text-sm leading-relaxed desc-col" style="color:#404041">{desc_p}</div>
<button id="bx" onclick="toggleD()" class="mt-3 text-xs font-semibold hidden" style="color:#003DA5">Ver más ▼</button>
<button id="bc" onclick="toggleD()" class="mt-3 text-xs font-semibold hidden" style="color:#003DA5">Ver menos ▲</button></div>
{car_html}</div>
<div class="md:col-span-1">
<div class="bg-white p-6 rounded-2xl shadow-lg sticky top-6 text-center" style="border:1px solid #C4C6C8;border-top:4px solid #DC1C2E">
<p class="text-[10px] font-semibold uppercase tracking-[.15em]" style="color:#949CA1">Precio</p>
<p class="text-2xl font-extrabold gradient-precio my-3">{esc(precio)}</p>
<div class="my-4 h-px" style="background:linear-gradient(to right,transparent,#C4C6C8,transparent)"></div>
<img src="{logo_fallback}" class="mx-auto mb-3 object-contain" style="max-width:90px;max-height:45px" onerror="this.style.display='none'" alt="logo">
<p class="font-bold text-sm" style="color:#1A3668">{nombre_h}</p>
<p class="text-[10px] font-semibold mb-4 uppercase tracking-wider" style="color:#DC1C2E">Asesor Inmobiliario</p>
<a href="https://wa.me/{whatsapp}" class="block w-full text-white py-3 rounded-xl font-semibold text-sm shadow-md flex items-center justify-center gap-2" style="background:#25D366">
<svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347z"/><path d="M12 0C5.373 0 0 5.373 0 12c0 2.625.846 5.059 2.284 7.034L.789 23.492a.5.5 0 00.611.611l4.458-1.495A11.952 11.952 0 0012 24c6.627 0 12-5.373 12-12S18.627 0 12 0zm0 22c-2.287 0-4.4-.745-6.112-2.008l-.427-.318-3.164 1.061 1.061-3.164-.318-.427A9.935 9.935 0 012 12C2 6.477 6.477 2 12 2s10 4.477 10 10-4.477 10-10 10z"/></svg>
Consultar</a>
{enc_html}{info_sb}</div></div></div>
<div class="mt-10 mb-4 text-center"><div class="h-px mb-4" style="background:linear-gradient(to right,transparent,#C4C6C8,transparent)"></div>
<p class="text-[10px] font-medium" style="color:#C4C6C8">Ficha generada por {nombre_h if nombre_h else 'tu asesor'} &middot; RE/MAX</p></div></div>
<div id="mf" class="modal" onclick="if(event.target===this)cm()"><div class="max-w-2xl mx-auto"><button onclick="cm()" class="fixed top-4 right-4 text-white text-3xl font-bold z-50">&times;</button>{todas_fotos}</div></div>
<script>
function am(){{document.getElementById('mf').classList.add('active');document.body.style.overflow='hidden';}}
function abrirModalFoto(i){{am();setTimeout(()=>{{const el=document.getElementById('foto-'+i);if(el)el.scrollIntoView({{behavior:'smooth',block:'start'}});}},150);}}
function cm(){{document.getElementById('mf').classList.remove('active');document.body.style.overflow='auto';}}
document.addEventListener('keydown',e=>{{if(e.key==='Escape')cm();}});
function toggleD(){{const d=document.getElementById('dt'),bx=document.getElementById('bx'),bc=document.getElementById('bc');if(d.classList.contains('desc-col')){{d.classList.replace('desc-col','desc-exp');bx.classList.add('hidden');bc.classList.remove('hidden');}}else{{d.classList.replace('desc-exp','desc-col');bx.classList.remove('hidden');bc.classList.add('hidden');}}}}
window.addEventListener('DOMContentLoaded',()=>{{const d=document.getElementById('dt'),b=document.getElementById('bx');if(d&&b&&d.scrollHeight>d.clientHeight+20)b.classList.remove('hidden');}});
</script></body></html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
