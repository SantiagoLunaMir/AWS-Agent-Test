import json
import uuid

import boto3
import pytest

from taller import config, handler, store


CLAVE_CHAT = "CHATDEMO23"


@pytest.fixture
def api(aws, monkeypatch):
    invocaciones = []

    class LambdaFalso:
        def invoke(self, **kw):
            invocaciones.append(json.loads(kw["Payload"]))

    ssm = boto3.client("ssm", region_name="us-east-2")
    ssm.put_parameter(Name="Taller-clave-chat", Value=CLAVE_CHAT, Type="SecureString")
    monkeypatch.setattr(config, "CHAT_PARAMETRO", "Taller-clave-chat")
    monkeypatch.setattr(handler, "_cliente", lambda s: LambdaFalso() if s == "lambda" else ssm)
    handler._claves.clear()
    yield invocaciones
    handler._claves.clear()


def test_recordatorio_con_fecha_legible(aws):
    cita = {"cita_id": "C-DEMO01", "session_id": "s-1", "nombre": "Ana", "email": "", "fecha": "2026-09-28",
            "hora": "14:00", "marca": "Bugatti", "modelo": "Veyron", "servicio": "cambio_aceite",
            "mecanico_nombre": "Jorge Pérez", "estado": "confirmada", "telefono": "5512345678"}
    store.guardar_cita(cita)
    handler.recordatorio({"cita_id": "C-DEMO01"}, None)
    texto = store.listar_mensajes("s-1")[0]["texto"]
    assert "es el lunes 28 de septiembre a las 14:00" in texto and "2026-09-28" not in texto


def post_chat(cuerpo, clave=CLAVE_CHAT):
    return handler.api({"routeKey": "POST /chat", "body": json.dumps(cuerpo), "headers": {"x-chat-key": clave}}, None)


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


def test_panel_lee_la_clave_de_parameter_store(aws, monkeypatch):
    boto3.client("ssm", region_name="us-east-2").put_parameter(Name="/Taller/clave-panel", Value="clave-demo",
                                                               Type="SecureString")
    monkeypatch.setattr(config, "PANEL_PARAMETRO", "/Taller/clave-panel")
    handler._claves.clear()
    evento = {"routeKey": "GET /panel/citas", "queryStringParameters": {"fecha": "2026-09-21"}}
    assert handler.api({**evento, "headers": {"x-panel-key": "otra"}}, None)["statusCode"] == 401
    assert handler.api({**evento, "headers": {"x-panel-key": "clave-demo"}}, None)["statusCode"] == 200
    handler._claves.clear()


@pytest.mark.parametrize("clave", ["", "OTRA", "chatdemo23", "ñandú"])
def test_chat_requiere_codigo_de_acceso(api, clave):
    r = post_chat(base(), clave=clave)
    assert r["statusCode"] == 401 and api == [], "sin el código correcto no se invoca al agente"
    for ruta in ("GET /mensajes", "POST /fotos"):
        assert handler.api({"routeKey": ruta, "headers": {"x-chat-key": clave}}, None)["statusCode"] == 401


def test_sin_parametro_configurado_el_chat_se_cierra(api, monkeypatch):
    monkeypatch.setattr(config, "CHAT_PARAMETRO", "")
    assert post_chat(base())["statusCode"] == 401


def test_clave_rotada_aplica_al_vencer_la_cache(api):
    assert post_chat(base())["statusCode"] == 202
    boto3.client("ssm", region_name="us-east-2").put_parameter(Name="Taller-clave-chat", Value="NUEVA23456",
                                                               Type="SecureString", Overwrite=True)
    assert post_chat(base())["statusCode"] == 202, "dentro de la caché sigue valiendo la anterior"
    valor, _ = handler._claves["Taller-clave-chat"]
    handler._claves["Taller-clave-chat"] = (valor, 0.0)  # la caché venció
    assert post_chat(base())["statusCode"] == 401
    assert post_chat(base(), clave="NUEVA23456")["statusCode"] == 202


def test_panel_comparte_el_chat_con_codigo_y_qr(api, monkeypatch):
    boto3.client("ssm", region_name="us-east-2").put_parameter(Name="Taller-clave-panel", Value="panel-demo",
                                                               Type="SecureString")
    monkeypatch.setattr(config, "PANEL_PARAMETRO", "Taller-clave-panel")
    monkeypatch.setattr(config, "CHAT_URL", "https://chat.ejemplo.net/")
    evento = {"routeKey": "GET /panel/chat"}
    assert handler.api({**evento, "headers": {"x-panel-key": CLAVE_CHAT}}, None)["statusCode"] == 401, \
        "el código del chat no abre el panel"
    r = handler.api({**evento, "headers": {"x-panel-key": "panel-demo"}}, None)
    datos = json.loads(r["body"])
    assert r["statusCode"] == 200 and datos["codigo"] == CLAVE_CHAT
    assert datos["enlace"] == f"https://chat.ejemplo.net/#clave={CLAVE_CHAT}"
    assert datos["qr"].startswith("data:image/svg+xml")
