"""Acceso a DynamoDB. Los datos estructurados se guardan como JSON para evitar conversiones a Decimal."""
import json
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache

import boto3
from boto3.dynamodb.conditions import Key

from . import config


@lru_cache(maxsize=None)
def _tabla(nombre: str):
    return boto3.resource("dynamodb", region_name=config.REGION).Table(nombre)


def _ttl() -> int:
    return int(time.time()) + config.DIAS_RETENCION * 86400


# ---------- conversaciones ----------
def obtener_conversacion(session_id: str) -> dict:
    item = _tabla(config.TABLA_CONVERSACIONES).get_item(Key={"session_id": session_id}).get("Item")
    if not item:
        return {"session_id": session_id, "history": [], "vehiculo": None, "cliente": None,
                "estado": "listo", "estado_ts": 0, "turnos": []}
    return {
        "session_id": session_id,
        "history": json.loads(item.get("history", "[]")),
        "vehiculo": json.loads(item["vehiculo"]) if item.get("vehiculo") else None,
        "cliente": json.loads(item["cliente"]) if item.get("cliente") else None,
        "estado": item.get("estado", "listo"),
        "estado_ts": int(item.get("estado_ts", 0)),
        "turnos": json.loads(item.get("turnos", "[]")),
    }


def guardar_conversacion(conv: dict) -> None:
    _tabla(config.TABLA_CONVERSACIONES).put_item(Item={
        "session_id": conv["session_id"],
        "history": json.dumps(conv["history"], ensure_ascii=False),
        "vehiculo": json.dumps(conv["vehiculo"], ensure_ascii=False) if conv.get("vehiculo") else "",
        "cliente": json.dumps(conv["cliente"], ensure_ascii=False) if conv.get("cliente") else "",
        "estado": conv.get("estado", "listo"),
        "estado_ts": int(conv.get("estado_ts", 0)),
        "turnos": json.dumps(conv.get("turnos", [])),
        "ttl": _ttl(),
    })


def iniciar_turno(session_id: str, turnos: list[int], cliente: dict) -> None:
    _tabla(config.TABLA_CONVERSACIONES).update_item(
        Key={"session_id": session_id},
        UpdateExpression="SET estado = :e, estado_ts = :t, turnos = :tu, cliente = :c, #ttl = :ttl",
        ExpressionAttributeNames={"#ttl": "ttl"},
        ExpressionAttributeValues={":e": "pensando", ":t": int(time.time()), ":tu": json.dumps(turnos),
                                   ":c": json.dumps(cliente, ensure_ascii=False), ":ttl": _ttl()},
    )


def marcar_estado(session_id: str, estado: str) -> None:
    _tabla(config.TABLA_CONVERSACIONES).update_item(
        Key={"session_id": session_id},
        UpdateExpression="SET estado = :e, estado_ts = :t, #ttl = :ttl",
        ExpressionAttributeNames={"#ttl": "ttl"},
        ExpressionAttributeValues={":e": estado, ":t": int(time.time()), ":ttl": _ttl()},
    )


# ---------- mensajes visibles en el chat ----------
def agregar_mensaje(session_id: str, rol: str, texto: str, foto_key: str | None = None) -> dict:
    ahora = datetime.now(timezone.utc)
    item = {
        "session_id": session_id,
        "sk": f"{ahora.strftime('%Y%m%dT%H%M%S%f')}#{uuid.uuid4().hex[:6]}",
        "rol": rol,
        "texto": texto,
        "ts": ahora.isoformat(),
        "ttl": _ttl(),
    }
    if foto_key:
        item["foto_key"] = foto_key
    _tabla(config.TABLA_MENSAJES).put_item(Item=item)
    return item


def listar_mensajes(session_id: str, desde: str | None = None) -> list[dict]:
    cond = Key("session_id").eq(session_id)
    if desde:
        cond = cond & Key("sk").gt(desde)
    resp = _tabla(config.TABLA_MENSAJES).query(KeyConditionExpression=cond, Limit=200)
    return [{k: v for k, v in m.items() if k not in ("ttl", "session_id")} for m in resp.get("Items", [])]


# ---------- citas ----------
class HorarioOcupado(Exception):
    pass


def _id_reserva(fecha: str, hora: str, mecanico_id: str) -> str:
    return f"reserva#{fecha}#{hora}#{mecanico_id}"


def reservar_horario(fecha: str, hora: str, mecanico_id: str, cita_id: str) -> None:
    """Escritura condicional: dos clientes no pueden quedarse con el mismo mecánico a la misma hora."""
    try:
        _tabla(config.TABLA_CITAS).put_item(
            Item={"cita_id": _id_reserva(fecha, hora, mecanico_id), "reservada_por": cita_id},
            ConditionExpression="attribute_not_exists(cita_id)",
        )
    except _tabla(config.TABLA_CITAS).meta.client.exceptions.ConditionalCheckFailedException:
        raise HorarioOcupado()


def liberar_horario(fecha: str, hora: str, mecanico_id: str) -> None:
    _tabla(config.TABLA_CITAS).delete_item(Key={"cita_id": _id_reserva(fecha, hora, mecanico_id)})


def guardar_cita(cita: dict) -> None:
    _tabla(config.TABLA_CITAS).put_item(Item=cita)


def obtener_cita(cita_id: str) -> dict | None:
    return _tabla(config.TABLA_CITAS).get_item(Key={"cita_id": cita_id}).get("Item")


def citas_por_fecha(fecha: str) -> list[dict]:
    resp = _tabla(config.TABLA_CITAS).query(IndexName="por_fecha", KeyConditionExpression=Key("fecha").eq(fecha))
    return resp.get("Items", [])


def citas_por_telefono(telefono: str, desde_fecha: str) -> list[dict]:
    resp = _tabla(config.TABLA_CITAS).query(
        IndexName="por_telefono",
        KeyConditionExpression=Key("telefono").eq(telefono) & Key("fecha").gte(desde_fecha),
    )
    return resp.get("Items", [])


def actualizar_estado_cita(cita_id: str, estado: str) -> None:
    _tabla(config.TABLA_CITAS).update_item(
        Key={"cita_id": cita_id},
        UpdateExpression="SET estado = :e, actualizada = :t",
        ConditionExpression="attribute_exists(cita_id)",
        ExpressionAttributeValues={":e": estado, ":t": datetime.now(timezone.utc).isoformat()},
    )


def marcar_recordatorio_enviado(cita_id: str) -> None:
    _tabla(config.TABLA_CITAS).update_item(
        Key={"cita_id": cita_id},
        UpdateExpression="SET recordatorio_enviado = :v",
        ExpressionAttributeValues={":v": True},
    )
