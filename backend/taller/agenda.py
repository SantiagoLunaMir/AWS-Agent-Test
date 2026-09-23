"""Reglas del taller: horarios, disponibilidad y asignación de mecánicos (lógica pura, sin AWS)."""
from datetime import date, datetime, timedelta

HORARIO = {0: (9, 18), 1: (9, 18), 2: (9, 18), 3: (9, 18), 4: (9, 18), 5: (9, 14)}
ANTICIPACION_MIN = timedelta(hours=2)
HORIZONTE_DIAS = 30

MECANICOS = [
    {"id": "m1", "nombre": "Laura Méndez", "especialidades": ["asiaticas", "hibridos"]},
    {"id": "m2", "nombre": "Carlos Ruiz", "especialidades": ["americanas", "diesel"]},
    {"id": "m3", "nombre": "Ana Torres", "especialidades": ["europeas", "electricos"]},
    {"id": "m4", "nombre": "Jorge Pérez", "especialidades": ["general"]},
]

ORIGEN_MARCA = {
    "asiaticas": ["toyota", "nissan", "honda", "mazda", "mitsubishi", "subaru", "suzuki", "kia", "hyundai", "lexus",
                  "infiniti", "acura"],
    "americanas": ["ford", "chevrolet", "gmc", "dodge", "ram", "jeep", "chrysler", "tesla", "buick", "cadillac",
                   "lincoln"],
    "europeas": ["volkswagen", "vw", "seat", "audi", "bmw", "mercedes", "mercedes-benz", "peugeot", "renault",
                 "volvo", "fiat", "mini", "porsche", "cupra", "skoda", "citroen"],
}

SERVICIOS = ["diagnostico", "afinacion", "cambio_aceite", "frenos", "suspension", "electrico", "aire_acondicionado",
             "alineacion_balanceo", "otro"]

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]


class ErrorAgenda(ValueError):
    pass


def origen_marca(marca: str) -> str:
    m = (marca or "").strip().lower()
    for origen, marcas in ORIGEN_MARCA.items():
        if m in marcas:
            return origen
    return "general"


def fecha_legible(dia: date | str) -> str:
    """'2026-09-28' -> 'lunes 28 de septiembre', para los mensajes que lee el cliente."""
    if isinstance(dia, str):
        dia = date.fromisoformat(dia)
    return f"{DIAS[dia.weekday()]} {dia.day} de {MESES[dia.month - 1]}"


def cuando_legible(momento: datetime, ahora: datetime) -> str:
    """Fecha en palabras para el modelo: evita que interprete mal un timestamp ISO."""
    minutos = round((momento - ahora).total_seconds() / 60)
    if minutos < 60:
        return f"en unos {max(minutos, 1)} minutos"
    return f"el {fecha_legible(momento)} a las {momento:%H:%M}"


def slots_del_dia(dia: date) -> list[str]:
    if dia.weekday() not in HORARIO:
        return []
    abre, cierra = HORARIO[dia.weekday()]
    return [f"{h:02d}:00" for h in range(abre, cierra)]


def parsear_fecha(fecha: str, ahora: datetime) -> date:
    try:
        dia = date.fromisoformat(fecha)
    except (TypeError, ValueError):
        raise ErrorAgenda("Fecha inválida, usa el formato AAAA-MM-DD.")
    if dia < ahora.date():
        raise ErrorAgenda("Esa fecha ya pasó.")
    if dia > ahora.date() + timedelta(days=HORIZONTE_DIAS):
        raise ErrorAgenda(f"Solo agendamos con hasta {HORIZONTE_DIAS} días de anticipación.")
    if dia.weekday() not in HORARIO:
        raise ErrorAgenda("El taller no abre ese día (domingo).")
    return dia


def _ocupados_por_hora(citas_del_dia: list[dict]) -> dict[str, set[str]]:
    ocupados: dict[str, set[str]] = {}
    for c in citas_del_dia:
        if c.get("estado") == "cancelada":
            continue
        ocupados.setdefault(c["hora"], set()).add(c["mecanico_id"])
    return ocupados


def horarios_libres(dia: date, citas_del_dia: list[dict], ahora: datetime) -> list[str]:
    ocupados = _ocupados_por_hora(citas_del_dia)
    libres = []
    for hora in slots_del_dia(dia):
        inicio = datetime.combine(dia, datetime.strptime(hora, "%H:%M").time(), tzinfo=ahora.tzinfo)
        if inicio - ahora < ANTICIPACION_MIN:
            continue
        if len(ocupados.get(hora, set())) < len(MECANICOS):
            libres.append(hora)
    return libres


def horarios_sugeridos(libres: list[str], maximo: int = 3) -> list[str]:
    """Hasta `maximo` horarios repartidos en el día (mañana, mediodía, tarde) para no listar todos al cliente."""
    if len(libres) <= maximo:
        return libres
    paso = (len(libres) - 1) / (maximo - 1)
    return [libres[round(i * paso)] for i in range(maximo)]


def elegir_mecanico(marca: str, hora: str, citas_del_dia: list[dict]) -> dict | None:
    ocupados = _ocupados_por_hora(citas_del_dia).get(hora, set())
    carga = {}
    for c in citas_del_dia:
        if c.get("estado") != "cancelada":
            carga[c["mecanico_id"]] = carga.get(c["mecanico_id"], 0) + 1
    origen = origen_marca(marca)
    libres = [m for m in MECANICOS if m["id"] not in ocupados]
    if not libres:
        return None

    def puntaje(m):
        especialista = origen in m["especialidades"]
        generalista = "general" in m["especialidades"]
        return (-(2 if especialista else 1 if generalista else 0), carga.get(m["id"], 0), m["id"])

    return sorted(libres, key=puntaje)[0]


def validar_hora(dia: date, hora: str, ahora: datetime) -> datetime:
    if hora not in slots_del_dia(dia):
        raise ErrorAgenda(f"Hora fuera de horario. Horarios del día: {', '.join(slots_del_dia(dia))}.")
    inicio = datetime.combine(dia, datetime.strptime(hora, "%H:%M").time(), tzinfo=ahora.tzinfo)
    if inicio - ahora < ANTICIPACION_MIN:
        raise ErrorAgenda("Necesitamos al menos 2 horas de anticipación.")
    return inicio
