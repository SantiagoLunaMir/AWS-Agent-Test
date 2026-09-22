"""Herramientas del agente. Las reglas críticas se validan aquí, en código, no solo en el prompt."""
import json
import logging
import re
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

import boto3

from . import agenda, config, notificaciones, skills, store, vision

log = logging.getLogger(__name__)

MAX_CITAS_ACTIVAS = 2
MAX_BYTES_FOTO = 5 * 1024 * 1024
RE_FOTO_ID = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp)$")
RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MEDIA_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


class ErrorHerramienta(Exception):
    pass


@dataclass
class Contexto:
    conv: dict
    ahora: datetime

    @property
    def session_id(self) -> str:
        return self.conv["session_id"]

    @property
    def cliente(self) -> dict:
        return self.conv.get("cliente") or {}


def _obj(propiedades: dict, requeridas: list[str] | None = None) -> dict:
    return {"type": "object", "properties": propiedades,
            "required": requeridas if requeridas is not None else list(propiedades), "additionalProperties": False}


def definiciones() -> list[dict]:
    tools = [
        {
            "name": "identificar_vehiculo",
            "description": "Analiza la foto que envió el cliente y devuelve marca, modelo, tipo, color y confianza. "
                           "Úsala cuando el cliente envíe una foto (viene como foto_id en su mensaje).",
            "input_schema": _obj({"foto_id": {"type": "string", "description": "El foto_id tal como aparece en el mensaje."}}),
        },
        {
            "name": "confirmar_vehiculo",
            "description": "Registra la marca y el modelo del vehículo tal como el cliente los dio o los confirmó. "
                           "Si el cliente los escribió él mismo, úsala directo. Si vienen de identificar_vehiculo, "
                           "úsala solo después de que el cliente confirme o corrija.",
            "input_schema": _obj({
                "marca": {"type": "string"},
                "modelo": {"type": "string"},
                "confirmado_por_cliente": {"type": "boolean"},
            }),
        },
        {
            "name": "consultar_disponibilidad",
            "description": "Devuelve los horarios libres de un día (formato AAAA-MM-DD).",
            "input_schema": _obj({"fecha": {"type": "string"}}),
        },
        {
            "name": "agendar_cita",
            "description": "Crea la cita, asigna un mecánico y programa el recordatorio. Requiere el vehículo "
                           "confirmado y que el cliente haya aceptado explícitamente la fecha y hora.",
            "input_schema": _obj({
                "fecha": {"type": "string", "description": "AAAA-MM-DD"},
                "hora": {"type": "string", "description": "HH:00"},
                "servicio": {"type": "string", "enum": agenda.SERVICIOS},
                "descripcion": {"type": "string", "description": "Síntoma o detalle en una frase."},
                "email": {"type": "string", "description": "Correo para la confirmación, o cadena vacía."},
                "horario_confirmado_por_cliente": {"type": "boolean"},
            }),
        },
        {
            "name": "mis_citas",
            "description": "Lista las citas activas del cliente de esta conversación.",
            "input_schema": _obj({}),
        },
        {
            "name": "cancelar_cita",
            "description": "Cancela una cita del cliente. Pide confirmación explícita antes de llamarla.",
            "input_schema": _obj({"cita_id": {"type": "string"}, "confirmado_por_cliente": {"type": "boolean"}}),
        },
        {
            "name": "cargar_skill",
            "description": "Carga las instrucciones completas de una skill del catálogo cuando la tarea lo requiera.",
            "input_schema": _obj({"nombre": {"type": "string", "enum": sorted(skills.catalogo())}}),
        },
    ]
    for t in tools:
        t["strict"] = True
    return tools


@lru_cache(maxsize=1)
def _s3():
    return boto3.client("s3", region_name=config.REGION)


def _nuevo_id_cita() -> str:
    return "C-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))


# ---------------- implementaciones ----------------
def identificar_vehiculo(ctx: Contexto, foto_id: str) -> dict:
    if not RE_FOTO_ID.match(foto_id):
        raise ErrorHerramienta("foto_id inválido.")
    key = f"fotos/{ctx.session_id}/{foto_id}"
    try:
        obj = _s3().get_object(Bucket=config.BUCKET_FOTOS, Key=key)
    except _s3().exceptions.NoSuchKey:
        raise ErrorHerramienta("No encontré esa foto. Pide al cliente que la envíe de nuevo.")
    if obj["ContentLength"] > MAX_BYTES_FOTO:
        raise ErrorHerramienta("La foto es demasiado grande.")
    resultado = vision.clasificar(obj["Body"].read(), MEDIA_TYPES[foto_id.rsplit(".", 1)[1]])
    if resultado.get("es_vehiculo"):
        ctx.conv["vehiculo"] = {**resultado, "foto_key": key, "origen": "foto", "confirmado": False}
    return resultado


def confirmar_vehiculo(ctx: Contexto, marca: str, modelo: str, confirmado_por_cliente: bool) -> dict:
    if not confirmado_por_cliente:
        raise ErrorHerramienta("El cliente aún no confirma. Pregúntale si la marca y el modelo son correctos.")
    marca, modelo = marca.strip()[:40], modelo.strip()[:40]
    if not marca or not modelo:
        raise ErrorHerramienta("Faltan la marca o el modelo. Pregúntaselos al cliente (o pídele una foto).")
    vehiculo = ctx.conv.get("vehiculo") or {"tipo": "otro", "color": "", "observaciones": "", "foto_key": "",
                                            "origen": "cliente"}
    vehiculo.update({"marca": marca, "modelo": modelo, "confirmado": True})
    ctx.conv["vehiculo"] = vehiculo
    return {"ok": True, "vehiculo": {k: vehiculo[k] for k in ("marca", "modelo", "tipo", "color")}}


def consultar_disponibilidad(ctx: Contexto, fecha: str) -> dict:
    dia = agenda.parsear_fecha(fecha, ctx.ahora)
    libres = agenda.horarios_libres(dia, store.citas_por_fecha(dia.isoformat()), ctx.ahora)
    return {"fecha": dia.isoformat(), "dia_semana": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado",
                                                     "domingo"][dia.weekday()], "horarios_libres": libres}


def agendar_cita(ctx: Contexto, fecha: str, hora: str, servicio: str, descripcion: str, email: str,
                 horario_confirmado_por_cliente: bool) -> dict:
    vehiculo = ctx.conv.get("vehiculo") or {}
    if not vehiculo.get("confirmado"):
        raise ErrorHerramienta("El vehículo no está confirmado por el cliente. Usa confirmar_vehiculo primero.")
    if not horario_confirmado_por_cliente:
        raise ErrorHerramienta("El cliente no ha confirmado la fecha y hora. Pregúntale antes de agendar.")
    if servicio not in agenda.SERVICIOS:
        raise ErrorHerramienta(f"Servicio inválido. Opciones: {', '.join(agenda.SERVICIOS)}.")
    email = email.strip()
    if email and not RE_EMAIL.match(email):
        raise ErrorHerramienta("El correo no parece válido.")
    cliente = ctx.cliente
    dia = agenda.parsear_fecha(fecha, ctx.ahora)
    inicio = agenda.validar_hora(dia, hora, ctx.ahora)
    activas = [c for c in store.citas_por_telefono(cliente["telefono"], ctx.ahora.date().isoformat())
               if c.get("estado") == "confirmada"]
    if len(activas) >= MAX_CITAS_ACTIVAS:
        raise ErrorHerramienta(f"El cliente ya tiene {len(activas)} citas activas (máximo {MAX_CITAS_ACTIVAS}).")

    cita_id = _nuevo_id_cita()
    citas_dia = store.citas_por_fecha(dia.isoformat())
    while True:
        mecanico = agenda.elegir_mecanico(vehiculo["marca"], hora, citas_dia)
        if mecanico is None:
            raise ErrorHerramienta("Ese horario ya no está disponible. Consulta la disponibilidad de nuevo.")
        try:
            store.reservar_horario(dia.isoformat(), hora, mecanico["id"], cita_id)
            break
        except store.HorarioOcupado:
            citas_dia = [*citas_dia, {"hora": hora, "mecanico_id": mecanico["id"], "estado": "confirmada"}]

    cita = {
        "cita_id": cita_id,
        "session_id": ctx.session_id,
        "nombre": cliente.get("nombre", ""),
        "telefono": cliente["telefono"],
        "email": email,
        "marca": vehiculo["marca"],
        "modelo": vehiculo["modelo"],
        "tipo": vehiculo.get("tipo", "otro"),
        "color": vehiculo.get("color", ""),
        "observaciones": vehiculo.get("observaciones", ""),
        "foto_key": vehiculo.get("foto_key", ""),
        "servicio": servicio,
        "descripcion": descripcion.strip()[:300],
        "fecha": dia.isoformat(),
        "hora": hora,
        "inicio": inicio.isoformat(),
        "mecanico_id": mecanico["id"],
        "mecanico_nombre": mecanico["nombre"],
        "estado": "confirmada",
        "creada": datetime.now(timezone.utc).isoformat(),
    }
    store.guardar_cita(cita)
    try:
        cita["recordatorio"] = notificaciones.programar_recordatorio(cita_id, inicio, ctx.ahora)
    except Exception:
        log.exception("No se pudo programar el recordatorio de %s", cita_id)
        cita["recordatorio"] = None
    if cita["recordatorio"]:
        store.guardar_cita(cita)
    correo = notificaciones.enviar_correo(email, f"Cita confirmada {cita_id} - {config.NOMBRE_TALLER}",
                                          notificaciones.texto_cita(cita))
    ctx.conv["vehiculo"] = None
    return {"ok": True, "cita_id": cita_id, "fecha": cita["fecha"], "hora": hora,
            "mecanico": mecanico["nombre"], "servicio": servicio,
            "recordatorio_programado_para": cita["recordatorio"], "correo": correo}


def mis_citas(ctx: Contexto) -> dict:
    citas = store.citas_por_telefono(ctx.cliente["telefono"], ctx.ahora.date().isoformat())
    return {"citas": [{k: c[k] for k in ("cita_id", "fecha", "hora", "marca", "modelo", "servicio", "mecanico_nombre")}
                      for c in sorted(citas, key=lambda c: (c["fecha"], c["hora"])) if c.get("estado") == "confirmada"]}


def cancelar_cita(ctx: Contexto, cita_id: str, confirmado_por_cliente: bool) -> dict:
    if not confirmado_por_cliente:
        raise ErrorHerramienta("Pide confirmación al cliente antes de cancelar.")
    cita = store.obtener_cita(cita_id.strip().upper())
    if not cita or cita.get("telefono") != ctx.cliente.get("telefono"):
        raise ErrorHerramienta("No encontré una cita con ese número para este cliente.")
    if cita.get("estado") != "confirmada":
        raise ErrorHerramienta(f"La cita está {cita.get('estado')}; no se puede cancelar.")
    cancelar(cita)
    return {"ok": True, "cita_id": cita["cita_id"], "estado": "cancelada"}


def cancelar(cita: dict) -> None:
    store.actualizar_estado_cita(cita["cita_id"], "cancelada")
    store.liberar_horario(cita["fecha"], cita["hora"], cita["mecanico_id"])
    notificaciones.cancelar_recordatorio(cita["cita_id"])


def cargar_skill(ctx: Contexto, nombre: str) -> dict:
    contenido = skills.cargar(nombre)
    if contenido is None:
        raise ErrorHerramienta("Skill inexistente.")
    return {"skill": nombre, "instrucciones": contenido}


IMPLEMENTACIONES = {
    "identificar_vehiculo": identificar_vehiculo,
    "confirmar_vehiculo": confirmar_vehiculo,
    "consultar_disponibilidad": consultar_disponibilidad,
    "agendar_cita": agendar_cita,
    "mis_citas": mis_citas,
    "cancelar_cita": cancelar_cita,
    "cargar_skill": cargar_skill,
}


def ejecutar(ctx: Contexto, nombre: str, entrada: dict) -> tuple[str, bool]:
    """Devuelve (contenido, es_error) listo para un bloque tool_result."""
    funcion = IMPLEMENTACIONES.get(nombre)
    if funcion is None:
        return f"Herramienta desconocida: {nombre}", True
    try:
        return json.dumps(funcion(ctx, **entrada), ensure_ascii=False), False
    except (ErrorHerramienta, agenda.ErrorAgenda) as e:
        return str(e), True
    except TypeError as e:
        return f"Parámetros inválidos: {e}", True
