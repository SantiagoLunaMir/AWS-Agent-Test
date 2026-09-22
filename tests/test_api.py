import json
import uuid

import pytest

from taller import config, handler, store


@pytest.fixture
def api(aws, monkeypatch):
    invocaciones = []

    class LambdaFalso:
        def invoke(self, **kw):
            invocaciones.append(json.loads(kw["Payload"]))

    monkeypatch.setattr(handler, "_cliente", lambda s: LambdaFalso() if s == "lambda" else None)
    return invocaciones


def post_chat(cuerpo):
    return handler.api({"routeKey": "POST /chat", "body": json.dumps(cuerpo)}, None)


def base(**extra):
    return {"session_id": str(uuid.uuid4()), "texto": "Hola", "cliente": {"nombre": "Ana", "telefono": "55 1234 5678"},
            **extra}


def test_chat_encola_turno(api):
    cuerpo = base()
    r = post_chat(cuerpo)
    assert r["statusCode"] == 202
    assert api == [{"session_id": cuerpo["session_id"], "texto": "Hola", "foto_id": None}]
    conv = store.obtener_conversacion(cuerpo["session_id"])
    assert conv["estado"] == "pensando" and conv["cliente"]["telefono"] == "5512345678"


@pytest.mark.parametrize("malo", [
    {"session_id": "no-es-uuid"},
    {"cliente": {"nombre": "", "telefono": "55"}},
    {"texto": "x" * 5000},
    {"texto": "", "foto_id": None},
    {"foto_id": "../../secreto.jpg"},
])
def test_chat_valida_entrada(api, malo):
    assert post_chat(base(**malo))["statusCode"] == 400


def test_no_acepta_mensaje_mientras_piensa(api):
    cuerpo = base()
    post_chat(cuerpo)
    assert post_chat(cuerpo)["statusCode"] == 409


def test_limite_de_mensajes(api, monkeypatch):
    cuerpo = base()
    for i in range(config.MAX_TURNOS_POR_VENTANA):
        assert post_chat(cuerpo)["statusCode"] == 202
        store.marcar_estado(cuerpo["session_id"], "listo")
    assert post_chat(cuerpo)["statusCode"] == 429


def test_panel_requiere_clave(api):
    r = handler.api({"routeKey": "GET /panel/citas", "headers": {}}, None)
    assert r["statusCode"] == 401
