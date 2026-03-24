import os
import queue
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import json
import time
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
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32).hex()

init_db()

user_repo = UserRepository()
property_repo = PropertyRepository()
client_repo = ClientRepository()
auth_service = AuthService(user_repo)
scraper_service = ScraperService()
property_service = PropertyService(property_repo, base_dir=BASE_DIR)

JOBS: dict = {}
_jobs_lock = threading.Lock()
VALID_CLIENT_TYPES = {"ph", "casa", "depto", "otro"}
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
        "sessionId": "5ab736",
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    log_path = os.path.join(BASE_DIR, "debug-5ab736.log")
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
    tipo_raw = (data.get("tipo") or "").strip().lower()
    ambientes_raw = (data.get("ambientes") or "").strip()
    zonas_raw = (data.get("zonas_busqueda") or "").strip()
    notas_resumidas = (data.get("notas_resumidas") or "").strip()
    situacion = (data.get("situacion") or "").strip()

    if not nombre:
        return False, "Nombre requerido"
    if not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ ]{2,80}", nombre):
        return False, "Nombre solo letras y espacios"
    if not telefono:
        return False, "Teléfono requerido"
    if len(telefono) < 8 or len(telefono) > 15:
        return False, "Teléfono inválido"
    tipos = [t.strip().lower() for t in tipo_raw.split(",") if t.strip()]
    if not tipos:
        return False, "Seleccioná al menos un tipo"
    for t in tipos:
        if t not in VALID_CLIENT_TYPES:
            return False, f"Tipo inválido: {t}"

    ambientes_list: list[str] = []
    if ambientes_raw:
        ambientes_list = [a.strip() for a in ambientes_raw.split(",") if a.strip()]
        if not ambientes_list:
            return False, "Ambientes inválido"
        for a in ambientes_list:
            if not a.isdigit():
                return False, "Ambientes debe ser numérico"
            if not (1 <= int(a) <= 10):
                return False, "Ambientes debe estar entre 1 y 10"

    zonas = [z.strip().lower() for z in zonas_raw.split(",") if z.strip()]
    if not zonas:
        return False, "Seleccioná al menos una zona"
    for z in zonas:
        if z not in VALID_CLIENT_ZONAS:
            return False, f"Zona inválida: {z}"

    return True, {
        "nombre": nombre,
        "telefono": telefono,
        "presupuesto": presupuesto,
        "tipo": ", ".join(dict.fromkeys(tipos)),
        "ambientes": ", ".join(dict.fromkeys(ambientes_list)),
        "apto_credito": bool(data.get("apto_credito")),
        "zonas_busqueda": ", ".join(zonas),
        "notas_resumidas": notas_resumidas,
        "situacion": situacion,
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
        user = auth_service.validate_login(username, password)
        if user:
            session["username"] = username
            session["role"] = user.get("role", "user")
            return redirect(url_for("dashboard"))
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

    job_id = uuid.uuid4().hex
    log_queue = queue.Queue()
    with _jobs_lock:
        JOBS[job_id] = {
            "queue": log_queue,
            "status": "running",
            "result_url": None,
            "error_message": None,
            "user": username,
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
@login_required
def property_detail(property_id: int):
    prop = property_repo.get_property(property_id)
    if not prop:
        abort(404)

    image_paths = prop.get("image_paths") or []
    source_image_urls = prop.get("source_image_urls") or []

    # Si sólo hay 1 imagen local (descarga probablemente fallida) y tenemos
    # URLs originales del scraping, usamos esas como fallback directo.
    if len(image_paths) <= 1 and len(source_image_urls) > 1:
        images = source_image_urls
    elif image_paths:
        images = image_paths
    else:
        images = []

    if not images:
        placeholder = PropertyService._placeholder_svg_url()
        images = [placeholder] * 5
    images = [_build_image_src(image, prop.get("source_url", "")) for image in images]
    while len(images) < 5:
        images.append(images[-1])

    merged_features = _merge_features(prop.get("caracteristicas", []), prop.get("detalles", {}))
    descripcion = prop.get("descripcion", "") or ""
    descripcion_parts = [p.strip() for p in descripcion.split("\n") if p.strip()] or [descripcion]
    wa_msg = urllib.parse.quote(
        f"Hola {prop.get('agent_name', '')}, te contacto por la propiedad: {prop.get('titulo', '')} - {prop.get('ubicacion', '')}"
    )
    survey_url = ""
    form_url = prop.get("form_url", "")
    if form_url:
        sep = "&" if "?" in form_url else "?"
        survey_url = f"{form_url}{sep}entry.0={urllib.parse.quote(prop.get('ubicacion') or prop.get('titulo') or '')}"

    return render_template(
        "property_detail.html",
        prop=prop,
        images=images,
        merged_features=merged_features[:10],
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
    portal = (request.args.get("portal") or "").strip().lower()
    allowed = {"zonaprop", "argenprop", "mercadolibre"}
    source_portal = portal if portal in allowed else None
    items = property_repo.list_properties(limit=100, owner_username=username, source_portal=source_portal)
    return jsonify(items)


@app.route("/api/propiedades/<int:property_id>", methods=["DELETE"])
@login_required
def delete_property(property_id: int):
    username = session["username"]
    deleted = property_service.delete_property(property_id, owner_username=username)
    if not deleted:
        return jsonify({"error": "Propiedad no encontrada"}), 404
    return jsonify({"ok": True})


@app.route("/api/clientes", methods=["GET"])
@login_required
def list_clients():
    username = session["username"]
    return jsonify(client_repo.list_clients(owner_username=username))


@app.route("/api/clientes", methods=["POST"])
@login_required
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
def delete_client(client_id: int):
    username = session["username"]
    ok = client_repo.delete_client(client_id=client_id, owner_username=username)
    if not ok:
        return jsonify({"error": "Cliente no encontrado"}), 404
    return jsonify({"ok": True})


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
