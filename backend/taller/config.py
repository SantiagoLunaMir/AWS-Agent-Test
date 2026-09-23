import os

REGION = os.environ.get("AWS_REGION", "us-east-2")
MODEL_ID = os.environ.get("MODEL_ID", "us.amazon.nova-2-lite-v1:0")
VISION_MODEL_ID = os.environ.get("VISION_MODEL_ID") or MODEL_ID
# Razonamiento extendido de Nova 2 ("low", "medium", "high"); vacío lo apaga. Solo se envía a modelos Nova 2.
RAZONAMIENTO = os.environ.get("RAZONAMIENTO", "low")
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
# Parámetros SecureString de SSM Parameter Store (gratis, a diferencia de Secrets Manager) con la clave del panel
# y el código de acceso del chat. Sin parámetro configurado, la ruta rechaza todo (401).
PANEL_PARAMETRO = os.environ.get("PANEL_PARAMETRO", "")
CHAT_PARAMETRO = os.environ.get("CHAT_PARAMETRO", "")
# Cada cuánto se vuelven a leer las claves: una clave rotada en Parameter Store aplica sin redesplegar.
SEGUNDOS_CACHE_CLAVES = 300

MAX_PASOS_AGENTE = 8
MAX_TEXTO = 1000
MAX_TURNOS_POR_VENTANA = 20
VENTANA_TURNOS_SEG = 600
DIAS_RETENCION = 7
