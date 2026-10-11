#!/usr/bin/env python3
"""Verificacion por mutacion de la binarizacion adaptativa y del modelo de linea.

Por que existe
--------------

Dos defensas nuevas que no se pueden distinguir de "probadas" sin mutarlas:

1. **`app/services/ocr.py`: la binarizacion es una VARIANTE PUNTUADA.** La
   garantia que se quiere es que no puede empeorar lo que ya funciona, y eso no
   se lee en el codigo: se lee en que el resultado con la etapa es identico al
   resultado sin ella cuando la lectura normal ya era buena. Un test que pasara
   con un reemplazo directo de `_preparar` no probaria nada.

2. **`app/services/parser_lineas.py`: `LineaDeTicket` es una PUERTA.** El
   `float` que se quito de `como_items` no fallaba en ninguna prueba —los
   numeros se veian bien en un `assert`— porque `float(1.15) == 1.15` es verdad.
   Lo que cambio es el tipo que viaja a la columna JSON.

Que se protege, en una linea cada una
-------------------------------------

- `_binarizar` se llama en la lectura (si no, el cambio es decorativo).
- La etapa compite con `_puntuar` y no gana siempre.
- El zoom solo se aplica a lo pequeno, y con tope.
- `--oem 3` se dice a mano.
- OpenCV se reporta en `disponibilidad()`.
- `como_items` serializa `Decimal`, no `float`.
- La linea pasa por el modelo antes de volverse dict.
- El modelo no acepta claves que no existen, ni importes no positivos.
- Lo que se descarta se dice en el log, y se cuenta.

Lo que este script NO verifica
------------------------------

**Que la binarizacion mejore la exactitud.** Eso no se puede verificar aqui y
tampoco deberia: necesita `verdad.json` rotulado a mano y
`scripts/medir_precision_ocr.py`. Lo que este script verifica es la garantia mas
pequena y mas fuerte: **la etapa no puede empeorar la lectura existente**, y por
eso se puede dejar puesta antes de tener la medicion.

Uso:
    python3 scripts/verify_ocr_mutations.py

Debe terminar con "Las N mutaciones mueren".

Nota sobre las dos ultimas
--------------------------

Las dos ultimas mutaciones (`items: normalizar descripcion` y `items: permitir
precio negativo`) existen por una razon concreta: la primera se anclo al codigo y
NO al docstring, porque el docstring de la clase menciona `is_finite` y una
mutacion anterior porbusqueda de palabra cambiaria la PROSA en vez del codigo. Es
el mismo modo de fallo que el que documento `verify_export_mutations.py` en su
linea 92: una mutacion que sobrevive porque no rompio nada.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

OCR = RAIZ / "app" / "services" / "ocr.py"
LINEAS = RAIZ / "app" / "services" / "parser_lineas.py"

TESTS_OCR = ["tests/unit/test_binarizacion_ocr.py"]
# `test_parser_lineas.py` se borro el 2026-10-10 con el codigo que cubria. Y
# `_correr` filtra los paths que no existen **en silencio**, asi que dejarlo aqui
# habria hecho que estas mutaciones se midieran contra un archivo de menos sin que
# nada lo dijera: exactamente el fallo que `verify_capture_mutations.py` ya sufrio
# una vez. Ver `docs/known-issues.md` §35.
TESTS_LINEAS = ["tests/unit/test_linea_de_ticket.py"]
# Las confusiones de digito y la verificacion de la suma viven en su propio
# archivo porque miden una sola idea: no se afirma un numero, se ofrece como
# candidata y lo judge el comprobante.
TESTS_VERIF = ["tests/unit/test_confusiones_y_verificacion.py"]

# El original de cada archivo, leido UNA vez. Todo el script trabaja contra estas
# cadenas: la restauracion no puede fallar porque no hay nada que copiar, y el
# hash de abajo avisa si alguna vez no se restauro bien.
ORIGEN_OCR = OCR.read_text(encoding="utf-8")
ORIGEN_LINEAS = LINEAS.read_text(encoding="utf-8")
HASH_OCR = hashlib.sha256(ORIGEN_OCR.encode("utf-8")).hexdigest()[:8]
HASH_LINEAS = hashlib.sha256(ORIGEN_LINEAS.encode("utf-8")).hexdigest()[:8]


def _restaurar() -> bool:
    OCR.write_text(ORIGEN_OCR, encoding="utf-8")
    LINEAS.write_text(ORIGEN_LINEAS, encoding="utf-8")
    return (
        hashlib.sha256(OCR.read_bytes()).hexdigest()[:8] == HASH_OCR
        and hashlib.sha256(LINEAS.read_bytes()).hexdigest()[:8] == HASH_LINEAS
    )


def _correr(tests: list[str]) -> bool:
    """True si los tests pasan, es decir, si la defensa NO esta presente."""
    existentes = [t for t in tests if (RAIZ / t).exists()]
    if not existentes:
        print(f"  ERROR    no existe ninguno de {tests}")
        return False
    r = subprocess.run(
        [sys.executable, "-m", "pytest", *existentes, "-q", "--no-header", "-x"],
        cwd=RAIZ, capture_output=True, text=True,
    )
    return r.returncode == 0


# (nombre, porque, archivo, viejo, nuevo, tests)
MUTACIONES = [
    # --- La etapa de binarizacion -----------------------------------------
    (
        "la binarizacion no se lee nunca",
        "el cambio es decorativo: la etapa existe y no se ejecuta",
        OCR,
        "\n    if mejor is not None and not _es_buena(mejor):\n        binarizada = _binarizar(base)",
        "\n    if False and mejor is not None and not _es_buena(mejor):\n        binarizada = _binarizar(base)",
        TESTS_OCR,
    ),
    (
        "la binarizacion gana siempre",
        "una etapa que se queda con lo peor es un retroceso, no una mejora",
        OCR,
        "                if mejor is None or lectura.puntos > mejor.puntos:\n                    # El puntaje",
        "                if True:\n                    # El puntaje",
        TESTS_OCR,
    ),
    (
        "el zoom se aplica a todo",
        "reescalar una foto que `_preparar` ya dejo bien desharca el trabajo de bajar a 2000",
        OCR,
        "if ancho < ANCHO_MINIMO_PARA_ZOOM:",
        "if True:",
        TESTS_OCR,
    ),
    (
        "el zoom no tiene tope",
        "una foto de 1100 px se va a 4400 y Tesseract paga por pixel",
        OCR,
        "destino = min(ancho * ZOOM_SIN_PIXELES, ANCHO_MAXIMO_PREPARADO)",
        "destino = ancho * ZOOM_SIN_PIXELES",
        TESTS_OCR,
    ),
    (
        "`--oem 3` deja de decirse",
        "el motor vuelve a depender de la version de Tesseract instalada",
        OCR,
        'return f"--oem {OEM_TESSERACT} --psm {modo}"',
        'return f"--psm {modo}"',
        TESTS_OCR,
    ),
    (
        "OpenCV no se reporta en la disponibilidad",
        "el operador no puede preguntar por que su foto se lee igual que antes",
        OCR,
        '        "opencv": opencv,',
        "",
        TESTS_OCR,
    ),

    # --- El modelo de linea ----------------------------------------------
    #
    # Tres de las que hubo aqui se quitaron el 2026-10-10: "`como_items` vuelve a
    # `float`", "lo descartado no se dice" y "lo descartado no se cuenta". Las tres
    # mutateaban codigo de `parser_lineas.py` que **ningun caller en `app/`**
    # invocaba, asi que protectan una rama que el producto no ejecuta: el
    # verificador reportaba defensa sobre una defensa que no corria. Se borro el
    # codigo y con el las tres. Ver el docstring de `app/services/parser_lineas.py`.
    (
        "la puerta de validacion se quita",
        "una linea ilegible entra al inventario como si fuera una compra",
        LINEAS,
        "                LineaDeTicket(\n"
        '                    description=str(cruda.get("description") or ""),\n',
        "                LineaDeTicket.model_construct(\n"
        '                    description=str(cruda.get("description") or ""),\n',
        TESTS_LINEAS + TESTS_VERIF,
    ),
    (
        "el modelo acepta claves desconocidas",
        "un `importe` en vez de `total` deja la linea sin importe y entra al kardex",
        LINEAS,
        'model_config = ConfigDict(extra="forbid")',
        "model_config = ConfigDict(extra='ignore')",
        TESTS_LINEAS,
    ),
    (
        "la cantidad puede ser cero o negativa",
        "una compra de 0 se guarda como compra y suma stock",
        LINEAS,
        '        if v <= 0:\n            raise ValueError(f"cantidad debe ser positiva, leida {v}")\n',
        "",
        TESTS_LINEAS,
    ),
    (
        "el importe puede ser cero o negativo",
        "la compra de 0.00 entra al inventario, y la aritmetica la acepta porque cuadra",
        LINEAS,
        '        if v <= 0:\n            raise ValueError(f"importe debe ser positivo, leido {v}")\n',
        "",
        TESTS_LINEAS,
    ),
    (
        "el precio puede ser negativo",
        "una devolucion leida como precio entra como si fuera una compra",
        LINEAS,
        '        if v <= 0:\n            raise ValueError(f"precio unitario debe ser positivo, leido {v}")\n',
        "",
        TESTS_LINEAS,
    ),
    (
        "la descripcion no se normaliza",
        "`LLAVE  DE  TILDE` y `LLAVE DE TILDE` se guardan como dos productos distintos",
        LINEAS,
        '        limpio = " ".join(v.split())\n'
        '        if not limpio.strip(" -_."):\n'
        '            raise ValueError("descripcion sin caracteres utiles")\n'
        "        return limpio",
        "        return v",
        TESTS_LINEAS,
    ),
    (
        "el OCR deja de registrar el decodificador de HEIC",
        "las fotos de iPhone dejan de abrirse: _preparar devuelve los bytes "
        "crudos y pytesseract lanza 'TypeError: Unsupported image object', que "
        "no menciona HEIC",
        OCR,
        "from app.core import heif as _heif  # noqa: F401\n",
        "",
        TESTS_OCR,
    ),
    (
        "la verificacion no calcula la suma",
        "la suma de items sale siempre None y el check no comprueba nada",
        LINEAS,
        "        self._suma = suma\n",
        "        self._suma = None\n",
        TESTS_VERIF,
    ),
    (
        "`None` se confunde con `False`",
        "un ticket sin lineas se reporta como 'se comprobo y no cuadra', que "
        "pide otra accion que 'no se pudo comprobar'",
        LINEAS,
        "        self._cuadra = None\n",
        "        self._cuadra = False\n",
        TESTS_VERIF,
    ),
]


def main() -> int:
    base_ocr = _correr(TESTS_OCR)
    base_lineas = _correr(TESTS_LINEAS)
    if not (base_ocr and base_lineas):
        print("La linea base ya esta en rojo. Arregla eso antes de medir nada.")
        print(f"  ocr={base_ocr}  lineas={base_lineas}")
        return 1

    print(
        "Las pruebas del OCR usan un doble de cv2 cuando OpenCV no esta "
        "instalado,\nasi que las quince se miden en cualquier maquina: sin el "
        "paquete, lo que se\nprueba es la GEOMETRIA del reescalado y la "
        "decision de competir, no el\numbral en si. Ver `con_cv2` en "
        "tests/unit/test_binarizacion_ocr.py."
    )
    print()

    sobrevividas: list[str] = []
    for nombre, porque, archivo, viejo, nuevo, tests in MUTACIONES:
        # Si una ronda anterior no restauro, no se mide nada: el resultado seria
        # sobre un archivo que no es el del repo.
        if not _restaurar():
            print(f"  ABORTA    el codigo quedo modificado antes de '{nombre}'")
            return 1

        original = ORIGEN_OCR if archivo == OCR else ORIGEN_LINEAS
        if viejo not in original:
            print(f"  ERROR    {nombre}: el patron no existe en el archivo")
            print(f"           {viejo!r}")
            sobrevividas.append(nombre)
            continue

        archivo.write_text(original.replace(viejo, nuevo, 1), encoding="utf-8")
        # La otra mitad del par se restaura antes de correr: si `ocr.py` esta
        # mutado y corremos los tests de `parser_lineas.py` (o al reves), se
        # mide sobre un estado que no existe en el repo.
        otro = LINEAS if archivo == OCR else OCR
        otro.write_text(
            ORIGEN_LINEAS if archivo == OCR else ORIGEN_OCR, encoding="utf-8"
        )
        sobrevive = _correr(tests)
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
    print()
    print("Lo que esto NO dice: que la binarizacion mejore la exactitud. Eso se")
    print("mide con scripts/medir_precision_ocr.py contra verdad.json, y §21 de")
    print("docs/known-issues.md sigue siendo la medicion vigente del OCR.")
    return 0


if __name__ == "__main__":
    sys.exit(main())