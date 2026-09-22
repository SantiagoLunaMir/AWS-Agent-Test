"""Detecta qué perfiles de inferencia de Claude puede invocar la cuenta en una región.

Uso: python scripts/probar_modelos.py [--region us-east-2]
Imprime el primer modelo que responde, listo para `npx aws-cdk deploy -c modelId=...`.
"""
import argparse
import sys

import anthropic
import boto3
from anthropic import AnthropicBedrock

PREFERENCIA = ["opus-5-5", "opus-5", "fable-5-1", "sonnet-5", "opus-4-8", "opus-4-7", "opus-4-6", "sonnet-4-6",
               "sonnet-4-5", "haiku-4-5"]
# El agente usa thinking adaptativo y effort: requiere la familia 4.6 o posterior.
COMPATIBLES = ("opus-5", "fable-5", "sonnet-5", "opus-4-8", "opus-4-7", "opus-4-6", "sonnet-4-6")


def perfiles(region: str) -> list[str]:
    resp = boto3.client("bedrock", region_name=region).list_inference_profiles(maxResults=1000)
    ids = [p["inferenceProfileId"] for p in resp["inferenceProfileSummaries"]
           if p["inferenceProfileId"].startswith("us.anthropic.")]
    return sorted(ids, key=lambda i: next((n for n, clave in enumerate(PREFERENCIA) if clave in i), 99))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-east-2")
    args = parser.parse_args()
    cliente = AnthropicBedrock(aws_region=args.region, max_retries=0)
    disponibles = []
    for perfil in perfiles(args.region):
        compatible = any(c in perfil for c in COMPATIBLES)
        try:
            cliente.messages.create(model=perfil, max_tokens=16, messages=[{"role": "user", "content": "hola"}])
            print(f"OK    {perfil}{'' if compatible else '  (responde, pero el código requiere la familia 4.6+)'}")
            if compatible:
                disponibles.append(perfil)
        except anthropic.APIStatusError as e:
            motivo = str(e.body.get("message", e.body) if isinstance(e.body, dict) else e.body)[:90]
            print(f"{e.status_code}   {perfil}  {motivo}")
    if not disponibles:
        print("\nNingún modelo compatible respondió. Si ves 'use case details have not been submitted', completa el "
              "formulario de Anthropic en la consola de Bedrock (Model catalog > modelo de Anthropic > Submit use "
              "case details). El acceso puede ser intermitente hasta que se procese: repite la prueba.")
        return 1
    print(f"\nUsa: npx aws-cdk deploy -c modelId={disponibles[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
