#!/usr/bin/env python3
"""Verificacion por mutacion del proveedor leido por marca impresa.

Un ticket de Bodega Aurrera se guardaba como `PANA C`: el logo no calificaba
como nombre y una linea de articulo si. Aqui se quita cada defensa a proposito
y se exige que la prueba correspondiente muera.

Uso:
    python3 scripts/verify_proveedor_marca_mutations.py
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ARCHIVO = "app/services/parser_service.py"
PRUEBA = "tests/unit/test_parser_service.py"


@dataclass
class Mutacion:
    nombre: str
    viejo: str
    nuevo: str
    filtro: str

    def aplicar(self) -> tuple[Path, str]:
        ruta = RAIZ / ARCHIVO
        original = ruta.read_text(encoding="utf-8")
        if self.viejo not in original:
            raise AssertionError(f"patron obsoleto: {self.viejo!r}")
        ruta.write_text(original.replace(self.viejo, self.nuevo, 1), encoding="utf-8")
        return ruta, original


MUTACIONES = [
    Mutacion(
        nombre="la marca deja de buscarse antes que el articulo",
        viejo="    for line in lines[:15]:\n        nombre_de_marca = proveedor_por_marca(line)\n",
        nuevo="    for line in ():\n        nombre_de_marca = proveedor_por_marca(line)\n",
        filtro="test_el_logo_roto_se_reconoce_y_el_articulo_no_gana",
    ),
    Mutacion(
        nombre="el logo sin la B vuelve a no contar",
        viejo=r'\bb?odega\s*aurrera\b',
        nuevo=r'\bbodega\s*aurrera\b',
        filtro="test_el_logo_roto_se_reconoce_y_el_articulo_no_gana",
    ),
    Mutacion(
        nombre="PANA C vuelve a ser un comercio",
        viejo="        if re.fullmatch(\n            r\"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{4,}\\s+[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]\", line\n        ):\n            return False\n",
        nuevo="        if False:  # MUTACION: fragmento de articulo aceptado\n            return False\n",
        filtro="test_un_articulo_con_tres_mayusculas_no_es_comercio",
    ),
    Mutacion(
        nombre="CONCEPTOS vuelve a ser proveedor",
        viejo="|art[ií]culos?|conceptos?|cantidad|",
        nuevo="|art[ií]culos?|concepto|cantidad|",
        filtro="test_un_encabezado_suelto_no_es_comercio",
    ),
]


def _correr(filtro: str) -> tuple[bool, str]:
    proceso = subprocess.run(
        [sys.executable, "-m", "pytest", PRUEBA, "-k", filtro, "-q", "--no-header"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
    )
    return proceso.returncode == 0, proceso.stdout + proceso.stderr


def main() -> int:
    base = _correr("TestLaMarcaImpresaGanaSobreElArticulo")
    if not base[0]:
        print("La suite ya esta en rojo antes de mutar. Arregla eso primero.\n")
        print(base[1][-3000:])
        return 1
    print("Suite base en verde. Se prueban 4 mutaciones.\n")

    supervivientes: list[str] = []
    for mutacion in MUTACIONES:
        try:
            ruta, original = mutacion.aplicar()
        except AssertionError as exc:
            print(f"  [OBSOLETA] {mutacion.nombre}\n      {exc}")
            supervivientes.append(f"{mutacion.nombre} (obsoleta)")
            continue
        try:
            paso, _ = _correr(mutacion.filtro)
        finally:
            ruta.write_text(original, encoding="utf-8")
        if paso:
            print(f"  [NO DETECTADA] {mutacion.nombre}")
            supervivientes.append(mutacion.nombre)
        else:
            print(f"  [detectada]   {mutacion.nombre}")

    if supervivientes:
        print(f"\n{len(supervivientes)} mutaciones pasaron sin que nadie se diera cuenta:")
        for nombre in supervivientes:
            print(f"  - {nombre}")
        return 1
    print("\nLas 4 mutaciones murieron.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
