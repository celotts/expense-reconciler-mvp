#!/usr/bin/env python3
"""Verificacion por mutacion del cerrojo "sin empresa no se digitaliza".

Las pruebas pueden pasar por el motivo equivocado: un `POST /scan/` que da 403
podria hacerlo por token, por rol o por carpeta. Aqui se quita cada defensa a
proposito y se exige que la prueba correspondiente muera.

Uso:
    python3 scripts/verify_digitalizacion_sin_empresa_mutations.py
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PRUEBAS = ["tests/integration/test_digitalizacion_sin_empresa.py", "-q", "--no-header"]


@dataclass
class Mutacion:
    nombre: str
    viejo: str
    nuevo: str

    def aplicar(self) -> tuple[Path, str]:
        ruta = RAIZ / "app/core/deps.py"
        original = ruta.read_text(encoding="utf-8")
        if self.viejo not in original:
            raise AssertionError(
                f"no se encontro el texto a mutar en {ruta.name}: {self.viejo!r}"
            )
        ruta.write_text(original.replace(self.viejo, self.nuevo, 1), encoding="utf-8")
        return ruta, original


MUTACIONES = [
    Mutacion(
        nombre="una base sin empresa vuelve a digitalizar",
        viejo="        if hay_empresa is None:\n",
        nuevo="        if False:  # MUTACION: ya no hay cerrojo\n",
    ),
    Mutacion(
        nombre="reprocesar se sale de las puertas vigiladas",
        viejo='        and ruta.endswith("/reprocess")\n',
        nuevo="        and False  # MUTACION: reprocess queda fuera\n",
    ),
    Mutacion(
        nombre="extract-and-create se sale de las puertas vigiladas",
        viejo='    "/api/v1/tickets/extract-and-create",\n',
        nuevo="    # MUTACION: ruta quitada del conjunto\n",
    ),
]


def _correr(prueba: str) -> tuple[bool, str]:
    comando = [sys.executable, "-m", "pytest", *PRUEBAS[:-2], "-k", prueba, "-q"]
    proceso = subprocess.run(comando, cwd=RAIZ, capture_output=True, text=True)
    return proceso.returncode == 0, proceso.stdout + proceso.stderr


def main() -> int:
    # La mutacion de reprocess necesita su propia prueba; la de
    # extract-and-create, otra. Se guardan como una sola lista de (mutacion,
    # filtro) para que la cuenta de mutaciones sea la real.
    filtros = [
        "test_sin_ninguna_empresa_las_cinco_puertas_paran",
        "reprocesar",
        "crear_ticket",
    ]
    base = _correr("test_")
    if not base[0]:
        print("La suite ya esta en rojo antes de mutar. Arregla eso primero.\n")
        print(base[1][-3000:])
        return 1
    print("Suite base en verde. Se prueban 3 mutaciones.\n")

    supervivientes: list[str] = []
    for mutacion, filtro in zip(MUTACIONES, filtros):
        try:
            ruta, original = mutacion.aplicar()
        except AssertionError as exc:
            print(f"  [OBSOLETA] {mutacion.nombre}\n      {exc}")
            supervivientes.append(f"{mutacion.nombre} (obsoleta)")
            continue
        try:
            paso, salida = _correr(filtro)
        finally:
            ruta.write_text(original, encoding="utf-8")
        if paso:
            print(f"  [NO DETECTADA] {mutacion.nombre}")
            supervivientes.append(mutacion.nombre)
        else:
            print(f"  [detectada]   {mutacion.nombre}  <- {filtro}")

    if supervivientes:
        print(f"\n{len(supervivientes)} mutaciones pasaron sin que nadie se diera cuenta:")
        for nombre in supervivientes:
            print(f"  - {nombre}")
        return 1
    print("\nLas 3 mutaciones murieron.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
