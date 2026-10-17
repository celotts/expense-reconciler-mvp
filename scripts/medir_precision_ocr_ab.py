#!/usr/bin/env python3
"""A/B del parser sobre UN MISMO OCR.

POR QUE ESTE SCRIPT Y NO DOS CORRIDAS DE `medir_precision_ocr.py`

Porque el OCR **no es determinista entre corridas**. MEDIDO en la foto de DSW:
la corrida guardada en `tickets.raw_text` decia `SUBTOTAL 279.33` y una nueva
dijo `SUBTOTAL 279.93`. Es el mismo archivo y el mismo motor.

Consecuencia directa: correr el script de medicion una vez antes de un cambio de
parser y otra despues **no mide el cambio**. Mide el cambio MAS el ruido del
motor, y con 7 fotos ese ruido es del mismo orden que el efecto. Un numero que
sale de ahi no se puede citar, y un numero que no se puede citar no sirve para
decidir nada.

Este script lee cada foto **una vez** y guarda el texto en
`/tickets/_medida_ocr_cache.json`. Despues se puede cambiar el parser y volver a
evaluar **ese mismo texto**, que es la unica forma de que la diferencia sea
atribuible al cambio.

LO QUE ESTE A/B MIDIO, Y HAY QUE DECIRLO ANTES DE QUE NADIE LO CITE

Con las correcciones de fecha de `parser_service.py`, sobre las 7 fotos
rotuladas: **cero diferencia en los seis campos.** Ni un punto. La razon esta
escrita en el test que las acompanha
(`tests/unit/test_parser_service.py::TestLasDosMarcasQueFaltabanEnLaCabecera`):
en esas 7 fotos la fecha sencillamente no esta en el texto que devolvio el OCR,
y ahi ningun parser la puede recuperar. Las correcciones no se equivocan; no
tienen donde ganar.

COMO SE USA

    # 1. con las fotos rotuladas en una carpeta
    docker compose exec expense-api python scripts/medir_precision_ocr_ab.py "CON EL CAMBIO"

    # 2. cambiar el parser
    # 3. otra vez, y sale el A/B porque el OCR ya esta cacheado
    docker compose exec expense-api python scripts/medir_precision_ocr_ab.py "SIN EL CAMBIO"

CUIDADO CON LA CACHE

Vive en `/tmp` del contenedor, asi que no la ve ni el escaner ni el repo. Se
borra sola cuando el contenedor se recrea, que es justo cuando hay que volver a
correr el OCR.

COMO SE USA

    # 1. con las fotos rotuladas en una carpeta dentro de /tickets
    docker compose exec expense-api python scripts/medir_precision_ocr_ab.py \
        /tickets/_medida "CON EL CAMBIO"

    # 2. cambiar el parser
    # 3. otra vez, y sale el A/B porque el OCR ya esta cacheado
    docker compose exec expense-api python scripts/medir_precision_ocr_ab.py \
        /tickets/_medida "SIN EL CAMBIO"
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, "/app/scripts")
from medir_precision_ocr import (  # noqa: E402
    CAMPOS,
    _comparable,
    _entradas_de_verdad,
    compara,
    selecciona_fotos,
)

RAIZ = Path(__file__).resolve().parents[1]
# `/tmp` DEL CONTENEDOR, a proposito. No en la carpeta del escaner: un `.json`
# ahi lo ve el escaner y lo reporta `NO_SOPORTADO` en cada corrida. No en el
# repo: un archivo de medicion con los tickets de una persona no se versiona.
CACHE = Path("/tmp/_medida_ocr_cache.json")


def ocr_una_vez(fotos: list[Path]) -> dict[str, str]:
    """El texto de cada foto, corriendo el OCR **solo** en las que faltan.

    ## POR QUE "SOLO LAS QUE FALTAN"

    MEDIDO el 2026-10-15. La primera version era:

        if CACHE.exists():
            return json.loads(CACHE.read_text())

    Con una foto nueva en la carpeta, esa version devolvia la cache entera **sin
    leerala**, y `evaluar` recorre las claves de la cache: la foto nueva no
    aparecia en ninguna de las dos columnas del A/B. O sea que agregar evidencia
    a la medicion era la unica forma de no tenerla, y no habia ningun aviso.

    El A/B entero existe para que el texto OCR sea el MISMO en las dos
    columnas. Ocrer una foto que ya esta cacheada no rompe eso (es el mismo
    archivo), asi que la cache se respeta: lo que se evita es el costo, no el
    rigor.
    """
    cacheados: dict[str, str] = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    faltan = [f for f in fotos if f.name not in cacheados]

    if not faltan:
        print(f"usando el OCR ya corrido (cacheado): {len(cacheados)} foto(s)")
        return {f.name: cacheados[f.name] for f in fotos if f.name in cacheados}

    print(
        f"OCR de {len(faltan)} foto(s) sin cachear"
        + (f" (reusando {len(cacheados)} de la cache)" if cacheados else "")
    )
    from app.core.archivo_real import detectar_tipo_real
    from app.services import ocr as ocr_mod

    for foto in faltan:
        datos = foto.read_bytes()
        if detectar_tipo_real(datos) is None:
            print(f"  {foto.name}: NO_SOPORTADO")
            continue
        leido = ocr_mod.leer_imagen(datos)
        cacheados[foto.name] = leido.texto
        print(f"  {foto.name}: {len(leido.texto)} chars, motor={leido.motor}")

    CACHE.write_text(json.dumps(cacheados, ensure_ascii=False))
    return {f.name: cacheados[f.name] for f in fotos if f.name in cacheados}


def evaluar(textos: dict[str, str], verdad: dict, etiqueta: str) -> dict:
    from app.services.parser_service import _parse_receipt_text

    por_minusc = _entradas_de_verdad(verdad)
    cuenta = {c: {"aciertos": 0, "fallos": 0} for c in CAMPOS}
    for nombre, texto in textos.items():
        # La cache puede traer una foto que ya no esta en el `verdad.json` (se
        # rotulo despues, o se borro la entrada). Antes esto era un `KeyError`
        # que tumbaba el A/B entero; ahora se dice y se sigue, porque perder la
        # comparacion de las demas por una entrada vieja es peor que la entrada
        # vieja.
        esperado = por_minusc.get(nombre.lower())
        if esperado is None:
            print(f"  (cacheado pero SIN verdad rotulada, no se evalua: {nombre})")
            continue
        obtenido = _parse_receipt_text(texto)
        for campo in CAMPOS:
            veredicto, _ = compara(
                _comparable(esperado.get(campo)),
                _comparable(getattr(obtenido, campo, None)),
                campo,
            )
            if veredicto == "acierto":
                cuenta[campo]["aciertos"] += 1
            elif veredicto == "fallo":
                cuenta[campo]["fallos"] += 1

    print(f"\n{'=' * 74}\n{etiqueta}  (texto OCR IDENTICO para las dos columnas)\n{'=' * 74}")
    print(f"  {'campo':20} {'aciertos':>9} {'fallos':>7} {'exactitud':>11}")
    for campo in CAMPOS:
        c = cuenta[campo]
        n = c["aciertos"] + c["fallos"]
        pct = f"{c['aciertos'] / n * 100:.1f}%" if n else "sin dato"
        print(f"  {campo:20} {c['aciertos']:>9} {c['fallos']:>7} {pct:>11}")
    return cuenta


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    carpeta = Path(sys.argv[1])
    etiqueta = sys.argv[2] if len(sys.argv) > 2 else "VERSION EVALUADA"
    verdad = json.loads(Path("/verdad/verdad.json").read_text())
    # La MISMA seleccion que `medir_precision_ocr.py`, y no un `p.name in verdad`
    # propio: MEDIDO el 2026-10-15, ese filtro era sensible a mayusculas y dejo
    # fuera `IMG_4398.heic` (la verdad la nombra `IMG_4398.HEIC`) sin avisar. Dos
    # copias de la misma seleccion son dos reglas que divergen, y esta ya habia
    # divergido una vez.
    fotos, sin_foto = selecciona_fotos(carpeta, verdad)
    print(f"{len(fotos)} foto(s) rotuladas en {carpeta}")
    if sin_foto:
        print(
            f"ATENCION: {len(sin_foto)} rotulada(s) NO estan en la carpeta y quedan "
            f"fuera del A/B: {', '.join(sin_foto)}\n"
            "  Un porcentaje sobre las que si estan no dice nada de estas."
        )
    print()

    textos = ocr_una_vez(fotos)
    evaluar(textos, verdad, etiqueta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
