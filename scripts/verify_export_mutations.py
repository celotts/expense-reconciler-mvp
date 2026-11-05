#!/usr/bin/env python3
"""Verificacion por mutacion de la defensa contra inyeccion de formulas.

Por que existe
--------------

`AGENTS.md` §Reglas 7, `README.md` y `docs/contrato-producto.md` §10 prometen que
esta defensa tiene un verificador de mutaciones. **El script no existia.** La
defensa si estaba probada (`tests/integration/test_export_injection.py`, 10
tests), pero "probada" y "la prueba muere si quitas la defensa" no son lo mismo:
un test puede pasar porque el codigo esta bien, y no porque el test mire.

Que se protege
--------------

`app/core/texto.py` tiene dos defensas y confundirlas es el error:

- `neutralizar_formula` -> CSV. Antepone un apostrofo, que el lector se come.
- `forzar_texto` -> XLSX. Pone `data_type = "s"` y openpyxl no puede escribir una
  formula en una celda de texto. Es la que sostiene la seguridad de verdad.

Aplicarlas al reves es un fallo de los dos lados: apostrofo en XLSX mete basura
visible en los datos, y `data_type` en CSV no hace nada porque el formato no
tiene tipos de celda.

Y una nota sobre la primera version de este script
--------------------------------------------------

La primera version mutaba con `shutil.copy2` a una copia temporal y reportaba
"no se encontro el patron" sobre un archivo que SI lo tenia, con el hash
correcto. El problema no era el patron: era la pareja de copiar y restaurar, que
podia dejar el archivo en un estado intermedio y medir sobre el. La version de
abajo no usa el sistema de archivos para el original: lo lee UNA vez a memoria y
restaura escribiendo esa cadena. Si alguna vez no restaura bien, el hash del
guard lo dice antes de que se mida cualquier cosa.

Uso:
    python3 scripts/verify_export_mutations.py

Debe terminar con "Las N mutaciones mueren".
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
TEXTO = RAIZ / "app" / "core" / "texto.py"
TESTS = [
    "tests/integration/test_export_injection.py",
    "tests/unit/test_texto.py",
    "tests/unit/test_export_service.py",
]

# El original, leido una sola vez. Todo el script trabaja contra esta cadena, no
# contra el archivo: la restauracion no puede fallar porque no hay nada que
# copiar, y el hash de abajo avisa si alguna vez no se restauro bien.
ORIGEN = TEXTO.read_text(encoding="utf-8")
ORIGEN_HASH = hashlib.sha256(ORIGEN.encode("utf-8")).hexdigest()[:8]


def _estado() -> str:
    return hashlib.sha256(
        TEXTO.read_bytes()
    ).hexdigest()[:8]


def _restaurar() -> bool:
    TEXTO.write_text(ORIGEN, encoding="utf-8")
    return _estado() == ORIGEN_HASH


def _correr() -> bool:
    """True si los tests pasan, es decir, si la defensa NO esta presente."""
    existentes = [t for t in TESTS if (RAIZ / t).exists()]
    r = subprocess.run(
        [sys.executable, "-m", "pytest", *existentes, "-q", "--no-header"],
        cwd=RAIZ, capture_output=True, text=True,
    )
    return r.returncode == 0


# (nombre, porque, viejo, nuevo)
MUTACIONES = [
    (
        "forzar_texto deja de forzar texto",
        "las celdas vuelven a ser tipo formula y Excel las ejecuta",
        # El salto de linea y la sangria NO son decorativos. El docstring de
        # esta misma funcion menciona `celda.data_type = "s"` al explicar que
        # es la unica linea que sostiene la defensa, y un replace por primera
        # aparicion mutaba la PROSA en vez del codigo: la mutencia "sobrevivia"
        # sin haber roto nada. Se encontro porque el verificador decia que una
        # mutacion era inocua cuando a mano, aplicandola al archivo, cuatro
        # tests morian. Anclar al principio de linea es lo que lo evita.
        '\n            celda.data_type = "s"\n',
        '\n            celda.data_type = "f"\n',
    ),
    (
        "forzar_texto no mira nada",
        "la defensa no hace absolutamente nada",
        "for fila in worksheet.iter_rows():",
        "for fila in []:",
    ),
    (
        "neutralizar_formula deja de neutralizar",
        "el CSV volveria a llevar la formula cruda",
        "return _PREFIJO_TEXTO + valor",
        "return valor",
    ),
    (
        "el apostrofo de CSV se aplica a XLSX",
        "regla 7 al reves: el contador ve el apostrofo pegado en la celda",
        '\n            celda.data_type = "s"\n',
        '\n            celda.value = f"\'{celda.value}"\n',
    ),
    (
        "es_formula_peligrosa acepta cualquier cosa",
        "toda cadena pasa por filtro, y = se cuela",
        "def es_formula_peligrosa(valor: Any) -> bool:",
        "def es_formula_peligrosa(valor: Any) -> bool:\n    return True\n\ndef _ignore(v):",
    ),
]


def main() -> int:
    if not _correr():
        print("La linea base ya esta en rojo. Arregla eso antes de medir nada.")
        return 1

    sobrevividas: list[str] = []
    for nombre, porque, viejo, nuevo in MUTACIONES:
        # Si una ronda anterior no restauro, no se mide nada: el resultado
        # seria sobre un archivo que no es el del repo.
        if not _restaurar():
            print(f"  ABORTA    el archivo quedo modificado antes de '{nombre}'")
            return 1

        texto = ORIGEN
        if viejo not in texto:
            print(f"  ERROR    {nombre}: el patron no existe en el archivo")
            print(f"           {viejo!r}")
            sobrevividas.append(nombre)
            continue

        TEXTO.write_text(texto.replace(viejo, nuevo, 1), encoding="utf-8")
        sobrevive = _correr()
        restaurado = _restaurar()

        if not restaurado:
            print(f"  ABORTA    no se pudo restaurar tras '{nombre}'")
            return 1

        if sobrevive:
            sobrevividas.append(nombre)
            print(f"  VIVA     {nombre}")
            print(f"           {porque}")
        else:
            print(f"  murio    {nombre}")

    print()
    if sobrevividas:
        print(f"{len(sobrevividas)} de {len(MUTACIONES)} mutaciones SOBREVIVIERON:")
        for s in sobrevividas:
            print(f"  - {s}")
        print()
        print("Una defensa que no muere cuando se rompe no es una defensa, y el")
        print("documento que promete el verificador tiene que encontrarlo.")
        return 1

    print(
        f"Las {len(MUTACIONES)} mutaciones mueren. "
        "Los tests miran lo que dicen mirar."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
