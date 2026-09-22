"""Bedrock Guardrails vía la API ApplyGuardrail: se revisa el mensaje del cliente y la respuesta del agente."""
import logging
from functools import lru_cache

import boto3

from . import config

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _runtime():
    return boto3.client("bedrock-runtime", region_name=config.REGION)


def revisar(texto: str, fuente: str) -> tuple[bool, str]:
    """Devuelve (bloqueado, texto_a_usar). fuente es "INPUT" o "OUTPUT"."""
    if not config.GUARDRAIL_ID or not texto.strip():
        return False, texto
    resp = _runtime().apply_guardrail(
        guardrailIdentifier=config.GUARDRAIL_ID,
        guardrailVersion=config.GUARDRAIL_VERSION,
        source=fuente,
        content=[{"text": {"text": texto}}],
    )
    if resp.get("action") != "GUARDRAIL_INTERVENED":
        return False, texto
    log.warning("Guardrail intervino en %s: %s", fuente, [a.get("topicPolicy") or a.get("contentPolicy")
                                                         for a in resp.get("assessments", [])])
    salida = " ".join(o.get("text", "") for o in resp.get("outputs", [])).strip()
    return True, salida or "No puedo ayudar con eso. ¿Te ayudo con una cita para tu vehículo?"
