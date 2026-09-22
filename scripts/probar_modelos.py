"""Prueba qué modelos de Bedrock puede usar el agente en tu cuenta, con la API Converse y sus herramientas reales.

Uso:
    python scripts/probar_modelos.py [--region us-east-2]
    python scripts/probar_modelos.py --modelo qwen.qwen3-235b-a22b-2507-v1:0

Por cada modelo pide los horarios de mañana y revisa que responda llamando a consultar_disponibilidad.
Al final recomienda el `-c modelId=...` (y `-c visionModelId=...` si hace falta) para `npx aws-cdk deploy`.
"""
import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from taller import herramientas  # noqa: E402

# Modelos que pasaron las pruebas del flujo de citas (sep 2026), en orden de preferencia. Precio en USD por millón
# de tokens (entrada/salida) en us-east-2. Ninguno pide formulario de caso de uso.
CANDIDATOS = {
    "us.amazon.nova-2-lite-v1:0": "0.33/2.75 · visión · el predeterminado",
    "global.amazon.nova-2-lite-v1:0": "0.30/2.50 · visión · enrutamiento global",
    "openai.gpt-oss-120b-1:0": "0.15/0.60 · solo texto",
    "qwen.qwen3-235b-a22b-2507-v1:0": "0.22/0.88 · solo texto",
    "deepseek.v3.2": "0.62/1.85 · solo texto, más lento",
    "moonshotai.kimi-k2.5": "0.60/3.00 · visión, más lento",
}
PREFIJOS_PERFIL = ("us.", "eu.", "apac.", "jp.", "au.", "ca.", "global.")


def acepta_imagenes(region: str) -> set[str]:
    modelos = boto3.client("bedrock", region_name=region).list_foundation_models()["modelSummaries"]
    return {m["modelId"] for m in modelos if "IMAGE" in m.get("inputModalities", [])}


def base(modelo: str) -> str:
    return modelo.split(".", 1)[1] if modelo.startswith(PREFIJOS_PERFIL) else modelo


def probar(br, modelo: str) -> tuple[bool, str]:
    manana = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    inicio = time.time()
    try:
        r = br.converse(
            modelId=modelo,
            system=[{"text": f"Eres el asistente de citas de un taller mecánico. Hoy es {dt.date.today()}. "
                             "Usa consultar_disponibilidad para ver horarios; nunca los inventes."}],
            messages=[{"role": "user", "content": [{"text": f"¿Qué horarios tienen el {manana}?"}]}],
            toolConfig=herramientas.definiciones(),
            inferenceConfig={"maxTokens": 800},
        )
    except ClientError as e:
        return False, f"{e.response['Error']['Code']}: {e.response['Error']['Message'][:90]}"
    segundos = time.time() - inicio
    usos = [b["toolUse"] for b in r["output"]["message"]["content"] if "toolUse" in b]
    if not usos or usos[0]["name"] != "consultar_disponibilidad":
        return False, f"respondió sin usar la herramienta ({segundos:.1f} s)"
    return True, f"llamó {usos[0]['name']}({usos[0]['input']}) en {segundos:.1f} s"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--modelo", action="append", help="modelo a probar (se puede repetir)")
    args = parser.parse_args()
    br = boto3.client("bedrock-runtime", region_name=args.region,
                      config=Config(read_timeout=60, retries={"max_attempts": 1}))
    vision = acepta_imagenes(args.region)
    ok = []
    for modelo in args.modelo or CANDIDATOS:
        paso, detalle = probar(br, modelo)
        con_vision = base(modelo) in vision
        print(f"{'OK ' if paso else 'ERR'}  {modelo:36} {'visión' if con_vision else 'texto '}  {detalle}")
        if CANDIDATOS.get(modelo):
            print(f"     {CANDIDATOS[modelo]}")
        if paso:
            ok.append((modelo, con_vision))

    if not ok:
        print("\nNingún modelo respondió. Revisa la región, tus credenciales y que la cuenta tenga acceso a Bedrock. "
              "'is not available for this account' suele significar que ese proveedor no está disponible en tu plan.")
        return 1
    agente, agente_vision = ok[0]
    comando = f"npx aws-cdk deploy -c modelId={agente}"
    if not agente_vision:
        con_vision = next((m for m, v in ok if v), None)
        comando += f" -c visionModelId={con_vision}" if con_vision else "   (sin modelo de visión: la foto no funcionará)"
    print(f"\nUsa: {comando}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
