import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3
import pytest

from taller import herramientas, store, vision

AHORA = datetime(2026, 9, 21, 8, 0, tzinfo=ZoneInfo("America/Mexico_City"))
FOTO = "a" * 32 + ".jpg"


def nuevo_ctx(telefono="5512345678"):
    conv = store.obtener_conversacion(str(uuid.uuid4()))
    conv["cliente"] = {"nombre": "Ana", "telefono": telefono}
    return herramientas.Contexto(conv=conv, ahora=AHORA)


@pytest.fixture
def clasificacion(monkeypatch):
    resultado = {"es_vehiculo": True, "marca": "Toyota", "modelo": "Corolla", "tipo": "sedan", "color": "gris",
                 "confianza": 0.93, "observaciones": "Sin daños visibles"}
    monkeypatch.setattr(vision, "clasificar", lambda imagen, media_type: dict(resultado))
    return resultado


def subir_foto(ctx):
    boto3.client("s3", region_name="us-east-2").put_object(
        Bucket="fotos-test", Key=f"fotos/{ctx.session_id}/{FOTO}", Body=b"jpeg", ContentType="image/jpeg")


def run(ctx, tool, **entrada):
    salida, es_error = herramientas.ejecutar(ctx, tool, entrada)
    return (salida if es_error else json.loads(salida)), es_error


def preparar_vehiculo(ctx):
    subir_foto(ctx)
    run(ctx, "identificar_vehiculo", foto_id=FOTO)
    run(ctx, "confirmar_vehiculo", marca="Toyota", modelo="Corolla", confirmado_por_cliente=True)


def agendar(ctx, hora="10:00", confirmado=True):
    return run(ctx, "agendar_cita", fecha="2026-09-21", hora=hora, servicio="frenos",
               descripcion="rechinan", email="", horario_confirmado_por_cliente=confirmado)


def test_flujo_completo(aws, clasificacion):
    ctx = nuevo_ctx()
    subir_foto(ctx)
    ident, err = run(ctx, "identificar_vehiculo", foto_id=FOTO)
    assert not err and ident["marca"] == "Toyota"

    _, err = agendar(ctx)
    assert err, "no debe agendar sin vehículo confirmado"

    _, err = run(ctx, "confirmar_vehiculo", marca="Toyota", modelo="Corolla", confirmado_por_cliente=False)
    assert err
    run(ctx, "confirmar_vehiculo", marca="Toyota", modelo="Corolla 2020", confirmado_por_cliente=True)

    disp, _ = run(ctx, "consultar_disponibilidad", fecha="2026-09-21")
    assert "10:00" in disp["horarios_libres"] and "09:00" not in disp["horarios_libres"]

    _, err = agendar(ctx, confirmado=False)
    assert err, "no debe agendar sin confirmación del horario"

    cita, err = agendar(ctx)
    assert not err and cita["mecanico"] == "Laura Méndez"
    guardada = store.obtener_cita(cita["cita_id"])
    assert guardada["modelo"] == "Corolla 2020" and guardada["estado"] == "confirmada"
    assert ctx.conv["vehiculo"] is None

    mis, _ = run(ctx, "mis_citas")
    assert [c["cita_id"] for c in mis["citas"]] == [cita["cita_id"]]


def test_sin_foto_el_cliente_dice_marca_y_modelo(aws):
    ctx = nuevo_ctx()
    _, err = run(ctx, "confirmar_vehiculo", marca="  ", modelo="Mustang", confirmado_por_cliente=True)
    assert err, "sin marca no se registra"
    res, err = run(ctx, "confirmar_vehiculo", marca="Ford", modelo="Mustang", confirmado_por_cliente=True)
    assert not err and ctx.conv["vehiculo"]["origen"] == "cliente"
    cita, err = agendar(ctx)
    assert not err and cita["mecanico"] == "Carlos Ruiz"
    assert store.obtener_cita(cita["cita_id"])["foto_key"] == ""


def test_mismo_horario_asigna_otro_mecanico_y_se_llena(aws, clasificacion):
    mecanicos = []
    for i in range(4):
        ctx = nuevo_ctx(telefono=f"55000000{i:02d}")
        preparar_vehiculo(ctx)
        cita, err = agendar(ctx)
        assert not err
        mecanicos.append(cita["mecanico"])
    assert len(set(mecanicos)) == 4
    ctx = nuevo_ctx(telefono="5599999999")
    preparar_vehiculo(ctx)
    _, err = agendar(ctx)
    assert err


def test_limite_de_citas_activas(aws, clasificacion):
    ctx = nuevo_ctx()
    for hora in ("10:00", "11:00"):
        preparar_vehiculo(ctx)
        _, err = agendar(ctx, hora=hora)
        assert not err
    preparar_vehiculo(ctx)
    salida, err = agendar(ctx, hora="12:00")
    assert err and "máximo" in salida


def test_cancelar_libera_horario_y_valida_dueno(aws, clasificacion):
    ctx = nuevo_ctx()
    preparar_vehiculo(ctx)
    cita, _ = agendar(ctx)

    otro = nuevo_ctx(telefono="5511111111")
    _, err = run(otro, "cancelar_cita", cita_id=cita["cita_id"], confirmado_por_cliente=True)
    assert err, "un cliente no puede cancelar citas ajenas"

    _, err = run(ctx, "cancelar_cita", cita_id=cita["cita_id"], confirmado_por_cliente=False)
    assert err
    res, err = run(ctx, "cancelar_cita", cita_id=cita["cita_id"], confirmado_por_cliente=True)
    assert not err and res["estado"] == "cancelada"

    preparar_vehiculo(ctx)
    nueva, err = agendar(ctx)
    assert not err and nueva["mecanico"] == "Laura Méndez"


def test_foto_id_con_ruta_maliciosa(aws, clasificacion):
    salida, err = run(nuevo_ctx(), "identificar_vehiculo", foto_id="../../otra-sesion/foto.jpg")
    assert err and "inválido" in salida


def test_parametros_inesperados_devuelven_error(aws):
    salida, err = herramientas.ejecutar(nuevo_ctx(), "consultar_disponibilidad", {"dia": "2026-09-21"})
    assert err


def test_skill_se_carga_bajo_demanda(aws):
    res, err = run(nuevo_ctx(), "cargar_skill", nombre="cotizacion-servicios")
    assert not err and "Precios de referencia" in res["instrucciones"]


def test_vision_sanea_salida():
    limpio = vision.sanear({"es_vehiculo": True, "marca": "X" * 200, "modelo": "Y", "tipo": "tanque",
                            "color": "rojo", "confianza": 7, "observaciones": "ok"})
    assert len(limpio["marca"]) == 40 and limpio["tipo"] == "otro" and limpio["confianza"] == 1.0
