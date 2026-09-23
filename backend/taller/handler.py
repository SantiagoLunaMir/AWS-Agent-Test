"""Entradas de Lambda: `api` (HTTP API), `procesar` (worker asíncrono del agente) y `recordatorio` (Scheduler)."""
import hmac
import json
import logging
import re
import time
import uuid
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import boto3
from botocore.config import Config

from . import agenda, agente, config, guard, herramientas, notificaciones, store

logging.getLogger().setLevel(logging.INFO)
log = logging.getLogger(__name__)

RE_SESSION = re.compile(r"^[0-9a-f-]{36}$")
RE_TELEFONO = re.compile(r"^\+?[0-9 ]{8,16}$")
RE_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIPOS_FOTO = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
SEGUNDOS_PENSANDO_MAX = 180


def _ahora() -> datetime:
    return datetime.now(ZoneInfo(config.TZ))


def _resp(status: int, cuerpo: dict | list) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json; charset=utf-8"},
            "body": json.dumps(cuerpo, ensure_ascii=False, default=str)}


def _json(evento: dict) -> dict:
    try:
        return json.loads(evento.get("body") or "{}")
    except json.JSONDecodeError:
        return {}


@lru_cache(maxsize=None)
def _cliente(servicio: str):
    if servicio == "s3":
        return boto3.client("s3", region_name=config.REGION,
                            config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
    return boto3.client(servicio, region_name=config.REGION)


_claves: dict[str, tuple[str, float]] = {}  # parámetro -> (valor, vence)


def _clave(parametro: str) -> str:
    valor, vence = _claves.get(parametro, ("", 0.0))
    if time.time() >= vence:
        valor = _cliente("ssm").get_parameter(Name=parametro, WithDecryption=True)["Parameter"]["Value"]
        _claves[parametro] = (valor, time.time() + config.SEGUNDOS_CACHE_CLAVES)
    return valor


def _autorizado(evento: dict, cabecera: str, parametro: str) -> bool:
    recibida = (evento.get("headers") or {}).get(cabecera, "")
    return bool(parametro) and hmac.compare_digest(recibida.encode(), _clave(parametro).encode())


# =================== API ===================
RUTAS_CHAT = {"POST /chat", "GET /mensajes", "POST /fotos"}


def api(evento, _contexto):
    ruta = evento.get("routeKey", "")
    try:
        if ruta in RUTAS_CHAT and not _autorizado(evento, "x-chat-key", config.CHAT_PARAMETRO):
            return _resp(401, {"error": "Código de acceso inválido."})
        if ruta == "POST /chat":
            return enviar_mensaje(_json(evento))
        if ruta == "GET /mensajes":
            return obtener_mensajes(evento.get("queryStringParameters") or {})
        if ruta == "POST /fotos":
            return url_subida_foto(_json(evento))
        if ruta.startswith(("GET /panel", "POST /panel")):
            if not _autorizado(evento, "x-panel-key", config.PANEL_PARAMETRO):
                return _resp(401, {"error": "Clave del panel inválida."})
            if ruta == "GET /panel/citas":
                return panel_citas(evento.get("queryStringParameters") or {})
            if ruta == "POST /panel/citas/{cita_id}/estado":
                return panel_cambiar_estado(evento["pathParameters"]["cita_id"], _json(evento))
            if ruta == "GET /panel/foto":
                return panel_foto(evento.get("queryStringParameters") or {})
        return _resp(404, {"error": "Ruta no encontrada."})
    except Exception:
        log.exception("Error en %s", ruta)
        return _resp(500, {"error": "Error interno."})


def enviar_mensaje(cuerpo: dict):
    session_id = str(cuerpo.get("session_id", ""))
    texto = str(cuerpo.get("texto", "")).strip()
    foto_id = cuerpo.get("foto_id") or None
    cliente = cuerpo.get("cliente") or {}
    nombre = str(cliente.get("nombre", "")).strip()[:60]
    telefono = str(cliente.get("telefono", "")).strip()

    if not RE_SESSION.match(session_id):
        return _resp(400, {"error": "session_id inválido."})
    if not nombre or not RE_TELEFONO.match(telefono):
        return _resp(400, {"error": "Nombre y teléfono son obligatorios."})
    if not texto and not foto_id:
        return _resp(400, {"error": "Mensaje vacío."})
    if len(texto) > config.MAX_TEXTO:
        return _resp(400, {"error": f"El mensaje supera {config.MAX_TEXTO} caracteres."})
    if foto_id and not herramientas.RE_FOTO_ID.match(str(foto_id)):
        return _resp(400, {"error": "foto_id inválido."})

    conv = store.obtener_conversacion(session_id)
    ahora = int(time.time())
    if conv["estado"] == "pensando" and ahora - conv["estado_ts"] < SEGUNDOS_PENSANDO_MAX:
        return _resp(409, {"error": "El agente aún está respondiendo."})
    turnos = [t for t in conv["turnos"] if ahora - t < config.VENTANA_TURNOS_SEG] + [ahora]
    if len(turnos) > config.MAX_TURNOS_POR_VENTANA:
        return _resp(429, {"error": "Demasiados mensajes. Espera unos minutos."})

    telefono = telefono.replace(" ", "")
    store.iniciar_turno(session_id, turnos, {"nombre": nombre, "telefono": telefono})
    mensaje = store.agregar_mensaje(session_id, "cliente", texto,
                                    f"fotos/{session_id}/{foto_id}" if foto_id else None)
    _cliente("lambda").invoke(
        FunctionName=config.WORKER_FUNCTION,
        InvocationType="Event",
        Payload=json.dumps({"session_id": session_id, "texto": texto, "foto_id": foto_id}).encode(),
    )
    return _resp(202, {"ok": True, "sk": mensaje["sk"]})


def obtener_mensajes(params: dict):
    session_id = params.get("session_id", "")
    if not RE_SESSION.match(session_id):
        return _resp(400, {"error": "session_id inválido."})
    conv = store.obtener_conversacion(session_id)
    return _resp(200, {"estado": conv["estado"], "mensajes": store.listar_mensajes(session_id, params.get("desde"))})


def url_subida_foto(cuerpo: dict):
    session_id = str(cuerpo.get("session_id", ""))
    ext = TIPOS_FOTO.get(cuerpo.get("content_type", ""))
    if not RE_SESSION.match(session_id) or not ext:
        return _resp(400, {"error": "Solicitud inválida (solo JPG, PNG o WEBP)."})
    foto_id = f"{uuid.uuid4().hex}.{ext}"
    post = _cliente("s3").generate_presigned_post(
        Bucket=config.BUCKET_FOTOS,
        Key=f"fotos/{session_id}/{foto_id}",
        Fields={"Content-Type": cuerpo["content_type"]},
        Conditions=[{"Content-Type": cuerpo["content_type"]},
                    ["content-length-range", 1, herramientas.MAX_BYTES_FOTO]],
        ExpiresIn=300,
    )
    return _resp(200, {"foto_id": foto_id, "url": post["url"], "fields": post["fields"]})


# =================== Panel del taller ===================
def panel_citas(params: dict):
    fecha = params.get("fecha") or _ahora().date().isoformat()
    if not RE_FECHA.match(fecha):
        return _resp(400, {"error": "Fecha inválida."})
    citas = sorted(store.citas_por_fecha(fecha), key=lambda c: c["hora"])
    return _resp(200, {"fecha": fecha, "citas": citas, "mecanicos": agenda.MECANICOS,
                       "horarios": agenda.slots_del_dia(datetime.fromisoformat(fecha).date())})


def panel_cambiar_estado(cita_id: str, cuerpo: dict):
    nuevo = cuerpo.get("estado")
    if nuevo not in ("completada", "cancelada"):
        return _resp(400, {"error": "Estado inválido."})
    cita = store.obtener_cita(cita_id)
    if not cita or cita.get("estado") != "confirmada":
        return _resp(409, {"error": "La cita no está activa."})
    if nuevo == "cancelada":
        herramientas.cancelar(cita)
        store.agregar_mensaje(cita["session_id"], "agente",
                              f"Hola {cita['nombre']}, el taller tuvo que *cancelar* tu cita {cita_id} del "
                              f"{agenda.fecha_legible(cita['fecha'])} a las {cita['hora']}. "
                              "Escríbeme y te ayudo a reagendar.")
    else:
        store.actualizar_estado_cita(cita_id, "completada")
        notificaciones.cancelar_recordatorio(cita_id)
    return _resp(200, {"ok": True})


def panel_foto(params: dict):
    key = params.get("key", "")
    if not key.startswith("fotos/") or ".." in key:
        return _resp(400, {"error": "Llave inválida."})
    url = _cliente("s3").generate_presigned_url("get_object", Params={"Bucket": config.BUCKET_FOTOS, "Key": key},
                                                ExpiresIn=300)
    return _resp(200, {"url": url})


# =================== Worker del agente ===================
def procesar(evento, _contexto):
    session_id = evento["session_id"]
    try:
        conv = store.obtener_conversacion(session_id)
        texto = evento.get("texto", "")
        bloqueado, salida_guard = guard.revisar(texto, "INPUT")
        if bloqueado:
            respuesta = salida_guard
        else:
            ctx = herramientas.Contexto(conv=conv, ahora=_ahora())
            respuesta = agente.responder(ctx, texto, evento.get("foto_id"))
            _, respuesta = guard.revisar(respuesta, "OUTPUT")
        store.agregar_mensaje(session_id, "agente", respuesta)
        conv["estado"] = "listo"
        conv["estado_ts"] = int(time.time())
        store.guardar_conversacion(conv)
    except Exception:
        log.exception("Falló el turno de %s", session_id)
        store.agregar_mensaje(session_id, "agente",
                              "Tuve un problema técnico. ¿Me escribes de nuevo en un momento, por favor?")
        store.marcar_estado(session_id, "listo")


# =================== Recordatorios ===================
def recordatorio(evento, _contexto):
    cita = store.obtener_cita(evento["cita_id"])
    if not cita or cita.get("estado") != "confirmada":
        log.info("Recordatorio omitido para %s", evento["cita_id"])
        return
    store.agregar_mensaje(
        cita["session_id"], "agente",
        f"⏰ *Recordatorio*: {cita['nombre']}, tu cita {cita['cita_id']} es el "
        f"{agenda.fecha_legible(cita['fecha'])} a las "
        f"{cita['hora']} para tu {cita['marca']} {cita['modelo']}. Te atiende {cita['mecanico_nombre']}. "
        "Si necesitas cancelar, responde aquí.")
    notificaciones.enviar_correo(cita.get("email"), f"Recordatorio de tu cita {cita['cita_id']}",
                                 notificaciones.texto_cita(cita))
    store.marcar_recordatorio_enviado(cita["cita_id"])
