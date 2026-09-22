import os

REGION = os.environ.get("AWS_REGION", "us-east-2")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-opus-4-6-v1")
VISION_MODEL_ID = os.environ.get("VISION_MODEL_ID", MODEL_ID)
EFFORT = os.environ.get("EFFORT", "medium")
TZ = os.environ.get("TALLER_TZ", "America/Mexico_City")
NOMBRE_TALLER = os.environ.get("NOMBRE_TALLER", "Taller Pistón")

TABLA_CONVERSACIONES = os.environ.get("TABLA_CONVERSACIONES", "taller-conversaciones")
TABLA_MENSAJES = os.environ.get("TABLA_MENSAJES", "taller-mensajes")
TABLA_CITAS = os.environ.get("TABLA_CITAS", "taller-citas")
BUCKET_FOTOS = os.environ.get("BUCKET_FOTOS", "")

WORKER_FUNCTION = os.environ.get("WORKER_FUNCTION", "")
RECORDATORIO_FUNCTION_ARN = os.environ.get("RECORDATORIO_FUNCTION_ARN", "")
SCHEDULER_ROLE_ARN = os.environ.get("SCHEDULER_ROLE_ARN", "")
SCHEDULE_GROUP = os.environ.get("SCHEDULE_GROUP", "taller-recordatorios")
# "demo": el recordatorio llega 2 minutos después de agendar; "real": 24 h antes de la cita.
MODO_RECORDATORIO = os.environ.get("MODO_RECORDATORIO", "demo")

GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "DRAFT")

SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")
PANEL_SECRET_ARN = os.environ.get("PANEL_SECRET_ARN", "")

MAX_PASOS_AGENTE = 8
MAX_TEXTO = 1000
MAX_TURNOS_POR_VENTANA = 20
VENTANA_TURNOS_SEG = 600
DIAS_RETENCION = 7
