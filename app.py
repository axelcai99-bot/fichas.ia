import os
from dotenv import load_dotenv
load_dotenv()  # Cargar variables de entorno desde .env

import queue
import re
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import json
import time
from collections import defaultdict
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    stream_with_context,
    url_for,
)

from db import init_db
from repositories.client_repository import ClientRepository
from repositories.property_repository import PropertyRepository
from repositories.user_repository import UserRepository
from services.auth_service import AuthService
from services.property_service import PropertyService
from services.scraper_service import ScraperService


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
_secret = os.environ.get("SECRET_KEY", "").strip()
if not _secret:
    import warnings
    _secret = os.urandom(32).hex()
    warnings.warn(
        "SECRET_KEY no configurado — sesiones se perderán al reiniciar. "
        "Configurá SECRET_KEY en las variables de entorno para producción.",
        stacklevel=1,
    )
app.secret_key = _secret

init_db()


@app.after_request
def _set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

user_repo = UserRepository()
property_repo = PropertyRepository()
client_repo = ClientRepository()
auth_service = AuthService(user_repo)
scraper_service = ScraperService()
property_service = PropertyService(property_repo, base_dir=BASE_DIR)

# ── CSRF ──────────────────────────────────────
def _get_csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(32)
    return session["_csrf"]


def csrf_protect(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method in ("POST", "PUT", "DELETE"):
            token = request.headers.get("X-CSRF-Token", "")
            if not token and request.is_json:
                token = (request.get_json(silent=True) or {}).get("_csrf", "")
            if not token:
                token = request.form.get("_csrf", "")
            if not token or token != session.get("_csrf"):
                return jsonify({"error": "Token CSRF inválido. Recargá la página."}), 403
        return f(*args, **kwargs)
    return decorated


app.jinja_env.globals["csrf_token"] = _get_csrf_token


# ── Rate limiter (login) ─────────────────────
_login_attempts: dict[str, list[float]] = defaultdict(list)
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 300  # 5 min


def _is_rate_limited(key: str) -> bool:
    now = time.time()
    attempts = _login_attempts[key]
    _login_attempts[key] = [t for t in attempts if now - t < _LOGIN_WINDOW_SECONDS]
    return len(_login_attempts[key]) >= _LOGIN_MAX_ATTEMPTS


def _record_login_attempt(key: str):
    _login_attempts[key].append(time.time())


JOBS: dict = {}
_jobs_lock = threading.Lock()
_JOB_TTL_SECONDS = 600  # 10 min


def _cleanup_stale_jobs():
    now = time.time()
    with _jobs_lock:
        stale = [k for k, v in JOBS.items() if now - v.get("created_at", now) > _JOB_TTL_SECONDS]
        for k in stale:
            JOBS.pop(k, None)
VALID_CLIENT_TYPES = {"depto", "ph", "casa", "lote", "oficina", "otro"}
VALID_CLIENT_ESTADOS = {
    "nuevo_lead", "contactado", "visito_propiedad",
    "negociando", "cerrado", "perdido",
}
VALID_CLIENT_ACCIONES = {
    "", "llamar", "enviar_propiedades", "coordinar_visita", "seguimiento", "esperar_respuesta",
}
VALID_CLIENT_ZONAS = {
    "agronomia", "almagro", "balvanera", "barracas", "belgrano", "boedo", "caballito",
    "chacarita", "coghlan", "colegiales", "constitucion", "flores", "floresta", "la boca",
    "la paternal", "liniers", "mataderos", "monserrat", "monte castro", "nuñez", "nunez",
    "palermo", "parque avellaneda", "parque chacabuco", "parque chas", "parque patricios",
    "puerto madero", "recoleta", "retiro", "saavedra", "san cristobal", "san nicolas",
    "san telmo", "velez sarsfield", "versalles", "villa crespo", "villa del parque",
    "villa devoto", "villa general mitre", "villa lugano", "villa luro", "villa ortuzar",
    "villa pueyrredon", "villa real", "villa riachuelo", "villa santa rita", "villa soldati",
    "villa urquiza", "olivos", "vicente lopez", "la lucila", "martinez", "san isidro",
    "acassuso", "beccar", "munro", "florida", "carapachay", "villa adelina",
}

def _debug_log(run_id: str, hypothesis_id: str, location: str, message: str, data: dict) -> None:
    if not os.environ.get("DEBUG_LOG"):
        return
    payload = {
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    log_path = os.path.join(BASE_DIR, "debug.log")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def get_user(username: str):
    return user_repo.get_user(username)


def _normalize_presupuesto(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return ""
    rev = digits[::-1]
    chunks = [rev[i:i + 3] for i in range(0, len(rev), 3)]
    return ".".join(c[::-1] for c in chunks[::-1])


def _sanitize_client_payload(data: dict) -> tuple[bool, dict | str]:
    nombre = re.sub(r"\s+", " ", (data.get("nombre") or "").strip())
    telefono = re.sub(r"\D", "", data.get("telefono") or "")
    presupuesto = _normalize_presupuesto(data.get("presupuesto") or "")
    notas_resumidas = (data.get("notas_resumidas") or "").strip()

    # ── Level 1: mandatory fields ──
    if not nombre:
        return False, "Nombre requerido"
    if not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ .'-]{2,80}", nombre):
        return False, "Nombre solo letras y espacios"
    if not telefono:
        return False, "Teléfono requerido"
    if len(telefono) < 8 or len(telefono) > 15:
        return False, "Teléfono inválido"

    # ── Pipeline status (enum) ──
    estado = (data.get("estado") or "nuevo_lead").strip().lower()
    if estado not in VALID_CLIENT_ESTADOS:
        estado = "nuevo_lead"

    # ── Next action (enum + optional date) ──
    proxima_accion = (data.get("proxima_accion") or "").strip().lower()
    if proxima_accion not in VALID_CLIENT_ACCIONES:
        proxima_accion = ""
    proxima_accion_fecha = (data.get("proxima_accion_fecha") or "").strip()
    proxima_accion_nota = re.sub(r"\s+", " ", (data.get("proxima_accion_nota") or "").strip())[:250]

    # ── Level 2: optional structured fields ──
    # Property types (array)
    tipos_raw = data.get("tipos", [])
    if isinstance(tipos_raw, str):
        tipos_raw = [t.strip().lower() for t in tipos_raw.split(",") if t.strip()]
    tipos = [t for t in tipos_raw if t in VALID_CLIENT_TYPES]

    # Rooms (min/max)
    ambientes_min = data.get("ambientes_min")
    ambientes_max = data.get("ambientes_max")
    if ambientes_min is not None:
        try:
            ambientes_min = max(1, min(10, int(ambientes_min)))
        except (ValueError, TypeError):
            ambientes_min = None
    if ambientes_max is not None:
        try:
            ambientes_max = max(1, min(10, int(ambientes_max)))
        except (ValueError, TypeError):
            ambientes_max = None
    if ambientes_min and ambientes_max and ambientes_min > ambientes_max:
        ambientes_min, ambientes_max = ambientes_max, ambientes_min
    apto_credito_raw = data.get("apto_credito")
    if isinstance(apto_credito_raw, bool):
        apto_credito_value = "si" if apto_credito_raw else "no"
    else:
        apto_credito_value = str(apto_credito_raw or "").strip().lower()
    if apto_credito_value not in {"si", "no", "indiferente"}:
        apto_credito_value = "indiferente"


    # Zones (array)
    zonas_raw = data.get("zonas", [])
    if isinstance(zonas_raw, str):
        zonas_raw = [z.strip().lower() for z in zonas_raw.split(",") if z.strip()]
    zonas = [z for z in zonas_raw if z in VALID_CLIENT_ZONAS]

    # Legacy compat fields (kept for backward compatibility)
    tipo_legacy = ", ".join(dict.fromkeys(tipos)) if tipos else ""
    zonas_legacy = ", ".join(zonas) if zonas else ""
    ambientes_legacy = ""
    if ambientes_min and ambientes_max:
        ambientes_legacy = f"{ambientes_min}-{ambientes_max}"
    elif ambientes_min:
        ambientes_legacy = str(ambientes_min)

    return True, {
        "nombre": nombre,
        "telefono": telefono,
        "presupuesto": presupuesto,
        "tipo": tipo_legacy,
        "ambientes": ambientes_legacy,
        "apto_credito": apto_credito_value == "si",
        "apto_credito_estado": apto_credito_value,
        "zonas_busqueda": zonas_legacy,
        "notas_resumidas": notas_resumidas,
        "situacion": estado,  # legacy column
        # New structured fields
        "estado": estado,
        "proxima_accion": proxima_accion,
        "proxima_accion_fecha": proxima_accion_fecha,
        "proxima_accion_nota": proxima_accion_nota,
        "tipos": tipos,
        "ambientes_min": ambientes_min,
        "ambientes_max": ambientes_max,
        "zonas": zonas,
    }


def _format_error_message(err: Exception) -> str:
    text = re.sub(r"\s+", " ", str(err or "").strip())
    text = re.sub(r"\s*Stacktrace:.*$", "", text, flags=re.I)
    if not text:
        return "Error inesperado al generar la ficha"
    if len(text) > 220:
        return text[:220].rstrip() + "..."
    return text


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        user = get_user(session["username"])
        if not user or not user.get("active", True):
            session.clear()
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        user = get_user(session["username"])
        if not user or not user.get("active", True):
            session.clear()
            return redirect(url_for("login"))
        if user.get("role") != "admin":
            abort(403)
        return f(*args, **kwargs)

    return decorated


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
        client_ip = request.remote_addr or "unknown"
        rate_key = f"{client_ip}:{username}"
        if _is_rate_limited(rate_key):
            error = "Demasiados intentos. Esperá unos minutos."
        else:
            user = auth_service.validate_login(username, password)
            if user:
                session.clear()
                session["username"] = username
                session["role"] = user.get("role", "user")
                return redirect(url_for("dashboard"))
            _record_login_attempt(rate_key)
            error = "Usuario o contraseña incorrectos"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    username = session["username"]
    user = get_user(username)
    users_all = user_repo.list_users() if user and user.get("role") == "admin" else []
    return render_template(
        "dashboard.html",
        username=username,
        user=user,
        is_admin=(user.get("role") == "admin") if user else False,
        users_all=users_all,
    )


@app.route("/api/perfil", methods=["POST"])
@login_required
@csrf_protect
def guardar_perfil():
    username = session["username"]
    data = request.json or {}
    user_repo.update_profile(
        username,
        nombre=data.get("nombre", "").strip(),
        whatsapp=data.get("whatsapp", "").strip(),
        form_url=data.get("form_url", "").strip(),
    )
    return jsonify({"ok": True})


@app.route("/api/cambiar_password", methods=["POST"])
@login_required
@csrf_protect
def cambiar_password():
    username = session["username"]
    data = request.json or {}
    ok, msg = auth_service.change_password(
        username=username,
        current_pw=data.get("pw_actual", ""),
        new_pw=data.get("pw_nueva", ""),
    )
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"ok": True})


@app.route("/api/admin/usuarios", methods=["GET"])
@admin_required
def listar_usuarios():
    return jsonify(user_repo.list_users())


@app.route("/api/admin/crear_usuario", methods=["POST"])
@admin_required
@csrf_protect
def crear_usuario():
    data = request.json or {}
    ok, payload = auth_service.admin_create_user(
        username=data.get("username", ""),
        password=data.get("password", ""),
        nombre=data.get("nombre", ""),
    )
    if not ok:
        return jsonify({"error": payload}), 400
    return jsonify({"ok": True, "username": payload})


@app.route("/api/admin/toggle_usuario", methods=["POST"])
@admin_required
@csrf_protect
def toggle_usuario():
    data = request.json or {}
    username = data.get("username", "")
    if username == "admin":
        return jsonify({"error": "No podés desactivar al admin"}), 400
    new_active = user_repo.toggle_user(username)
    if new_active is None:
        return jsonify({"error": "Usuario no encontrado"}), 404
    return jsonify({"ok": True, "active": new_active})


@app.route("/api/admin/reset_password", methods=["POST"])
@admin_required
@csrf_protect
def reset_password():
    data = request.json or {}
    ok, msg = auth_service.admin_reset_password(
        username=data.get("username", ""),
        new_pw=data.get("password", ""),
    )
    if not ok:
        status = 404 if msg == "Usuario no encontrado" else 400
        return jsonify({"error": msg}), status
    return jsonify({"ok": True})


@app.route("/api/admin/delete_usuario", methods=["POST"])
@admin_required
@csrf_protect
def delete_usuario():
    data = request.json or {}
    ok, msg = auth_service.admin_delete_user(
        username=data.get("username", ""),
        acting_username=session["username"],
    )
    if not ok:
        status = 404 if msg == "Usuario no encontrado" else 400
        return jsonify({"error": msg}), status
    return jsonify({"ok": True})


@app.route("/api/generar", methods=["POST"])
@login_required
@csrf_protect
def generar():
    run_id = f"run-{int(time.time() * 1000)}"
    username = session["username"]
    user = get_user(username)
    data = request.json or {}
    url_prop = data.get("url", "").strip()
    # region agent log
    _debug_log(
        run_id=run_id,
        hypothesis_id="H6",
        location="app.py:generar",
        message="API generar called",
        data={
            "username": username,
            "has_url": bool(url_prop),
            "base_dir": BASE_DIR,
            "cwd": os.getcwd(),
        },
    )
    # endregion
    if not url_prop:
        return jsonify({"error": "Falta el link"}), 400

    nombre = data.get("nombre", user.get("nombre", "") if user else "").strip()
    whatsapp = data.get("whatsapp", user.get("whatsapp", "") if user else "").strip()
    form_url = data.get("form_url", user.get("form_url", "") if user else "").strip()

    _cleanup_stale_jobs()
    job_id = uuid.uuid4().hex
    log_queue = queue.Queue()
    with _jobs_lock:
        JOBS[job_id] = {
            "queue": log_queue,
            "status": "running",
            "result_url": None,
            "error_message": None,
            "user": username,
            "created_at": time.time(),
        }

    threading.Thread(
        target=_run_generation,
        args=(job_id, url_prop, nombre, whatsapp, form_url, run_id),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/api/stream/<job_id>")
@login_required
def stream(job_id):
    with _jobs_lock:
        job = JOBS.get(job_id)
    if not job or job.get("user") != session["username"]:
        abort(403)

    def generate():
        try:
            while True:
                try:
                    msg = job["queue"].get(timeout=30)
                    if msg == "__DONE__":
                        result = job.get("result_url") or ""
                        yield f"event: done\ndata: {result}\n\n"
                        break
                    if msg == "__ERROR__":
                        error_msg = (job.get("error_message") or "Error inesperado").replace("\n", " ")
                        yield f"event: failed\ndata: {error_msg}\n\n"
                        break
                    yield f"data: {msg.replace(chr(10), ' ')}\n\n"
                except queue.Empty:
                    yield "data: trabajando...\n\n"
        finally:
            with _jobs_lock:
                JOBS.pop(job_id, None)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/propiedad/<int:property_id>")
def property_detail(property_id: int):
    prop = property_repo.get_property(property_id)
    if not prop:
        abort(404)

    image_paths = prop.get("image_paths") or []
    source_image_urls = prop.get("source_image_urls") or []

    # Si sólo hay 1 imagen local (descarga probablemente fallida) y tenemos
    # URLs originales del scraping, usamos esas como fallback directo.
    if source_image_urls and len(image_paths) != len(source_image_urls):
        images = source_image_urls
    elif image_paths:
        images = image_paths
    else:
        images = []

    if not images:
        placeholder = PropertyService._placeholder_svg_url()
        images = [placeholder]
    images = [_build_image_src(image, prop.get("source_url", "")) for image in images]

    descripcion = prop.get("descripcion", "") or ""
    # Dividir por párrafos (doble salto de línea) para respetar la estructura original.
    # Los saltos simples dentro de un párrafo se preservan con white-space:pre-line en el CSS.
    descripcion_parts = [p.strip() for p in re.split(r"\n{2,}", descripcion) if p.strip()] or [descripcion]
    wa_msg = urllib.parse.quote(
        f"Hola {prop.get('agent_name', '')}, te contacto por la propiedad: {prop.get('titulo', '')} - {prop.get('ubicacion', '')}"
    )
    survey_url = ""
    form_url = prop.get("form_url", "")
    if form_url:
        sep = "&" if "?" in form_url else "?"
        survey_url = f"{form_url}{sep}entry.0={urllib.parse.quote(prop.get('ubicacion') or prop.get('titulo') or '')}"

    detalles = prop.get("detalles", {}) or {}

    return render_template(
        "property_detail.html",
        prop=prop,
        images=images,
        detalles=detalles,
        descripcion_parts=descripcion_parts,
        wa_msg=wa_msg,
        survey_url=survey_url,
        inicial=(prop.get("agent_name") or "A")[0].upper(),
    )


@app.route("/proxy-image")
def proxy_image():
    image_url = (request.args.get("url") or "").strip()
    referer_url = (request.args.get("referer") or "").strip()
    if not re.match(r"^https?://", image_url, re.I):
        abort(400)
    parsed = urllib.parse.urlparse(image_url)
    if not parsed.hostname or parsed.hostname in ("localhost", "127.0.0.1", "0.0.0.0") or parsed.hostname.startswith("192.168.") or parsed.hostname.startswith("10.") or parsed.hostname.endswith(".local"):
        abort(400)
    referer_url = re.sub(r"[\r\n]", "", referer_url)

    origin = PropertyService._origin_from_url(referer_url)
    header_sets = [
        PropertyService._image_request_headers(referer_url=referer_url, origin=origin, include_referer=True),
        PropertyService._image_request_headers(referer_url=referer_url, origin=origin, include_referer=False),
    ]

    last_error: Exception | None = None
    for headers in header_sets:
        req = urllib.request.Request(image_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                data = response.read()
                content_type = (response.headers.get_content_type() or "").lower() or "image/jpeg"
            return Response(data, mimetype=content_type, headers={"Cache-Control": "public, max-age=3600"})
        except Exception as exc:
            last_error = exc

    if isinstance(last_error, urllib.error.HTTPError):
        abort(last_error.code)
    abort(502)


@app.route("/api/portales")
@login_required
def portals_list():
    username = session["username"]
    portals = property_repo.list_portals(owner_username=username)
    return jsonify(portals)


@app.route("/propiedades")
@login_required
def properties_list():
    username = session["username"]
    portal = (request.args.get("portal") or "").strip().lower()
    page = max(1, int(request.args.get("page") or 1))
    per_page = min(100, max(1, int(request.args.get("per_page") or 20)))
    available = property_repo.list_portals(owner_username=username)
    source_portal = portal if portal in available else None
    result = property_repo.list_properties(
        limit=per_page, offset=(page - 1) * per_page,
        owner_username=username, source_portal=source_portal,
    )
    return jsonify(result)


@app.route("/api/propiedades/<int:property_id>", methods=["DELETE"])
@login_required
@csrf_protect
def delete_property(property_id: int):
    username = session["username"]
    deleted = property_repo.soft_delete_property(property_id, owner_username=username)
    if not deleted:
        return jsonify({"error": "Propiedad no encontrada"}), 404
    return jsonify({"ok": True})


@app.route("/api/propiedades/<int:property_id>/restaurar", methods=["POST"])
@login_required
@csrf_protect
def restore_property(property_id: int):
    username = session["username"]
    restored = property_repo.restore_property(property_id, owner_username=username)
    if not restored:
        return jsonify({"error": "Propiedad no encontrada en papelera"}), 404
    return jsonify({"ok": True})


@app.route("/api/propiedades/papelera")
@login_required
def trash_properties():
    username = session["username"]
    return jsonify(property_repo.list_deleted_properties(owner_username=username))


@app.route("/api/propiedades/<int:property_id>/eliminar-definitivo", methods=["DELETE"])
@login_required
@csrf_protect
def permanent_delete_property(property_id: int):
    username = session["username"]
    deleted = property_service.delete_property(property_id, owner_username=username)
    if not deleted:
        return jsonify({"error": "Propiedad no encontrada"}), 404
    return jsonify({"ok": True})


@app.route("/api/propiedades/papelera/vaciar", methods=["DELETE"])
@login_required
@csrf_protect
def empty_trash_properties():
    username = session["username"]
    from db import get_connection
    try:
        with get_connection() as conn:
            conn.execute("DELETE FROM properties WHERE owner_username = ? AND deleted_at IS NOT NULL", (username,))
            # Si no quedan propiedades en la tabla, resetear el autoincrement
            row = conn.execute("SELECT COUNT(*) FROM properties").fetchone()
            if row[0] == 0:
                conn.execute("DELETE FROM sqlite_sequence WHERE name='properties'")
            conn.commit()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


@app.route("/api/clientes", methods=["GET"])
@login_required
def list_clients():
    username = session["username"]
    search = (request.args.get("q") or "").strip()
    estado = (request.args.get("estado") or "").strip()
    page = max(1, int(request.args.get("page") or 1))
    per_page = min(100, max(1, int(request.args.get("per_page") or 50)))
    result = client_repo.list_clients(owner_username=username, search=search, estado=estado, limit=per_page, offset=(page - 1) * per_page)
    return jsonify(result)


@app.route("/api/clientes", methods=["POST"])
@login_required
@csrf_protect
def create_client():
    username = session["username"]
    data = request.json or {}
    ok, payload_or_msg = _sanitize_client_payload(data)
    if not ok:
        return jsonify({"error": payload_or_msg}), 400
    client_id = client_repo.create_client(owner_username=username, payload=payload_or_msg)
    return jsonify({"ok": True, "id": client_id})


@app.route("/api/clientes/<int:client_id>", methods=["PUT"])
@login_required
@csrf_protect
def update_client(client_id: int):
    username = session["username"]
    data = request.json or {}
    ok, payload_or_msg = _sanitize_client_payload(data)
    if not ok:
        return jsonify({"error": payload_or_msg}), 400
    ok = client_repo.update_client(client_id=client_id, owner_username=username, payload=payload_or_msg)
    if not ok:
        return jsonify({"error": "Cliente no encontrado"}), 404
    return jsonify({"ok": True})


@app.route("/api/clientes/<int:client_id>", methods=["DELETE"])
@login_required
@csrf_protect
def delete_client(client_id: int):
    username = session["username"]
    ok = client_repo.soft_delete_client(client_id=client_id, owner_username=username)
    if not ok:
        return jsonify({"error": "Cliente no encontrado"}), 404
    return jsonify({"ok": True})


@app.route("/api/clientes/<int:client_id>/restaurar", methods=["POST"])
@login_required
@csrf_protect
def restore_client(client_id: int):
    username = session["username"]
    restored = client_repo.restore_client(client_id=client_id, owner_username=username)
    if not restored:
        return jsonify({"error": "Cliente no encontrado en papelera"}), 404
    return jsonify({"ok": True})


@app.route("/api/clientes/papelera")
@login_required
def trash_clients():
    username = session["username"]
    return jsonify(client_repo.list_deleted_clients(owner_username=username))


@app.route("/api/clientes/<int:client_id>/eliminar-definitivo", methods=["DELETE"])
@login_required
@csrf_protect
def permanent_delete_client(client_id: int):
    username = session["username"]
    deleted = client_repo.delete_client(client_id=client_id, owner_username=username)
    if not deleted:
        return jsonify({"error": "Cliente no encontrado"}), 404
    return jsonify({"ok": True})


@app.route("/api/clientes/papelera/vaciar", methods=["DELETE"])
@login_required
@csrf_protect
def empty_trash_clients():
    username = session["username"]
    from db import get_connection
    try:
        with get_connection() as conn:
            conn.execute("DELETE FROM clients WHERE owner_username = ? AND deleted_at IS NOT NULL", (username,))
            conn.commit()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


# ── Client-Property Interests ──────────────────
@app.route("/api/intereses", methods=["POST"])
@login_required
@csrf_protect
def add_interest():
    username = session["username"]
    data = request.json or {}
    client_id = data.get("client_id")
    property_id = data.get("property_id")
    nota = (data.get("nota") or "").strip()[:500]
    if not client_id or not property_id:
        return jsonify({"error": "Faltan client_id o property_id"}), 400
    from db import get_connection
    now = datetime.now().isoformat()
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO client_property_interests (client_id, property_id, owner_username, nota, created_at) VALUES (?, ?, ?, ?, ?)",
                (client_id, property_id, username, nota, now),
            )
            conn.commit()
    except Exception:
        return jsonify({"error": "Error al vincular"}), 500
    return jsonify({"ok": True})


@app.route("/api/intereses", methods=["DELETE"])
@login_required
@csrf_protect
def remove_interest():
    data = request.json or {}
    client_id = data.get("client_id")
    property_id = data.get("property_id")
    username = session["username"]
    from db import get_connection
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM client_property_interests WHERE client_id = ? AND property_id = ? AND owner_username = ?",
            (client_id, property_id, username),
        )
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/intereses/cliente/<int:client_id>")
@login_required
def interests_by_client(client_id: int):
    username = session["username"]
    from db import get_connection
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT cpi.id, cpi.property_id, cpi.nota, cpi.created_at,
                   p.titulo, p.precio, p.ubicacion
            FROM client_property_interests cpi
            JOIN properties p ON p.id = cpi.property_id
            WHERE cpi.client_id = ? AND cpi.owner_username = ?
            ORDER BY cpi.created_at DESC
            """,
            (client_id, username),
        ).fetchall()
    return jsonify([
        {"id": r["id"], "property_id": r["property_id"], "nota": r["nota"],
         "created_at": r["created_at"], "titulo": r["titulo"],
         "precio": r["precio"], "ubicacion": r["ubicacion"]}
        for r in rows
    ])


@app.route("/api/intereses/propiedad/<int:property_id>")
@login_required
def interests_by_property(property_id: int):
    username = session["username"]
    from db import get_connection
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT cpi.id, cpi.client_id, cpi.nota, cpi.created_at,
                   c.nombre, c.telefono
            FROM client_property_interests cpi
            JOIN clients c ON c.id = cpi.client_id
            WHERE cpi.property_id = ? AND cpi.owner_username = ?
            ORDER BY cpi.created_at DESC
            """,
            (property_id, username),
        ).fetchall()
    return jsonify([
        {"id": r["id"], "client_id": r["client_id"], "nota": r["nota"],
         "created_at": r["created_at"], "nombre": r["nombre"],
         "telefono": r["telefono"]}
        for r in rows
    ])


def _is_detail_feature(feature: str) -> bool:
    """Return True if this feature string came from the structured detalles dict."""
    low = feature.lower()
    return bool(
        re.search(r"m²\s*(tot|cub)", low)
        or re.search(r"\b(amb|dorm)\.", low)
        or re.search(r"\bbaños\b", low)
    )


def _merge_features(caracteristicas: list[str], detalles: dict) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()

    for item in _ordered_detail_features(detalles):
        _append_feature(merged, seen, item)

    for feature in (caracteristicas or []):
        cleaned = _clean_feature_label(feature)
        if not cleaned or _feature_duplicates_details(cleaned, detalles):
            continue
        _append_feature(merged, seen, cleaned)

    return merged


def _build_image_src(image: str, referer_url: str) -> str:
    value = (image or "").strip()
    if not re.match(r"^https?://", value, re.I):
        return value
    query = urllib.parse.urlencode({"url": value, "referer": referer_url or ""})
    return f"/proxy-image?{query}"


def _ordered_detail_features(detalles: dict) -> list[str]:
    ordered: list[str] = []
    mappings = [
        ("metros_totales", lambda value: f"{value} m\u00b2 tot."),
        ("metros_cubiertos", lambda value: f"{value} m\u00b2 cub."),
        ("ambientes", lambda value: f"{value} amb."),
        ("banos", lambda value: f"{value} ba\u00f1os"),
        ("dormitorios", lambda value: f"{value} dorm."),
        ("estado", lambda value: value),
        ("disposicion", lambda value: value),
    ]
    for key, formatter in mappings:
        raw_value = _repair_text((detalles.get(key) or "").strip())
        if not raw_value:
            continue
        ordered.append(formatter(raw_value))
    return ordered


def _append_feature(target: list[str], seen: set[str], value: str) -> None:
    normalized = _repair_text(value).strip()
    if not normalized:
        return
    key = normalized.lower()
    if key in seen:
        return
    seen.add(key)
    target.append(normalized)


def _clean_feature_label(value: str) -> str:
    cleaned = _repair_text(re.sub(r"\s+", " ", (value or "")).replace("\\|", " ")).strip(" -|:.,")
    if len(cleaned) < 2:
        return ""

    normalized = cleaned.lower()
    if normalized in {
        "amb", "amb.", "ambiente", "ambientes",
        "dorm", "dorm.", "dormitorio", "dormitorios",
        "ba\u00f1o", "ba\u00f1os", "bano", "banos",
        "m\u00b2 tot", "m\u00b2 cub", "m2 tot", "m2 cub",
    }:
        return ""
    if re.fullmatch(r"\d+(?:[.,]\d+)?", normalized):
        return ""
    if re.search(r"\b(publicado|actualizado|favorito|compartir|notas personales|ver datos)\b", normalized):
        return ""
    if re.search(r"\bdepartamento\b", normalized) and re.search(r"\bamb", normalized):
        return ""
    if re.search(r"\b(?:av|avenida|calle|pasaje|pje|ruta|boulevard|blvd|bv)\b", normalized) and re.search(r"\d{3,5}", normalized):
        return ""
    if re.search(r"\b(?:capital federal|san crist[oó]bal)\b", normalized):
        return ""
    if re.search(r"\b\d+\s+o\s+m[aá]s\s+ambientes?\b", normalized):
        return ""

    return cleaned


def _feature_duplicates_details(value: str, detalles: dict) -> bool:
    normalized = _repair_text(value).lower()
    detail_patterns = [
        ("metros_totales", f"\\b{re.escape(str(detalles.get('metros_totales') or '').strip())}\\s*m(?:²|2).*(?:tot|total)"),
        ("metros_cubiertos", f"\\b{re.escape(str(detalles.get('metros_cubiertos') or '').strip())}\\s*m(?:²|2).*(?:cub|cubierta)"),
        ("ambientes", f"\\b{re.escape(str(detalles.get('ambientes') or '').strip())}\\s*(?:amb|ambientes?)"),
        ("banos", f"\\b{re.escape(str(detalles.get('banos') or '').strip())}\\s*(?:bañ|ban)"),
        ("dormitorios", f"\\b{re.escape(str(detalles.get('dormitorios') or '').strip())}\\s*dorm"),
    ]
    for key, pattern in detail_patterns:
        if detalles.get(key) and re.search(pattern, normalized):
            return True

    for key in ("estado", "disposicion"):
        detail_value = _repair_text((detalles.get(key) or "").strip()).lower()
        if detail_value and detail_value == normalized:
            return True

    return False


def _repair_text(value: str) -> str:
    text = value or ""
    if any(token in text for token in ("\u00c3", "\u00c2")):
        try:
            repaired = text.encode("latin1").decode("utf-8")
            if repaired:
                text = repaired
        except Exception:
            pass
    return text


def _run_generation(job_id, source_url, agent_name, agent_whatsapp, form_url, run_id: str | None = None):
    with _jobs_lock:
        job = JOBS[job_id]
    q = job["queue"]
    run_id = run_id or f"run-{int(time.time() * 1000)}"

    def log(msg: str):
        q.put(msg)

    try:
        # region agent log
        _debug_log(
            run_id=run_id,
            hypothesis_id="H7",
            location="app.py:_run_generation:start",
            message="Background generation started",
            data={"job_id": job_id, "source_url_prefix": (source_url or "")[:120]},
        )
        # endregion
        scraped = scraper_service.scrape_property(source_url, log)
        property_id = property_service.save_scraped_property(
            source_url=source_url,
            owner_username=job.get("user", "admin"),
            agent_name=agent_name or "Asesor",
            agent_whatsapp=agent_whatsapp or "",
            form_url=form_url or "",
            scraped=scraped,
            log=log,
        )
        job["result_url"] = f"/propiedad/{property_id}"
        job["status"] = "done"
        log("Proceso completado")
        q.put("__DONE__")
    except Exception as e:
        # region agent log
        _debug_log(
            run_id=run_id,
            hypothesis_id="H7",
            location="app.py:_run_generation:except",
            message="Background generation exception",
            data={"error_type": type(e).__name__, "error": str(e)[:300]},
        )
        # endregion
        friendly_error = _format_error_message(e)
        log(f"Error: {friendly_error}")
        job["status"] = "error"
        job["error_message"] = friendly_error
        q.put("__ERROR__")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
