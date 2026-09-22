"""Skills con divulgación progresiva: el agente solo ve nombre + descripción y carga el SKILL.md cuando lo necesita."""
from functools import lru_cache
from pathlib import Path

DIRECTORIO = Path(__file__).parent / "skills"


def _frontmatter(texto: str) -> dict:
    if not texto.startswith("---"):
        return {}
    cabecera = texto.split("---", 2)[1]
    datos = {}
    for linea in cabecera.strip().splitlines():
        clave, _, valor = linea.partition(":")
        datos[clave.strip()] = valor.strip()
    return datos


@lru_cache(maxsize=1)
def catalogo() -> dict[str, dict]:
    skills = {}
    for archivo in sorted(DIRECTORIO.glob("*/SKILL.md")):
        texto = archivo.read_text(encoding="utf-8")
        meta = _frontmatter(texto)
        skills[meta["name"]] = {"descripcion": meta["description"], "contenido": texto.split("---", 2)[2].strip()}
    return skills


def indice() -> str:
    return "\n".join(f"- {nombre}: {s['descripcion']}" for nombre, s in catalogo().items())


def cargar(nombre: str) -> str | None:
    skill = catalogo().get(nombre)
    return skill["contenido"] if skill else None
