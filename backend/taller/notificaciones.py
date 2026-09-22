"""Recordatorios con EventBridge Scheduler y correos con Amazon SES (opcional)."""
import json
import logging
from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import ClientError

from . import config

log = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def _cliente(servicio: str):
    return boto3.client(servicio, region_name=config.REGION)


def _nombre_schedule(cita_id: str) -> str:
    return f"recordatorio-{cita_id}"


def momento_recordatorio(inicio_cita: datetime, ahora: datetime) -> datetime:
    if config.MODO_RECORDATORIO == "demo":
        return ahora + timedelta(minutes=2)
    objetivo = inicio_cita - timedelta(hours=24)
    return objetivo if objetivo > ahora + timedelta(minutes=1) else ahora + timedelta(minutes=1)


def programar_recordatorio(cita_id: str, inicio_cita: datetime, ahora: datetime) -> str | None:
    if not (config.RECORDATORIO_FUNCTION_ARN and config.SCHEDULER_ROLE_ARN):
        return None
    cuando = momento_recordatorio(inicio_cita, ahora).astimezone(ZoneInfo(config.TZ))
    _cliente("scheduler").create_schedule(
        Name=_nombre_schedule(cita_id),
        GroupName=config.SCHEDULE_GROUP,
        ScheduleExpression=f"at({cuando.strftime('%Y-%m-%dT%H:%M:%S')})",
        ScheduleExpressionTimezone=config.TZ,
        FlexibleTimeWindow={"Mode": "OFF"},
        ActionAfterCompletion="DELETE",
        Target={
            "Arn": config.RECORDATORIO_FUNCTION_ARN,
            "RoleArn": config.SCHEDULER_ROLE_ARN,
            "Input": json.dumps({"cita_id": cita_id}),
        },
    )
    return cuando.isoformat()


def cancelar_recordatorio(cita_id: str) -> None:
    if not config.RECORDATORIO_FUNCTION_ARN:
        return
    try:
        _cliente("scheduler").delete_schedule(Name=_nombre_schedule(cita_id), GroupName=config.SCHEDULE_GROUP)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise


def enviar_correo(destino: str | None, asunto: str, cuerpo: str) -> str:
    """Devuelve un estado legible para el agente. En el sandbox de SES el destinatario debe estar verificado."""
    if not destino:
        return "sin_correo"
    if not config.SENDER_EMAIL:
        return "correo_no_configurado"
    try:
        _cliente("sesv2").send_email(
            FromEmailAddress=config.SENDER_EMAIL,
            Destination={"ToAddresses": [destino]},
            Content={"Simple": {"Subject": {"Data": asunto, "Charset": "UTF-8"},
                                "Body": {"Text": {"Data": cuerpo, "Charset": "UTF-8"}}}},
        )
        return "enviado"
    except ClientError as e:
        log.warning("SES no pudo enviar a %s: %s", destino, e.response["Error"]["Message"])
        return "no_enviado"


def texto_cita(cita: dict) -> str:
    return (
        f"Cita {cita['cita_id']}\n"
        f"Fecha: {cita['fecha']} a las {cita['hora']}\n"
        f"Vehículo: {cita['marca']} {cita['modelo']}\n"
        f"Servicio: {cita['servicio']}\n"
        f"Mecánico asignado: {cita['mecanico_nombre']}\n"
        f"{config.NOMBRE_TALLER}"
    )
