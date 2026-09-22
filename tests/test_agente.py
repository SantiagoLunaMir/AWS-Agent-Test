import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from taller import agente, config, herramientas, store


def respuesta(stop_reason, *bloques):
    return {"stopReason": stop_reason, "output": {"message": {"role": "assistant", "content": list(bloques)}},
            "usage": {"inputTokens": 10, "outputTokens": 5}}


def texto(t):
    return {"text": t}


def uso(id_, nombre, entrada):
    return {"toolUse": {"toolUseId": id_, "name": nombre, "input": entrada}}


class BedrockFalso:
    """Imita bedrock-runtime.converse: devuelve respuestas en orden y guarda cada petición."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []

    def converse(self, **kwargs):
        self.llamadas.append(json.loads(json.dumps(kwargs)))
        return self.respuestas.pop(0)


def ctx():
    conv = store.obtener_conversacion(str(uuid.uuid4()))
    conv["cliente"] = {"nombre": "Ana", "telefono": "5512345678"}
    return herramientas.Contexto(conv=conv, ahora=datetime(2026, 9, 21, 8, tzinfo=ZoneInfo("America/Mexico_City")))


def test_loop_ejecuta_herramienta_y_responde(aws, monkeypatch):
    falso = BedrockFalso([
        respuesta("tool_use", texto("Reviso."), uso("tu_1", "consultar_disponibilidad", {"fecha": "2026-09-21"})),
        respuesta("end_turn", texto("Tengo *10:00* libre.")),
    ])
    monkeypatch.setattr(agente, "cliente", lambda: falso)
    c = ctx()
    assert agente.responder(c, "¿Tienen lugar hoy?", None) == "Tengo *10:00* libre."

    segunda = falso.llamadas[1]["messages"]
    resultado = segunda[-1]["content"][0]["toolResult"]
    assert resultado["toolUseId"] == "tu_1" and resultado["status"] == "success"
    assert "10:00" in resultado["content"][0]["text"]
    assert [m["role"] for m in c.conv["history"]] == ["user", "assistant", "user", "assistant"]


def test_peticion_usa_modelo_cache_y_herramientas(aws, monkeypatch):
    falso = BedrockFalso([respuesta("end_turn", texto("Hola"))])
    monkeypatch.setattr(agente, "cliente", lambda: falso)
    monkeypatch.setattr(config, "MODEL_ID", "us.amazon.nova-2-lite-v1:0")
    monkeypatch.setattr(config, "RAZONAMIENTO", "low")
    agente.responder(ctx(), "hola", None)
    peticion = falso.llamadas[0]
    assert peticion["modelId"] == "us.amazon.nova-2-lite-v1:0"
    assert peticion["system"][1] == {"cachePoint": {"type": "default"}}
    assert "Fecha y hora actual" in peticion["system"][2]["text"]
    assert len(peticion["toolConfig"]["tools"]) == len(herramientas.IMPLEMENTACIONES)
    assert peticion["additionalModelRequestFields"]["reasoningConfig"]["maxReasoningEffort"] == "low"


def test_razonamiento_solo_se_envia_a_nova_2(aws, monkeypatch):
    falso = BedrockFalso([respuesta("end_turn", texto("Hola"))])
    monkeypatch.setattr(agente, "cliente", lambda: falso)
    monkeypatch.setattr(config, "MODEL_ID", "openai.gpt-oss-120b-1:0")
    agente.responder(ctx(), "hola", None)
    assert "additionalModelRequestFields" not in falso.llamadas[0]


def test_foto_llega_como_referencia_no_como_imagen(aws, monkeypatch):
    falso = BedrockFalso([respuesta("end_turn", texto("¡Gracias!"))])
    monkeypatch.setattr(agente, "cliente", lambda: falso)
    agente.responder(ctx(), "", "b" * 32 + ".jpg")
    contenido = falso.llamadas[0]["messages"][-1]["content"]
    assert all(list(b) == ["text"] for b in contenido)
    assert "foto_id" in contenido[0]["text"]


def test_error_de_herramienta_se_marca_como_error(aws, monkeypatch):
    falso = BedrockFalso([
        respuesta("tool_use", uso("tu_1", "confirmar_vehiculo",
                                  {"marca": "VW", "modelo": "Vocho", "confirmado_por_cliente": "true"})),
        respuesta("end_turn", texto("¿Me confirmas la marca?")),
    ])
    monkeypatch.setattr(agente, "cliente", lambda: falso)
    c = ctx()
    agente.responder(c, "Es un vocho", None)
    resultado = falso.llamadas[1]["messages"][-1]["content"][0]["toolResult"]
    assert resultado["status"] == "error" and "boolean" in resultado["content"][0]["text"]
    assert not c.conv.get("vehiculo")


def test_contenido_bloqueado_no_deja_basura_en_historial(aws, monkeypatch):
    falso = BedrockFalso([respuesta("content_filtered")])
    monkeypatch.setattr(agente, "cliente", lambda: falso)
    c = ctx()
    assert "no puedo" in agente.responder(c, "algo", None)
    assert [m["role"] for m in c.conv["history"]] == ["user"]


def test_llamada_mal_formada_se_reintenta(aws, monkeypatch):
    falso = BedrockFalso([respuesta("malformed_tool_use"), respuesta("end_turn", texto("Listo"))])
    monkeypatch.setattr(agente, "cliente", lambda: falso)
    assert agente.responder(ctx(), "hola", None) == "Listo"
    assert len(falso.llamadas) == 2


def test_razonamiento_y_etiquetas_thinking_no_llegan_al_cliente(aws, monkeypatch):
    falso = BedrockFalso([respuesta("end_turn", {"reasoningContent": {"reasoningText": {"text": "[REDACTED]"}}},
                                    texto("<thinking>debo saludar</thinking>¡Hola **Ana**!"))])
    monkeypatch.setattr(agente, "cliente", lambda: falso)
    c = ctx()
    assert agente.responder(c, "hola", None) == "¡Hola *Ana*!"
    assert all("reasoningContent" not in b for m in c.conv["history"] for b in m["content"])


def test_recorte_de_historial_no_separa_tool_result():
    hist = []
    for i in range(40):
        hist += [{"role": "user", "content": [{"text": f"m{i}"}]},
                 {"role": "assistant", "content": [{"toolUse": {"toolUseId": f"t{i}"}}]},
                 {"role": "user", "content": [{"toolResult": {"toolUseId": f"t{i}"}}]}]
    recortado = agente.recortar_historial(hist)
    assert len(recortado) <= agente.LIMITE_HISTORIAL
    assert "text" in recortado[0]["content"][0]


def test_historial_con_formato_anterior_se_descarta():
    viejo = [{"role": "user", "content": [{"type": "text", "text": "hola"}]}]
    assert agente.recortar_historial(viejo) == []
