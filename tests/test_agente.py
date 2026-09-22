import json
import uuid
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from taller import agente, herramientas, store


def bloque(**campos):
    return SimpleNamespace(**campos, to_dict=lambda: dict(campos))


def respuesta(stop_reason, *bloques):
    return SimpleNamespace(stop_reason=stop_reason, content=list(bloques))


class ClienteFalso:
    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []
        self.messages = self

    def create(self, **kwargs):
        self.llamadas.append(json.loads(json.dumps(kwargs["messages"])))
        return self.respuestas.pop(0)


def ctx():
    conv = store.obtener_conversacion(str(uuid.uuid4()))
    conv["cliente"] = {"nombre": "Ana", "telefono": "5512345678"}
    return herramientas.Contexto(conv=conv, ahora=datetime(2026, 9, 21, 8, tzinfo=ZoneInfo("America/Mexico_City")))


def test_loop_ejecuta_herramienta_y_responde(aws, monkeypatch):
    falso = ClienteFalso([
        respuesta("tool_use",
                  bloque(type="text", text="Reviso."),
                  bloque(type="tool_use", id="tu_1", name="consultar_disponibilidad", input={"fecha": "2026-09-21"})),
        respuesta("end_turn", bloque(type="text", text="Tengo *10:00* libre.")),
    ])
    monkeypatch.setattr(agente, "cliente", lambda: falso)
    c = ctx()
    texto = agente.responder(c, "¿Tienen lugar hoy?", None)

    assert texto == "Tengo *10:00* libre."
    segunda = falso.llamadas[1]
    resultado = segunda[-1]["content"][0]
    assert resultado["type"] == "tool_result" and resultado["tool_use_id"] == "tu_1"
    assert "10:00" in resultado["content"] and resultado["is_error"] is False
    assert [m["role"] for m in c.conv["history"]] == ["user", "assistant", "user", "assistant"]


def test_foto_llega_como_referencia_no_como_imagen(aws, monkeypatch):
    falso = ClienteFalso([respuesta("end_turn", bloque(type="text", text="¡Gracias!"))])
    monkeypatch.setattr(agente, "cliente", lambda: falso)
    agente.responder(ctx(), "", "b" * 32 + ".jpg")
    contenido = falso.llamadas[0][-1]["content"]
    assert all(b["type"] == "text" for b in contenido)
    assert "foto_id" in contenido[0]["text"]


def test_rechazo_no_deja_basura_en_historial(aws, monkeypatch):
    falso = ClienteFalso([respuesta("refusal")])
    monkeypatch.setattr(agente, "cliente", lambda: falso)
    c = ctx()
    texto = agente.responder(c, "algo", None)
    assert "no puedo" in texto
    assert [m["role"] for m in c.conv["history"]] == ["user"]


def test_recorte_de_historial_no_separa_tool_result():
    hist = []
    for i in range(40):
        hist += [{"role": "user", "content": [{"type": "text", "text": f"m{i}"}]},
                 {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}"}]},
                 {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}"}]}]
    recortado = agente.recortar_historial(hist)
    assert len(recortado) <= agente.LIMITE_HISTORIAL
    assert recortado[0]["content"][0]["type"] == "text"
