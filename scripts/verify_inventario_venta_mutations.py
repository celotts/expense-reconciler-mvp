#!/usr/bin/env python3
"""Verifica por MUTACION la evidencia de las SALIDAS del inventario.

Tres defensas, y cada una tiene que matar a un test. Un guard que se puede
quitar sin que nada se ponga rojo se lee como una garantia y no lo es.
`docs/known-issues.md` §0.b cuenta por que el nombre del producto no puede
hacerse pasar por la nota de venta.

    python3 scripts/verify_inventario_venta_mutations.py
"""

import os
import subprocess
import sys
from pathlib import Path

# En esta maquina el `python3` del PATH es el venv 3.15, sin las dependencias
# del proyecto; la suite corrio con el 3.14 explicito. `PYTHON` permite fijarlo
# sin tocar el shebang.
PYTHON = os.environ.get("PYTHON", sys.executable)

RAIZ = Path(__file__).resolve().parents[1]
SERVICIO = RAIZ / "app/services/inventario_service.py"
TESTS = ["tests/unit/test_inventario.py", "tests/integration/test_administracion_api.py"]

MUTACIONES = [
    (
        "M1 se quita el guard de fuente declarada",
        "    if not origen:\n        raise ErrorDeInventario(\n            \"Este movimiento cambia el stock y necesita una fuente declarada: \"",
        "    if False:\n        raise ErrorDeInventario(\n            \"Este movimiento cambia el stock y necesita una fuente declarada: \"",
        "test_una_salida_sin_fuente_no_se_registra",
    ),
    (
        "M2 el nombre del producto vuelve a ser el origen por defecto",
        '    origen = (descripcion_origen or "").strip()',
        "    origen = (descripcion_origen or producto.nombre).strip()",
        "test_una_salida_sin_fuente_no_se_registra",
    ),
    (
        "M3 los espacios cuentan como evidencia",
        '    origen = (descripcion_origen or "").strip()',
        "    origen = descripcion_origen or \"\"",
        "test_espacios_no_son_una_fuente",
    ),
]


def correr_tests() -> subprocess.CompletedProcess:
    entorno = os.environ.copy()
    # El shell de esta maquina exporta una SECRET_KEY corta; los tests la
    # rechazan. La .env enciende el reset del escaner, que tampoco aplica aqui.
    entorno.pop("SECRET_KEY", None)
    entorno["TICKETS_SCAN_RESET_HABILITADO"] = "false"
    entorno["PYTHONPATH"] = str(RAIZ)
    return subprocess.run(
        [PYTHON, "-m", "pytest", *TESTS, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        env=entorno,
    )


def main() -> int:
    original = SERVICIO.read_text()
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
        SERVICIO.write_text(original.replace(viejo, nuevo, 1))
        try:
            r = correr_tests()
        finally:
            SERVICIO.write_text(original)

        murio = r.returncode != 0
        aparecio = test_esperado in r.stdout
        if not murio:
            supervivientes.append(nombre)
            estado = "SOBREVIVIO  <<<"
        else:
            estado = "murio"
        print(f"  {nombre}")
        print(f"    {estado}  (el test '{test_esperado}' aparecio: {'SI' if aparecio else 'NO'})")

    print()
    if supervivientes:
        print(f"FALLA: {len(supervivientes)} defensa(s) sin test que muera:")
        for s in supervivientes:
            print(f"  - {s}")
        return 1
    print(f"OK: {len(MUTACIONES)} de {len(MUTACIONES)} mutaciones mueren")
    return 0


if __name__ == "__main__":
    sys.exit(main())
