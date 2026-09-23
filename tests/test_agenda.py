from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from taller import agenda

TZ = ZoneInfo("America/Mexico_City")
LUNES_8AM = datetime(2026, 9, 21, 8, 0, tzinfo=TZ)


def cita(hora, mecanico_id, estado="confirmada"):
    return {"hora": hora, "mecanico_id": mecanico_id, "estado": estado}


def test_horario_sabado_y_domingo():
    assert agenda.slots_del_dia(date(2026, 9, 26))[-1] == "13:00"
    assert agenda.slots_del_dia(date(2026, 9, 27)) == []


def test_parsear_fecha_rechaza_pasado_domingo_y_lejanas():
    with pytest.raises(agenda.ErrorAgenda):
        agenda.parsear_fecha("2026-09-20", LUNES_8AM)
    with pytest.raises(agenda.ErrorAgenda):
        agenda.parsear_fecha("2026-09-27", LUNES_8AM)
    with pytest.raises(agenda.ErrorAgenda):
        agenda.parsear_fecha("2026-12-01", LUNES_8AM)
    with pytest.raises(agenda.ErrorAgenda):
        agenda.parsear_fecha("21/09/2026", LUNES_8AM)


def test_horarios_libres_respeta_anticipacion_y_ocupacion():
    llenas = [cita("11:00", m["id"]) for m in agenda.MECANICOS]
    libres = agenda.horarios_libres(date(2026, 9, 21), llenas, LUNES_8AM)
    assert "09:00" not in libres  # menos de 2 h de anticipación
    assert "10:00" in libres
    assert "11:00" not in libres  # todos los mecánicos ocupados


def test_cita_cancelada_libera_horario():
    llenas = [cita("11:00", m["id"], "cancelada") for m in agenda.MECANICOS]
    assert "11:00" in agenda.horarios_libres(date(2026, 9, 21), llenas, LUNES_8AM)


def test_asigna_especialista_luego_generalista_luego_menor_carga():
    assert agenda.elegir_mecanico("Toyota", "10:00", [])["id"] == "m1"
    assert agenda.elegir_mecanico("BMW", "10:00", [])["id"] == "m3"
    assert agenda.elegir_mecanico("Toyota", "10:00", [cita("10:00", "m1")])["id"] == "m4"
    assert agenda.elegir_mecanico("BYD", "10:00", [])["id"] == "m4"
    todos = [cita("10:00", m["id"]) for m in agenda.MECANICOS]
    assert agenda.elegir_mecanico("Toyota", "10:00", todos) is None


def test_validar_hora():
    with pytest.raises(agenda.ErrorAgenda):
        agenda.validar_hora(date(2026, 9, 21), "18:00", LUNES_8AM)
    assert agenda.validar_hora(date(2026, 9, 21), "12:00", LUNES_8AM).hour == 12


def test_cuando_legible():
    from datetime import timedelta
    assert agenda.cuando_legible(LUNES_8AM + timedelta(minutes=2), LUNES_8AM) == "en unos 2 minutos"
    assert agenda.cuando_legible(datetime(2026, 9, 22, 9, 0, tzinfo=TZ), LUNES_8AM) == \
        "el martes 22 de septiembre a las 09:00"


def test_fecha_legible():
    assert agenda.fecha_legible("2026-09-28") == "lunes 28 de septiembre"
    assert agenda.fecha_legible(date(2026, 10, 3)) == "sábado 3 de octubre"


def test_horarios_sugeridos_reparte_el_dia():
    dia = agenda.slots_del_dia(date(2026, 9, 28))
    assert agenda.horarios_sugeridos(dia) == ["09:00", "13:00", "17:00"]
    assert agenda.horarios_sugeridos(["10:00", "11:00"]) == ["10:00", "11:00"]
    assert agenda.horarios_sugeridos(["09:00", "10:00", "11:00", "12:00"]) == ["09:00", "11:00", "12:00"]
