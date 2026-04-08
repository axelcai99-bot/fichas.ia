import os
import ipaddress
import socket
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
from repositories.interest_repository import InterestRepository
from repositories.property_repository import PropertyRepository
from repositories.user_repository import UserRepository
from services.auth_service import AuthService
from services.client_service import sanitize_client_payload
from services.property_service import PropertyService
from services.scraper_service import ScraperService


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
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


@app.teardown_appcontext
def _close_db(exc):
    from flask import g
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.after_request
def _set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

user_repo = UserRepository()
property_repo = PropertyRepository()
client_repo = ClientRepository()
interest_repo = InterestRepository()
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
_JOB_HARD_TIMEOUT_SECONDS = 180


def _cleanup_stale_jobs():
    now = time.time()
    with _jobs_lock:
        stale = [k for k, v in JOBS.items() if now - v.get("created_at", now) > _JOB_TTL_SECONDS]
        for k in stale:
            JOBS.pop(k, None)


def get_user(username: str):
    return user_repo.get_user(username)


_MAP_LATITUDE_KEYS = ("latitude", "latitud", "lat")
_MAP_LONGITUDE_KEYS = ("longitude", "longitud", "lng", "lon")


def _split_description_parts(description: str) -> list[str]:
    normalized_description = description or ""
    parts = [part.strip() for part in re.split(r"\n{2,}", normalized_description) if part.strip()]
    return parts or [normalized_description]


def _parse_coord_from_sources(keys: tuple[str, ...], *sources: dict | None) -> float | None:
    for source in sources:
        if not source:
            continue
        for key in keys:
            value = source.get(key)
            if value in (None, ""):
                continue
            try:
                return float(str(value).strip().replace(",", "."))
            except (TypeError, ValueError):
                continue
    return None


def _normalize_map_query(location: str) -> str:
    value = re.sub(r"\s+", " ", (location or "").strip(" ,"))
    if not value:
        return ""
    value = re.sub(r"\bal\s+(\d{3,5})\b", r" \1", value, flags=re.I)
    parts = [part.strip(" ,") for part in value.split(",") if part.strip(" ,")]
    if len(parts) >= 4 and "argentina" in parts[2].lower():
        parts = [parts[0], parts[3], parts[1], parts[2]]
    return ", ".join(dict.fromkeys(parts))


def _build_google_embed(query: str, zoom: int = 16) -> str:
    return (
        "https://maps.google.com/maps?"
        f"hl=es&q={urllib.parse.quote(query)}&z={zoom}&ie=UTF8&iwloc=B&output=embed"
    )


def _resolve_property_images(prop: dict) -> list[str]:
    image_paths = prop.get("image_paths") or []
    source_image_urls = prop.get("source_image_urls") or []

    # Preferir URLs originales del portal (sobreviven redeploys).
    # Solo usar archivos locales si no hay URLs originales.
    if source_image_urls:
        images = source_image_urls
    elif image_paths:
        images = image_paths
    else:
        images = [PropertyService._placeholder_svg_url()]

    referer_url = prop.get("source_url", "")
    return [_build_image_src(image, referer_url) for image in images]


def _build_property_map_context(prop: dict, detalles: dict, info_adicional: dict) -> tuple[str, str, str]:
    latitude = _parse_coord_from_sources(_MAP_LATITUDE_KEYS, prop, detalles, info_adicional)
    longitude = _parse_coord_from_sources(_MAP_LONGITUDE_KEYS, prop, detalles, info_adicional)
    map_location_label = (prop.get("ubicacion") or "").strip()
    map_embed_url = ""
    maps_url = ""

    if latitude is not None and longitude is not None:
        coords_query = f"{latitude},{longitude}"
        maps_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(coords_query)}"
        map_embed_url = _build_google_embed(coords_query)
        if not map_location_label:
            map_location_label = coords_query
        return map_embed_url, maps_url, map_location_label

    map_query = _normalize_map_query(map_location_label)
    if map_query and map_query.lower() != "ver en el portal":
        maps_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(map_query)}"
        map_embed_url = _build_google_embed(map_query)

    return map_embed_url, maps_url, map_location_label


def _is_private_hostname(hostname: str) -> bool:
    host = (hostname or "").strip().strip(".")
    if not host:
        return True
    lowered = host.lower()
    blocked_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
    if lowered in blocked_hosts or lowered.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_unspecified
            or ip.is_reserved
            or ip.is_multicast
        )
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True

    for info in infos:
        candidate = info[4][0]
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            return True
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_unspecified
            or ip.is_reserved
            or ip.is_multicast
        ):
            return True
    return False


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
    username = session["username"]
    user = get_user(username)
    data = request.json or {}
    url_prop = data.get("url", "").strip()
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
        args=(job_id, url_prop, nombre, whatsapp, form_url),
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

    images = _resolve_property_images(prop)

    descripcion = prop.get("descripcion", "") or ""
    descripcion_parts = _split_description_parts(descripcion)
    wa_msg = urllib.parse.quote(
        f"Hola {prop.get('agent_name', '')}, te contacto por la propiedad: {prop.get('titulo', '')} - {prop.get('ubicacion', '')}"
    )
    survey_url = ""
    form_url = prop.get("form_url", "")
    if form_url:
        sep = "&" if "?" in form_url else "?"
        survey_url = f"{form_url}{sep}entry.0={urllib.parse.quote(prop.get('ubicacion') or prop.get('titulo') or '')}"

    detalles = prop.get("detalles", {}) or {}
    info_adicional = prop.get("info_adicional", {}) or {}
    map_embed_url, maps_url, map_location_label = _build_property_map_context(prop, detalles, info_adicional)

    is_owner = session.get("username") == prop.get("owner_username")
    return render_template(
        "property_detail.html",
        prop=prop,
        images=images,
        detalles=detalles,
        info_adicional=info_adicional,
        descripcion_parts=descripcion_parts,
        wa_msg=wa_msg,
        survey_url=survey_url,
        map_embed_url=map_embed_url,
        maps_url=maps_url,
        map_location_label=map_location_label,
        inicial=(prop.get("agent_name") or "A")[0].upper(),
        is_owner=is_owner,
    )


@app.route("/proxy-image")
def proxy_image():
    image_url = (request.args.get("url") or "").strip()
    referer_url = (request.args.get("referer") or "").strip()
    if not re.match(r"^https?://", image_url, re.I):
        abort(400)
    parsed = urllib.parse.urlparse(image_url)
    if not parsed.hostname or _is_private_hostname(parsed.hostname):
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


@app.route("/propiedades")
@login_required
def properties_list():
    username = session["username"]
    page = max(1, int(request.args.get("page") or 1))
    per_page = min(100, max(1, int(request.args.get("per_page") or 20)))
    search = (request.args.get("q") or "").strip()
    portal = (request.args.get("portal") or "").strip()
    result = property_repo.list_properties(
        limit=per_page, offset=(page - 1) * per_page,
        owner_username=username,
        source_portal=portal or None,
        search=search,
    )
    return jsonify(result)


@app.route("/api/propiedades/<int:property_id>/tags", methods=["PUT"])
@login_required
@csrf_protect
def update_property_tags(property_id: int):
    username = session["username"]
    data = request.json or {}
    tags = [str(t).strip()[:50] for t in (data.get("tags") or []) if str(t).strip()][:10]
    ok = property_repo.update_tags(property_id, username, tags)
    if not ok:
        return jsonify({"error": "Propiedad no encontrada"}), 404
    return jsonify({"ok": True})


@app.route("/api/propiedades/<int:property_id>", methods=["DELETE"])
@login_required
@csrf_protect
def delete_property(property_id: int):
    username = session["username"]
    deleted = property_repo.soft_delete_property(property_id, owner_username=username)
    if not deleted:
        return jsonify({"error": "Propiedad no encontrada"}), 404
    return jsonify({"ok": True})


@app.route("/api/propiedades", methods=["DELETE"])
@login_required
@csrf_protect
def delete_all_properties():
    username = session["username"]
    deleted_count = property_repo.soft_delete_all_properties(owner_username=username)
    return jsonify({"ok": True, "deleted_count": deleted_count})


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
    try:
        deleted_properties = property_repo.list_deleted_properties(owner_username=username)
        deleted_count = 0
        for prop in deleted_properties:
            if property_service.delete_property(prop["id"], owner_username=username):
                deleted_count += 1
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, "deleted_count": deleted_count})


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
    ok, payload_or_msg = sanitize_client_payload(data)
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
    ok, payload_or_msg = sanitize_client_payload(data)
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


@app.route("/api/clientes/<int:client_id>/actividad", methods=["GET"])
@login_required
def list_client_activity(client_id: int):
    username = session["username"]
    return jsonify(client_repo.list_activities(client_id, username))


@app.route("/api/clientes/<int:client_id>/actividad", methods=["POST"])
@login_required
@csrf_protect
def add_client_activity(client_id: int):
    username = session["username"]
    data = request.json or {}
    tipo = (data.get("tipo") or "nota").strip()
    texto = (data.get("texto") or "").strip()[:1000]
    if tipo not in {"nota", "llamada", "visita", "whatsapp"}:
        tipo = "nota"
    if not texto:
        return jsonify({"error": "Texto requerido"}), 400
    activity_id = client_repo.add_activity(client_id, username, tipo, texto)
    if not activity_id:
        return jsonify({"error": "Cliente no encontrado"}), 404
    return jsonify({"ok": True, "id": activity_id})


@app.route("/api/clientes/papelera/vaciar", methods=["DELETE"])
@login_required
@csrf_protect
def empty_trash_clients():
    username = session["username"]
    client_repo.empty_trash(username)
    return jsonify({"ok": True})


# ── Client-Property Interests ──────────────────
@app.route("/api/intereses", methods=["POST"])
@login_required
@csrf_protect
def add_interest():
    username = session["username"]
    data = request.json or {}
    try:
        client_id = int(data.get("client_id"))
        property_id = int(data.get("property_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "IDs inválidos"}), 400
    nota = (data.get("nota") or "").strip()[:500]
    if not client_id or not property_id:
        return jsonify({"error": "Faltan client_id o property_id"}), 400
    ok = interest_repo.add(client_id, property_id, username, nota)
    if not ok:
        return jsonify({"error": "Cliente o propiedad no encontrados"}), 404
    return jsonify({"ok": True})


@app.route("/api/intereses", methods=["DELETE"])
@login_required
@csrf_protect
def remove_interest():
    data = request.json or {}
    try:
        client_id = int(data.get("client_id"))
        property_id = int(data.get("property_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "IDs inválidos"}), 400
    interest_repo.remove(client_id, property_id, session["username"])
    return jsonify({"ok": True})


@app.route("/api/intereses/cliente/<int:client_id>")
@login_required
def interests_by_client(client_id: int):
    return jsonify(interest_repo.by_client(client_id, session["username"]))


@app.route("/api/intereses/propiedad/<int:property_id>")
@login_required
def interests_by_property(property_id: int):
    return jsonify(interest_repo.by_property(property_id, session["username"]))


def _build_image_src(image: str, referer_url: str) -> str:
    value = (image or "").strip()
    if not re.match(r"^https?://", value, re.I):
        return value
    query = urllib.parse.urlencode({"url": value, "referer": referer_url or ""})
    return f"/proxy-image?{query}"


def _run_generation(job_id, source_url, agent_name, agent_whatsapp, form_url):
    with _jobs_lock:
        job = JOBS[job_id]
    q = job["queue"]
    started_at = time.time()

    def log(msg: str):
        q.put(msg)

    def ensure_not_timed_out(stage: str) -> None:
        if time.time() - started_at > _JOB_HARD_TIMEOUT_SECONDS:
            raise TimeoutError(
                f"El proceso superó el límite de {_JOB_HARD_TIMEOUT_SECONDS} segundos durante {stage}."
            )

    try:
        cached = property_service.property_repo.find_by_source_url(source_url)
        if cached:
            log("Esta URL ya fue procesada anteriormente. Usando datos en caché (sin re-scrapear)...")
            property_id = property_service.save_from_cache(
                source_url=source_url,
                owner_username=job.get("user", "admin"),
                agent_name=agent_name or "Asesor",
                agent_whatsapp=agent_whatsapp or "",
                form_url=form_url or "",
                cached=cached,
                log=log,
            )
        else:
            log("Iniciando scraping de la publicación...")
            ensure_not_timed_out("inicio")
            scraped = scraper_service.scrape_property(source_url, log)
            ensure_not_timed_out("scraping")
            log("Scraping listo. Guardando propiedad e imágenes...")
            property_id = property_service.save_scraped_property(
                source_url=source_url,
                owner_username=job.get("user", "admin"),
                agent_name=agent_name or "Asesor",
                agent_whatsapp=agent_whatsapp or "",
                form_url=form_url or "",
                scraped=scraped,
                log=log,
            )
        ensure_not_timed_out("guardado")
        prop_data = property_repo.get_property(property_id)
        token = prop_data.get("public_token") if prop_data else None
        job["result_url"] = f"/p/{token}" if token else f"/propiedad/{property_id}"
        job["status"] = "done"
        log("Proceso completado")
        q.put("__DONE__")
    except Exception as e:
        friendly_error = _format_error_message(e)
        log(f"Error: {friendly_error}")
        job["status"] = "error"
        job["error_message"] = friendly_error
        q.put("__ERROR__")



@app.route("/p/<token>")
def public_property(token: str):
    prop = property_repo.find_by_token(token)
    if not prop:
        abort(404)
    return property_detail(prop["id"])



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
