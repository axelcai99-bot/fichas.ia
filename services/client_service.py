import re

from repositories.client_repository import (
    VALID_CLIENT_TYPES,
    VALID_CLIENT_ESTADOS,
    VALID_CLIENT_ACCIONES,
    VALID_CLIENT_ZONAS,
)


def _normalize_presupuesto(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return ""
    rev = digits[::-1]
    chunks = [rev[i:i + 3] for i in range(0, len(rev), 3)]
    return ".".join(c[::-1] for c in chunks[::-1])


def _parse_bounded_int(value, minimum: int = 1, maximum: int = 10) -> int | None:
    if value is None:
        return None
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return None


def _normalize_select_values(raw_values, allowed_values: set[str]) -> list[str]:
    if isinstance(raw_values, str):
        raw_values = [item.strip().lower() for item in raw_values.split(",") if item.strip()]
    if not isinstance(raw_values, list):
        return []
    return [value for value in raw_values if value in allowed_values]


def sanitize_client_payload(data: dict) -> tuple[bool, dict | str]:
    nombre = re.sub(r"\s+", " ", (data.get("nombre") or "").strip())
    telefono = re.sub(r"\D", "", data.get("telefono") or "")
    presupuesto = _normalize_presupuesto(data.get("presupuesto") or "")
    notas_resumidas = (data.get("notas_resumidas") or "").strip()

    if not nombre:
        return False, "Nombre requerido"
    if not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ .'-]{2,80}", nombre):
        return False, "Nombre solo letras y espacios"
    if not telefono:
        return False, "Teléfono requerido"
    if len(telefono) < 8 or len(telefono) > 15:
        return False, "Teléfono inválido"

    estado = (data.get("estado") or "nuevo_lead").strip().lower()
    if estado not in VALID_CLIENT_ESTADOS:
        estado = "nuevo_lead"

    proxima_accion = (data.get("proxima_accion") or "").strip().lower()
    if proxima_accion not in VALID_CLIENT_ACCIONES:
        proxima_accion = ""
    proxima_accion_fecha = (data.get("proxima_accion_fecha") or "").strip()
    proxima_accion_nota = re.sub(r"\s+", " ", (data.get("proxima_accion_nota") or "").strip())[:250]

    tipos = _normalize_select_values(data.get("tipos", []), VALID_CLIENT_TYPES)

    ambientes_min = _parse_bounded_int(data.get("ambientes_min"))
    ambientes_max = _parse_bounded_int(data.get("ambientes_max"))
    if ambientes_min and ambientes_max and ambientes_min > ambientes_max:
        ambientes_min, ambientes_max = ambientes_max, ambientes_min

    apto_credito_raw = data.get("apto_credito")
    if isinstance(apto_credito_raw, bool):
        apto_credito_value = "si" if apto_credito_raw else "no"
    else:
        apto_credito_value = str(apto_credito_raw or "").strip().lower()
    if apto_credito_value not in {"si", "no", "indiferente"}:
        apto_credito_value = "indiferente"

    zonas = _normalize_select_values(data.get("zonas", []), VALID_CLIENT_ZONAS)

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
        "situacion": estado,
        "estado": estado,
        "proxima_accion": proxima_accion,
        "proxima_accion_fecha": proxima_accion_fecha,
        "proxima_accion_nota": proxima_accion_nota,
        "tipos": tipos,
        "ambientes_min": ambientes_min,
        "ambientes_max": ambientes_max,
        "zonas": zonas,
    }
