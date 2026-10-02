#!/usr/bin/env python3
"""Mide la exactitud de la ruta de OCR contra una verdad conocida.

POR QUE EXISTE

El sistema ya mide su propia exactitud: el 5% de los tickets AUTO_APROBADOS entra
al muestreo de `spot_check` y una persona revisa si la lectura era correcta
(`app/services/accuracy_service.py`). Ese mecanismo mide **lo que el sistema
afirma**. Y aqui esta el problema: un ticket de OCR nunca llega a AUTO_APROBADO,
porque regla 4 de `AGENTS.md` exige RFC **y** subtotal **y** fecha y una foto
rara vez trae los tres. Medido en la base de este proyecto:

    confidence_source | extraction_status  | tickets | en_muestreo
    ocr               | PENDIENTE           |       1 |          0
    ocr               | REQUIERE_REVISION   |       1 |          0

Las dos filas de OCR estan en `en_muestreo = 0`. O sea: **el reporte de exactitud
no puede decir nada sobre las fotos**, y por eso no se puede afirmar una
precision de escaneo con el. Este script mide la ruta de OCR **por fuera del
ciclo de vida del ticket**, que es la unica forma de medirla hoy sin cambiar lo
que el sistema afirma.

QUE MIDE, Y QUE NO

Mide el texto que sale de `ocr.leer_imagen` + `parser_service._parse_receipt_text`,
que es exactamente lo que hace `scan_service.procesar_archivo`. Si este script
dice que el total esta mal, el ticket guardado tiene el total mal: no es una
aproximacion de otra ruta.

NO mide la calidad del gate ni la de la conciliacion. Y no es un test: corre
fuera, tarda lo que tarda el OCR real, y su codigo de salida es el de un umbral
de precision.

COMO SE USA

1. Se rotula cada foto a mano. La verdad la escribe una persona mirando el
   papel, nunca el sistema: si la verdad la produce el OCR, el script mide que
   el OCR coincide consigo mismo.

2. Se guarda el archivo de verdad **fuera del repositorio**:

       python3 scripts/medir_precision_ocr.py --init /ruta/a/las/fotos
       # escribe /ruta/a/las/fotos/verdad.json con el esqueleto

   Los comprobantes personales no van a un repositorio: llevan RFC, proveedores y
   montos. El repositorio lleva el script y el ejemplo del formato
   (`scripts/verdad_ejemplo.json`), nunca los papeles.

3. Se mide:

       python3 scripts/medir_precision_ocr.py /ruta/a/las/fotos
       python3 scripts/medir_precision_ocr.py /ruta --min-exactitud-total 0.985

Salida: 0 si se alcanza el umbral del total, 1 si no, 2 si algo fallo.

POR QUE ESTE SCRIPT NO ESTA EN tests/

Porque necesita Tesseract instalado, tarda ~30 s por foto y lee archivos de
verdad de una maquina concreta. Un test que dependa de eso deja de ser un test:
`pytest` deja de ser ejecutable en cualquier lado y el que se rompe es el que no
esta mirando. La red que si corre en los 960 tests esta en
`tests/unit/test_capture_ocr.py`, con OCR falso y texto fijo.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]

# Campos que se comparan, y como se normaliza cada uno para comparar. Un "51.50"
# y un "51.50" son el mismo dato; un "51.50" y un "$51.50 MXN" tambien.
CAMPOS = ("provider_name", "provider_tax_id", "expense_date", "subtotal", "tax_amount", "total_amount")

# Formatos de imagen que se aceptan como foto de un comprobante.
EXTENSIONES = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff"}

# Que se considera "el mismo dato" en el nombre del proveedor. Comparar cadenas
# exactas daria 0 de precision con dos ways de escribir "OXXO" y "Oxxo", que es
# un problema de formato y no de lectura.
def _mismo_proveedor(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return a == b
    from app.core.enums import UNKNOWN_PROVIDER

    if a.strip().upper() == UNKNOWN_PROVIDER.upper() or b.strip().upper() == UNKNOWN_PROVIDER.upper():
        return a.strip().upper() == b.strip().upper()
    return _normaliza(a) == _normaliza(b)


def _normaliza(texto: str) -> str:
    """Quita lo que no distingue: mayusculas, acentos, espacios y puntuacion."""
    import re
    import unicodedata

    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^A-Z0-9]+", "", sin_acentos.upper())


def _comparable(valor: Any) -> Any:
    """Deja el valor en una forma comparable, sin inventar precision."""
    if valor is None:
        return None
    if isinstance(valor, Decimal):
        return valor.quantize(Decimal("0.01"))
    return valor


def _a_importe(valor: Any) -> Decimal | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, Decimal):
        return valor
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None


def _a_fecha(valor: Any) -> str | None:
    if valor is None or valor == "":
        return None
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    return str(valor)


def compara(esperado: Any, obtenido: Any, campo: str) -> tuple[str, str]:
    """Compara un campo. Devuelve `(veredicto, detalle)`.

    Verdictos:

    - `acierto`      el sistema leyó lo que el papel dice.
    - `fallo`        el papel trae el dato y el sistema puso otra cosa.
    - `n/d`          el papel NO trae el dato y el sistema tampoco puso nada:
                     no habia nada que leer y no se leyó. **No es un acierto.**
    - `sin_respaldo` el papel NO trae el dato y el sistema puso uno.

    Los dos ultimos van aparte, y es la parte que hace que el numero signifique
    algo. `n/d` no cuenta ni como acierto ni como fallo: meterlo como acierto
    inflaria la precision, y meterlo como fallo contaria como error del OCR leer
    lo unico que el papel permitia leer.

    `sin_respaldo` SI es una señal, pero **no todas valen igual**, y el script no
    las mezcla a proposito:

    - En `subtotal` es peligroso. El valor hace que corra el check
      `subtotal_plus_tax_mismatch` sobre un numero que no esta en el papel, y un
      check que corre sobre datos inventados no esta comprobando el documento:
      esta comprobando la suposicion del parser.
    - En `tax_amount` con `0.00` es el valor POR OMISION del campo, no una
      lectura. El papel no trae IVA y el parser devuelve cero porque el campo no
      es opcional. Senalar esto como fallo de lectura seria medir el default en
      vez de la lectura, que es justo el error que este script existe para no
      cometer.

    Por eso el veredicto es "sin_respaldo" y no "inventado": nombra el hecho
    observable sin afirmar que en todos los casos sea un defecto.
    """

    if campo in ("subtotal", "tax_amount", "total_amount"):
        e, o = _a_importe(esperado), _a_importe(obtenido)
    elif campo == "expense_date":
        e, o = _a_fecha(esperado), _a_fecha(obtenido)
    elif campo == "provider_tax_id":
        e, o = (None if esperado is None else str(esperado)), (None if obtenido is None else str(obtenido))
    else:
        e, o = esperado, obtenido

    # El papel no trae el campo.
    if e is None or e == "":
        if o is None or o == "":
            return "n/d", "el papel no lo trae y el sistema tampoco"
        return "sin_respaldo", f"el papel NO lo trae y el sistema puso {o!r}"

    # El papel si lo trae.
    if campo == "provider_name":
        return ("acierto" if _mismo_proveedor(e, o) else "fallo"), f"esperaba {e!r}, leyo {o!r}"
    if campo in ("subtotal", "tax_amount", "total_amount"):
        return ("acierto" if (o is not None and o == e) else "fallo"), f"esperaba {e!r}, leyo {o!r}"
    if campo == "expense_date":
        return ("acierto" if (o is not None and o == e) else "fallo"), f"esperaba {e!r}, leyo {o!r}"
    return ("acierto" if (o is not None and _normaliza(o) == _normaliza(e)) else "fallo"), f"esperaba {e!r}, leyo {o!r}"


def _carga_verdad(carpeta: Path) -> dict[str, dict[str, Any]]:
    ruta = carpeta / "verdad.json"
    if not ruta.is_file():
        raise SystemExit(
            f"No esta {ruta}.\n"
            f"Corrige primero:  python3 scripts/medir_precision_ocr.py --init {carpeta}\n"
            f"y rellena los valores mirando cada papel."
        )
    crudo = json.loads(ruta.read_text(encoding="utf-8"))
    if not isinstance(crudo, dict):
        raise SystemExit(f"{ruta} deberia ser un objeto con una clave por archivo.")
    return crudo


def _esqueleto(carpeta: Path) -> str:
    """El archivo de verdad vacio, para llenarlo a mano."""
    if not carpeta.exists():
        raise SystemExit(
            f"No existe {carpeta}. Este script escribe DENTRO de la carpeta de las\n"
            f"fotos, y no la crea: es el archivo de verdad de tus comprobantes, y\n"
            f"decidir donde vive es decision de quien los tiene, no de la herramienta."
        )
    if not carpeta.is_dir():
        raise SystemExit(f"{carpeta} no es una carpeta.")

    if (carpeta / "verdad.json").exists():
        raise SystemExit(f"{carpeta / 'verdad.json'} ya existe. No se pisa.")

    entrada = {
        "_COMO_SE_LLENA": (
            "Una linea por foto. Los valores se leen MIRANDO EL PAPEL, no desde el "
            "sistema. Lo que el papel no trae va en null: null es 'novenia', y no "
            "cuenta como error del OCR."
        ),
        "_CAMPOS": list(CAMPOS),
        "EJEMPLO.jpg": {
            "provider_name": "Nombre del comercio tal como sale impreso",
            "provider_tax_id": None,
            "expense_date": "2026-09-28",
            "subtotal": None,
            "tax_amount": None,
            "total_amount": "97.56",
        },
    }
    destino = carpeta / "verdad.json"
    destino.write_text(json.dumps(entrada, indent=2, ensure_ascii=False), encoding="utf-8")
    return f"Escrito {destino}. Rellena los valores de cada foto y corre el script otra vez."


def _corre_una(foto: Path) -> Any:
    """La MISMA ruta que usa el escaner. Si divergiera, el script mediria otra cosa."""
    from app.core.archivo_real import detectar_tipo_real
    from app.services import ocr as ocr_mod
    from app.services.parser_service import _parse_receipt_text

    datos = foto.read_bytes()
    tipo = detectar_tipo_real(datos)
    if tipo is None:
        raise RuntimeError(
            "los bytes no son un PDF ni una imagen con firma conocida, "
            "asi que el escaner lo marcaria NO_SOPORTADO"
        )

    leido = ocr_mod.leer_imagen(datos)
    return leido, _parse_receipt_text(leido.texto)


def main() -> int:
    ap = argparse.ArgumentParser(description="Exactitud de la ruta de OCR contra verdad conocida.")
    ap.add_argument("carpeta", nargs="?", type=Path, help="Carpeta con las fotos y verdad.json")
    ap.add_argument("--init", type=Path, help="Escribe el esqueleto de verdad.json y sale")
    ap.add_argument(
        "--min-exactitud-total",
        type=float,
        default=None,
        help="Umbral de acierto del total. Sin esta opción el script siempre sale 0.",
    )
    args = ap.parse_args()

    if args.init:
        print(_esqueleto(args.init))
        return 0

    if args.carpeta is None:
        ap.error("hace falta la carpeta, o --init para crearla")

    sys.path.insert(0, str(RAIZ))
    verdad = _carga_verdad(args.carpeta)
    fotos = sorted(
        f for f in args.carpeta.iterdir()
        if f.is_file() and f.suffix.lower() in EXTENSIONES and f.name in verdad
    )

    if not fotos:
        print(
            f"No hay fotos con etiqueta en {args.carpeta}.\n"
            f"El archivo verdad.json nombra {len(verdad)} archivos; revisa que los nombres coincidan.",
        )
        return 2

    # Lo que se compara por campo. Los cuatro cubos son distintos a proposito:
    # ver la nota de `compara`.
    cuenta = {campo: {"aciertos": 0, "fallos": 0, "n/d": 0, "sin_respaldo": 0} for campo in CAMPOS}
    fallos: list[tuple[str, str, str]] = []

    print(f"\nMidiendo {len(fotos)} foto(s) con la ruta real de OCR\n" + "=" * 78)

    for foto in fotos:
        esperado = verdad[foto.name]
        try:
            leido, obtenido = _corre_una(foto)
        except Exception as exc:  # noqa: BLE001 - aqui se reporta, no se propaga
            print(f"  {foto.name:34} ERROR: {exc}")
            if "--verbose" in sys.argv:
                traceback.print_exc()
            # Un archivo que no se pudo leer es un fallo de todo lo que el papel
            # si traia, y `n/d` en lo que no traia. Contarlo como `n/d` en todo
            # lo demas seria "no leyo nada" dressado de "no havia nada".
            for campo in CAMPOS:
                if _comparable(esperado.get(campo)) in (None, ""):
                    continue
                cuenta[campo]["fallos"] += 1
                fallos.append((foto.name, campo, f"no se pudo leer: {exc}"))
            continue

        detalle = []
        for campo in CAMPOS:
            veredicto, nota = compara(
                _comparable(esperado.get(campo)),
                _comparable(getattr(obtenido, campo, None)),
                campo,
            )
            # El veredicto va en singular y la cubo en plural, y esa diferencia es
            # la que hacia que TODO cayera en "inventados" en una version previa.
            # Se mapea a mano en vez de por comparacion de sufijo.
            cubo = {
                "acierto": "aciertos",
                "fallo": "fallos",
                "n/d": "n/d",
                "sin_respaldo": "sin_respaldo",
            }[veredicto]
            cuenta[campo][cubo] += 1
            if veredicto in ("fallo", "sin_respaldo"):
                fallos.append((foto.name, campo, nota))
            detalle.append(f"{campo}={veredicto.upper()} ({nota})")

        print(f"  {foto.name:34} {leido.motor:9} conf={leido.confianza_media}")
        for d in detalle:
            print(f"      {d}")

    print("=" * 78)
    print(
        "\nExactitud por campo. El denominador es SOLO lo que el papel traia:\n"
        "  'n/d' = el papel no lo traia y el sistema tampoco puso nada (no cuenta).\n"
        "  'sin_respaldo' = el papel no lo trae y el sistema puso algo (revisar a mano).\n"
    )
    print(f"  {'campo':20} {'aciertos':>9} {'fallos':>7} {'n/d':>5} {'sin_respaldo':>13} {'exactitud':>10}")

    exactitud: dict[str, float | None] = {}
    for campo in CAMPOS:
        c = cuenta[campo]
        con_dato = c["aciertos"] + c["fallos"]
        exacto = c["aciertos"] / con_dato if con_dato else None
        exactitud[campo] = exacto
        shown = f"{exacto * 100:.1f}%" if exacto is not None else "sin dato"
        print(f"  {campo:20} {c['aciertos']:>9} {c['fallos']:>7} {c['n/d']:>5} {c['sin_respaldo']:>13} {shown:>10}")

    total = exactitud["total_amount"]
    sin_respaldo = sum(cuenta[c]["sin_respaldo"] for c in CAMPOS)
    print()
    if fallos:
        print(f"{len(fallos)} discrepancia(s):")
        for foto, campo, nota in fallos:
            print(f"  {foto}\n      {campo}: {nota}")

    print()
    if total is None:
        print("SIN EVIDENCIA del total: ningun papel trae un total rotulado.")
        return 2

    print(f"Exactitud del TOTAL: {total * 100:.1f}%")
    if sin_respaldo:
        print(f"VALOR SIN RESPALDO: {sin_respaldo} vez/veces (el papel no trae ese campo).")
        print("  En `subtotal` es peligroso: hace correr subtotal_plus_tax_mismatch sobre un numero")
        print("  fabricado. En `tax_amount` con 0.00 es el valor por omision del campo, no una lectura.")

    if args.min_exactitud_total is not None:
        if total >= args.min_exactitud_total:
            print(f"Cumple el umbral de {args.min_exactitud_total * 100:.1f}%.")
            return 0
        print(f"NO cumple el umbral de {args.min_exactitud_total * 100:.1f}%.")
        return 1

    if len(fotos) < 30:
        print(
            f"\nCon {len(fotos)} foto(s) esto no es una medida: es una anécdota. "
            f"Un porcentaje sobre menos de ~30 casos se mueve mucho con un solo "
            f"comprobante. Para afirmar una precision hay que rotular cientos."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())