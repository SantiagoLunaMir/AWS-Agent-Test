# AGENTS.md · Guía para agentes de código

Instrucciones para que un agente (Claude Code, Codex, Cursor, Copilot, etc.) despliegue y pruebe este repo rápido. La explicación para humanos está en [README.md](README.md).

## Qué es

Demo educativa: un agente de IA agenda citas en un taller mecánico desde un chat **estilo** app de mensajería. **No** usa WhatsApp ni ningún servicio de Meta: solo imita la interfaz.

- `backend/taller/`: Lambdas en Python 3.12. El modelo es **Amazon Nova 2 Lite** (`us.amazon.nova-2-lite-v1:0`) en Bedrock, con la **API Converse** de `boto3` (sin SDK de terceros). No pide formulario de caso de uso.
- `frontend/`: HTML/CSS/JS estático sin build (chat y panel del taller).
- `infra/`: AWS CDK en Python. Nombre del stack: `TallerAgente`.
- `tests/`: pytest + moto. No llaman a AWS.
- `scripts/`: prueba de humo y detector de modelos.

## Ruta rápida (≈10 min)

Todos los comandos se corren desde la raíz del repo. En Windows usa Git Bash (en PowerShell cambia `source .venv/Scripts/activate` por `.venv\Scripts\activate`).

```bash
# 0. Requisitos: AWS CLI con credenciales, Python 3.12, Node 18+, Docker encendido
aws sts get-caller-identity && docker info --format '{{.ServerVersion}}' && node --version

# 1. Entorno
python -m venv .venv
source .venv/Scripts/activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt -r infra/requirements.txt

# 2. Pruebas unitarias (sin AWS). Deben pasar todas.
pytest -q

# 3. ¿Qué modelos responden en la cuenta? (Nova 2 Lite debería salir OK; si no, usa el comando que recomiende)
python scripts/probar_modelos.py --region us-east-2

# 4. Desplegar (bootstrap solo la primera vez por cuenta y región). Sin -c usa Nova 2 Lite
cd infra
npx -y aws-cdk@latest bootstrap
npx -y aws-cdk@latest deploy --require-approval never --outputs-file ../cdk-outputs.json
cd ..

# 5. Prueba de humo de punta a punta contra lo desplegado
python scripts/smoke_test.py            # código de acceso + 1 turno de chat + fotos + panel
python scripts/smoke_test.py --flujo    # conversación completa hasta agendar (y cancela la cita al final)
```

`cdk-outputs.json` contiene `ChatUrl`, `PanelUrl`, `ApiUrl`, `ChatKeyCommand` y `PanelKeyCommand`. Está en `.gitignore`: **no lo subas**. Para que un humano pruebe el chat, dale `<ChatUrl>/#clave=<código>` con el código de `ChatKeyCommand`.

## Resultados esperados

- `pytest -q`: todas las pruebas pasan.
- `smoke_test.py`: termina con `Todo OK` y código de salida 0.
- Si el agente responde *"Tuve un problema técnico…"*, casi siempre es el acceso al modelo (siguiente sección).

## Problemas conocidos

| Síntoma | Causa | Qué hacer |
|---|---|---|
| `AccessDeniedException ... is not available for this account` | Ese proveedor no está disponible para la cuenta (pasa, por ejemplo, con algunos modelos en el *Free plan*) | Usa el que recomiende `scripts/probar_modelos.py` |
| `404 Model use case details have not been submitted` | Se configuró un modelo de Anthropic, que pide formulario | Vuelve al predeterminado (Nova 2 Lite) o a otro de la tabla del README |
| `ResourceNotFoundException ... end of its life` / `Legacy` | Modelo retirado (por ejemplo, Nova Premier o Nova Canvas) | Cambia de modelo |
| `ValidationException` sobre `outputConfig` o `strict` | Nova 2 Lite no los soporta | No los agregues: la foto usa una herramienta forzada (`toolChoice`) y `herramientas.validar_entrada` valida cada llamada |
| `ValidationException` sobre `reasoningConfig` | Se envió razonamiento a un modelo que no es Nova 2 | `agente._campos_del_modelo` solo lo manda a `amazon.nova-2*`; revisa si lo cambiaste |
| `AccessDeniedException` en `bedrock:InvokeModel` desde la Lambda | La política IAM solo permite los modelos configurados | Despliega de nuevo con `-c modelId=...` / `-c visionModelId=...` en lugar de cambiar la variable de entorno a mano |
| `ENOTEMPTY ... jsii-kernel` al terminar un comando CDK en Windows | Limpieza de un temporal de jsii | Inofensivo, ignóralo |
| `ModuleNotFoundError: pydantic_core` en pytest | pytest recorrió `infra/cdk.out` | `pytest.ini` ya fija `testpaths = tests`: corre pytest desde la raíz |
| El bundling de la Lambda falla | Docker apagado | Enciende Docker; CDK empaqueta con la imagen oficial de Python 3.12 |
| `401 Código de acceso inválido` en `/chat`, `/mensajes` o `/fotos`, o el chat vuelve a pedir el código | Falta la cabecera `x-chat-key` o el código cambió en Parameter Store | Obtén el vigente con `ChatKeyCommand`. Tras rotarlo, la Lambda tarda hasta 5 min en leerlo (`config.SEGUNDOS_CACHE_CLAVES`) |

### Ver logs

Los log groups tienen nombre propio (no `/aws/lambda/...`):

```bash
FN=$(aws lambda list-functions --region us-east-2 --query "Functions[?starts_with(FunctionName,'TallerAgente-Agente')].FunctionName" --output text)
LG=$(aws lambda get-function-configuration --function-name "$FN" --region us-east-2 --query LoggingConfig.LogGroup --output text)
MSYS_NO_PATHCONV=1 aws logs tail "$LG" --region us-east-2 --since 15m --format short
```

Cambia `Agente` por `Api` o `Recordatorio` para ver las otras funciones. Cada llamada al modelo se registra como `modelo <id> stop=<motivo> tokens entrada=… salida=… cache_leidos=…` y cada herramienta como `tool <nombre>(<entrada>) -> error=<bool>`.

## Cómo está armado (lo mínimo para modificarlo)

- **Flujo de un turno:** `POST /chat` → Lambda `api` revisa el código de acceso (`x-chat-key`), valida, limita el ritmo y hace `lambda.invoke(InvocationType="Event")` → Lambda `procesar` corre Guardrail (entrada) → `agente.responder()` → Guardrail (salida) → guarda el mensaje en DynamoDB. El frontend consulta `GET /mensajes` cada 2 s. Es asíncrono para no chocar con el límite de 30 s de API Gateway.
- **Modelo:** `llm.py` crea el cliente `bedrock-runtime`. `agente.py` usa `converse()` con el prompt fijo antes de un `cachePoint`, las herramientas en `toolConfig` y razonamiento opcional de Nova 2 en `additionalModelRequestFields`. El historial guarda solo bloques `text`, `toolUse` y `toolResult` (formato Converse). Para cambiar de modelo usa `-c modelId=...` al desplegar; no hace falta tocar código.
- **Herramientas:** `backend/taller/herramientas.py`. Las reglas críticas (confirmación del cliente, horarios, máximo 2 citas activas, reserva atómica del horario, dueño de la cita) se validan **en código**. Como Nova 2 Lite no tiene modo `strict`, `validar_entrada` revisa cada entrada contra su esquema (tipos, `enum`, parámetros faltantes o de más). Si agregas una herramienta, usa solo tipos `string`/`boolean` o amplía `TIPOS_JSON`. Lo que el modelo no respeta solo con el prompt se resuelve en la herramienta: `consultar_disponibilidad` devuelve en `proponer` hasta 3 horarios repartidos, y los mensajes al cliente usan `agenda.fecha_legible` ("lunes 28 de septiembre"), nunca la fecha ISO.
- **Foto opcional:** el cliente puede dar marca y modelo por texto. Si manda foto, `vision.py` la clasifica en una llamada aislada que obliga al modelo a usar la herramienta `registrar_vehiculo` (`toolChoice`), cuyo esquema es el JSON esperado; después `sanear` recorta cada campo. La imagen **nunca** entra a la conversación del agente.
- **Skills:** `backend/taller/skills/<nombre>/SKILL.md` con frontmatter `name` y `description`. Se cargan solas al catálogo; el agente las abre con `cargar_skill`.
- **Reglas del taller:** horarios, mecánicos y especialidades están en `backend/taller/agenda.py`.
- **Claves del panel y del chat:** parámetros `SecureString` `TallerAgente-clave-panel` (cabecera `x-panel-key`) y `TallerAgente-clave-chat` (cabecera `x-chat-key`, protege `/chat`, `/mensajes` y `/fotos`) en SSM Parameter Store. Los crea un recurso personalizado del stack (`CODIGO_CLAVES` en `infra/taller_stack.py`) si no existen y los borra al destruir el stack. `handler._autorizado` los compara en tiempo constante y los relee cada 5 minutos, así que rotarlos no requiere desplegar. Sin parámetro configurado, la ruta responde 401. No uses Secrets Manager: cobra por secreto al mes y el objetivo es no tener costos fijos.
- **Configuración:** variables de entorno en `backend/taller/config.py`. Contexto de CDK: `modelId` (predeterminado `us.amazon.nova-2-lite-v1:0`), `visionModelId` (predeterminado: igual que `modelId`; debe aceptar imágenes), `razonamiento` (`low` por defecto; vacío lo apaga), `modoRecordatorio` (`demo` = 2 min después de agendar; `real` = 24 h antes) y `senderEmail` (SES opcional; en sandbox el destinatario debe estar verificado). Sin `senderEmail`, `agente.PASO_AGENDAR` le indica al modelo que no ofrezca correo y `agendar_cita` no guarda el que dé el cliente.
- **Identificadores en español** en todo el código. Mantén ese estilo.

## Reglas para agentes

- No hagas commit de `cdk-outputs.json`, `frontend/config.json`, `.env`, credenciales ni datos reales. Usa solo teléfonos y nombres ficticios.
- No escribas la clave del panel ni el código del chat en navegadores ni los pegues en archivos o commits. Obtenlos con `PanelKeyCommand` / `ChatKeyCommand` solo para pruebas por API; el humano es quien los usa en el navegador.
- Después de cambiar `backend/` o `frontend/`, corre `pytest -q`, vuelve a desplegar y ejecuta `smoke_test.py`.
- El stack crea recursos con costo por uso. Si solo lo levantaste para probar, avisa al humano antes de destruirlo: `cd infra && npx aws-cdk destroy`.
- Si la cuenta está en el *Free plan* de AWS, todo se paga con créditos y **la cuenta se cierra si se acaban**. No lances pruebas de carga ni bucles contra el modelo, y no agregues servicios con costo fijo mensual (Secrets Manager, NAT Gateway, instancias o endpoints siempre encendidos).
