#!/usr/bin/env python3
"""Verifica por MUTACION las reglas de fecha del parser.

Corre cada defensa por separado y comprueba que AL MENOS un test muere. Un test
que no muere es una defensa decorativa: se lee como una garantia y no esta
protegiendo nada. Salida 1 si alguna defensa sobrevive.

    python3 scripts/verify_ocr_mutations.py   # las 16 de la ruta de OCR
    python3 _verify_fecha_mutations.py        # estas 7
"""

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PARSER = RAIZ / "app/services/parser_service.py"

MUTACIONES = [
    (
        "M1 se quita la rama de la HORA en `_fecha_del_comprobante`",
        "        if HORA_EN_LA_LINEA_RE.search(linea):",
        "        if False:",
        "test_la_hora_al_lado_es_marca_suficiente",
    ),
    (
        "M2 `HORA_EN_LA_LINEA_RE` deja de exigir dos puntos",
        r'HORA_EN_LA_LINEA_RE = re.compile(r"\b[0-2]\d:[0-5]\d(?::[0-5]\d)?\b")',
        r'HORA_EN_LA_LINEA_RE = re.compile(r"\b[0-2]\d[0-5]\d(?::[0-5]\d)?\b")',
        "test_la_hora_al_lado_es_marca_suficiente",
    ),
    (
        "M3 `VENCIMIENTO_RE` deja de vetar el vencimiento",
        "        if VENCIMIENTO_RE.search(linea):\n            continue",
        "        if False:\n            continue",
        "test_un_vencimiento_no_gana_aunque_le_pongan_hora",
    ),
    (
        "M4 `MARCA_DEL_COMPROBANTE_RE` deja de exigir marca",
        "        if not MARCA_DEL_COMPROBANTE_RE.search(linea):\n            continue",
        "        if False:\n            continue",
        "test_una_fecha_sin_marca_y_sin_hora_sigue_nada",
    ),
    (
        r"M5 la ruta etiquetada vuelve a `\d{2,4}` (ano de 3 digitos)",
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2}(?:\d{2})?)(?!\d)",
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})(?!\d)",
        "test_un_ano_de_tres_digitos_no_es_un_ano",
    ),
    (
        r"M6 la ruta etiquetada pierde el `(?!\d)` de cola",
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2}(?:\d{2})?)(?!\d)",
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2}(?:\d{2})?)",
        "test_un_ano_de_tres_digitos_no_es_un_ano",
    ),
    (
        r"M7 `FECHA_SIN_ETIQUETA_RE` vuelve a `\d{2,4}`",
        'FECHA_SIN_ETIQUETA_RE = re.compile(r"(?<!\\d)(\\d{1,2})[/-](\\d{1,2})[/-](\\d{2}(?:\\d{2})?)(?!\\d)")',
        'FECHA_SIN_ETIQUETA_RE = re.compile(r"(?<!\\d)(\\d{1,2})[/-](\\d{1,2})[/-](\\d{2,4})(?!\\d)")',
        "test_un_ano_de_tres_digitos_tampoco_pasa_por_la_ruta_sin_etiqueta",
    ),
    (
        "M8 la ruta etiquetada vuelve a `pd.to_datetime`",
        '                partes = re.split(r"[/-]", date_match.group(1))\n'
        '                leida = _a_fecha(*partes)\n'
        '                if leida is not None:\n'
        '                    expense_date = leida',
        '                import pandas as _pd\n'
        '                expense_date = _pd.to_datetime(date_match.group(1), dayfirst=True).date()',
        "test_la_ruta_etiquetada_no_invierte_dia_y_mes",
    ),
]


def correr_tests() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest",
         "tests/unit/test_parser_service.py", "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=RAIZ, capture_output=True, text=True,
    )


def main() -> int:
    original = PARSER.read_text()
    supervivientes = []

    print("Baseline (sin mutacion, tiene que pasar)")
    base = correr_tests()
    if base.returncode != 0:
        print("  FALLA: el baseline ya esta roto, las mutaciones no dicen nada")
        print(base.stdout[-3000:])
        return 1
    print("  OK\n")

    for nombre, viejo, nuevo, test_esperado in MUTACIONES:
        if viejo not in original:
            print(f"  {nombre}\n    NO SE APLICA: el patron viejo no esta en el archivo")
            supervivientes.append(nombre)
            continue
        PARSER.write_text(original.replace(viejo, nuevo, 1))
        try:
            r = correr_tests()
        finally:
            PARSER.write_text(original)

        murio = r.returncode != 0
        marco = "SI" if test_esperado in r.stdout else "NO"
        if not murio:
            supervivientes.append(nombre)
            estado = "SOBREVIVIO  <<<"
        else:
            estado = "murio"
        print(f"  {nombre}")
        print(f"    {estado}  (y el test '{test_esperado}' aparecio en la salida: {marco})")

    print()
    if supervivientes:
        print(f"FALLA: {len(supervivientes)} defensa(s) sin test que muera:")
        for s in supervivientes:
            print(f"  - {s}")
        return 1
    print(f"OK: las {len(MUTACIONES)} defensas mueren si se quitan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
