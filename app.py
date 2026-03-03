import os
import queue
import threading
import urllib.parse
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
from repositories.property_repository import PropertyRepository
from repositories.user_repository import UserRepository
from services.auth_service import AuthService
from services.property_service import PropertyService
from services.scraper_service import ScraperService


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fichas-ia-secret-2024-cambiar-en-produccion")

init_db()

user_repo = UserRepository()
property_repo = PropertyRepository()
auth_service = AuthService(user_repo)
scraper_service = ScraperService()
property_service = PropertyService(property_repo, base_dir=BASE_DIR)

JOBS: dict = {}

def _debug_log(run_id: str, hypothesis_id: str, location: str, message: str, data: dict) -> None:
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
    JOBS[job_id] = {
        "queue": log_queue,
        "status": "running",
        "result_url": None,
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
                if msg == "__ERROR__":
                    yield "event: error\ndata: Error\n\n"
                    break
                yield f"data: {msg.replace(chr(10), ' ')}\n\n"
            except queue.Empty:
                yield "data: trabajando...\n\n"

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

    images = prop.get("image_paths") or []
    if not images:
        placeholder = PropertyService._placeholder_svg_url()
        images = [placeholder] * 5
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


@app.route("/propiedades")
@login_required
def properties_list():
    items = property_repo.list_properties(limit=100)
    return jsonify(items)


@app.route("/api/propiedades/<int:property_id>", methods=["DELETE"])
@login_required
def delete_property(property_id: int):
    deleted = property_service.delete_property(property_id)
    if not deleted:
        return jsonify({"error": "Propiedad no encontrada"}), 404
    return jsonify({"ok": True})


def _merge_features(caracteristicas: list[str], detalles: dict) -> list[str]:
    seen: dict[str, str] = {}
    for c in (caracteristicas or []):
        key = (c or "").lower().strip()
        if key and key not in seen:
            seen[key] = c
    merged = list(seen.values())
    cl = " ".join(merged).lower()
    if detalles.get("ambientes") and "amb" not in cl:
        merged.insert(0, f"{detalles['ambientes']} ambientes")
    if detalles.get("banos") and "ba" not in cl:
        merged.insert(1, f"{detalles['banos']} baños")
    if detalles.get("metros_totales") and "tot" not in cl:
        merged.append(f"{detalles['metros_totales']} m² totales")
    if detalles.get("metros_cubiertos") and "cub" not in cl:
        merged.append(f"{detalles['metros_cubiertos']} m² cubiertos")
    return merged


def _run_generation(job_id, source_url, agent_name, agent_whatsapp, form_url, run_id: str | None = None):
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
        log(f"Error: {e}")
        job["status"] = "error"
        q.put("__ERROR__")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
