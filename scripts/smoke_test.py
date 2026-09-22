"""Prueba de humo contra el stack desplegado: API, subida de foto, conversación con el agente y panel.

Uso:
    python scripts/smoke_test.py            # un turno de chat + fotos + panel
    python scripts/smoke_test.py --flujo    # conversación completa hasta agendar una cita
    python scripts/smoke_test.py --api https://xxxx.execute-api.us-east-2.amazonaws.com

Lee `cdk-outputs.json` (lo genera `npx aws-cdk deploy --outputs-file ../cdk-outputs.json`).
Sale con código 1 si algo falla.
"""
import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parents[1]
FALLA_TECNICA = "problema técnico"
FLUJO = [
    "Hola, quiero agendar una cita. Tengo un Ford Mustang 2019.",
    "Sí, es correcto. Necesito cambio de aceite, de preferencia mañana en la mañana.",
    "El primer horario que tengas me queda bien, confírmalo por favor. No quiero correo.",
]


class Fallo(Exception):
    pass


def salidas(api: str | None) -> dict:
    archivo = RAIZ / "cdk-outputs.json"
    datos = json.loads(archivo.read_text())["TallerAgente"] if archivo.exists() else {}
    if api:
        datos["ApiUrl"] = api
    if "ApiUrl" not in datos:
        raise Fallo("No encontré cdk-outputs.json ni --api. Despliega con --outputs-file ../cdk-outputs.json.")
    return datos


def paso(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"[{'OK ' if ok else 'ERR'}] {nombre}{' · ' + detalle if detalle else ''}")
    if not ok:
        raise Fallo(nombre)


def turno(api: str, sid: str, cliente: dict, texto: str, timeout: int = 180) -> list[str]:
    r = requests.post(f"{api}/chat", json={"session_id": sid, "texto": texto, "cliente": cliente}, timeout=15)
    paso(f"POST /chat «{texto[:40]}…»", r.status_code == 202, str(r.status_code))
    desde = r.json()["sk"]
    respuestas = []
    limite = time.time() + timeout
    while time.time() < limite:
        time.sleep(2)
        datos = requests.get(f"{api}/mensajes", params={"session_id": sid, "desde": desde}, timeout=15).json()
        for m in datos["mensajes"]:
            desde = m["sk"]
            if m["rol"] == "agente":
                respuestas.append(m["texto"])
                print(f"      agente> {m['texto']}")
        if datos["estado"] == "listo" and respuestas:
            break
    paso("el agente respondió", bool(respuestas), f"{len(respuestas)} mensaje(s)")
    if any(FALLA_TECNICA in t for t in respuestas):
        paso("respuesta sin error técnico", False,
             "revisa los logs de la Lambda del agente (acceso al modelo en Bedrock). "
             "Prueba: python scripts/probar_modelos.py")
    return respuestas


def clave_panel(datos: dict) -> str:
    comando = datos.get("PanelKeyCommand")
    if not comando:
        raise Fallo("Falta PanelKeyCommand en cdk-outputs.json")
    return subprocess.run(comando, shell=True, capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api")
    parser.add_argument("--flujo", action="store_true", help="conversación completa hasta agendar")
    args = parser.parse_args()
    try:
        datos = salidas(args.api)
        api = datos["ApiUrl"].rstrip("/")
        sid = str(uuid.uuid4())
        telefono = "55" + str(uuid.uuid4().int)[:8]
        cliente = {"nombre": "Smoke Test", "telefono": telefono}
        print(f"API {api}\nsesión {sid} · teléfono ficticio {telefono}")

        r = requests.post(f"{api}/chat", json={"session_id": "x", "texto": "hola", "cliente": cliente}, timeout=15)
        paso("validación de entrada (400)", r.status_code == 400)

        r = requests.post(f"{api}/fotos", json={"session_id": sid, "content_type": "image/jpeg"}, timeout=15)
        paso("POST /fotos (URL prefirmada)", r.status_code == 200)
        firma = r.json()
        subida = requests.post(firma["url"], data=firma["fields"],
                               files={"file": ("auto.jpg", b"\xff\xd8\xff\xe0prueba", "image/jpeg")}, timeout=30)
        paso("subida directa a S3", subida.status_code == 204, str(subida.status_code))

        mensajes = FLUJO if args.flujo else FLUJO[:1]
        for texto in mensajes:
            turno(api, sid, cliente, texto)

        r = requests.get(f"{api}/panel/citas", timeout=15)
        paso("panel sin clave (401)", r.status_code == 401)
        clave = clave_panel(datos)
        manana = time.strftime("%Y-%m-%d", time.localtime(time.time() + 86400))
        r = requests.get(f"{api}/panel/citas", params={"fecha": manana}, headers={"x-panel-key": clave}, timeout=15)
        paso("panel con clave (200)", r.status_code == 200)
        if args.flujo:
            mias = [c for c in r.json()["citas"] if c["telefono"] == telefono and c["estado"] == "confirmada"]
            if not mias:
                print("      aviso: no encontré la cita de mañana; puede que el agente eligiera otro día o siga "
                      "preguntando. Revisa la conversación de arriba.")
            else:
                c = mias[0]
                print(f"      cita {c['cita_id']} {c['fecha']} {c['hora']} · {c['marca']} {c['modelo']} · "
                      f"{c['mecanico_nombre']}")
                r = requests.post(f"{api}/panel/citas/{c['cita_id']}/estado", json={"estado": "cancelada"},
                                  headers={"x-panel-key": clave}, timeout=15)
                paso("limpieza: cita de prueba cancelada", r.status_code == 200)
        print("\nTodo OK")
        return 0
    except (Fallo, requests.RequestException, subprocess.CalledProcessError) as e:
        print(f"\nFALLÓ: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
