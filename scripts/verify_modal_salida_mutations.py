#!/usr/bin/env python3
"""Mutaciones del modal de salida: preguntar no vale si se puede ignorar.

El verificador mira HTML y tambien las dos lineas que deciden el estado: una
corrida viva tiene que dar `ocupado` y un fallo de red tiene que dar `error`.
Aqui se quitan las cuatro defensas a proposito:

- el boton de Salir deja de estar deshabilitado -> `verify:salida`.
- una corrida viva se declara libre -> `verify:salida`.
- un error de red se declara libre -> `verify:salida`.
- la barra vuelve a cerrar la sesion sin preguntar -> `verify:salida`.

Uso:  python3 scripts/verify_modal_salida_mutations.py
Salida: 0 si toda mutacion muere, 1 si alguna sobrevive o quedo obsoleta.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

MUTACIONES: list[tuple[str, str, str, str]] = [
    (
        "el boton de salir no se deshabilita nunca",
        "front/src/components/ModalSalir.tsx",
        "            disabled={!libre}\n",
        "            disabled={false}\n",
    ),
    (
        "una digitalizacion en curso se declara libre",
        "front/src/components/ModalSalir.tsx",
        "        setEstado(n > 0 ? 'ocupado' : 'libre');\n",
        "        setEstado('libre');\n",
    ),
    (
        "un fallo al preguntar se finge libre",
        "front/src/components/ModalSalir.tsx",
        "          setEstado('error');\n",
        "          setEstado('libre');\n",
    ),
    (
        "la barra cierra la sesion sin confirmacion",
        "front/src/App.tsx",
        "              onClick={() => setConfirmarSalida(true)}\n",
        "              onClick={() => { void cerrarSesion(); }}\n",
    ),
]


def main() -> int:
    supervivientes: list[str] = []
    for nombre, archivo, original, mutado in MUTACIONES:
        ruta = RAIZ / archivo
        fuente = ruta.read_text(encoding="utf-8")
        if original not in fuente:
            print(f"  ?    {nombre}")
            print(f"        NO SE ENCONTRO el texto original en {archivo}.")
            supervivientes.append(f"{nombre} (mutacion obsoleta)")
            continue

        try:
            ruta.write_text(fuente.replace(original, mutado, 1), encoding="utf-8")
            proc = subprocess.run(
                ["npm", "--prefix", "front", "run", "verify:salida"],
                capture_output=True,
                text=True,
                cwd=RAIZ,
            )
        finally:
            ruta.write_text(fuente, encoding="utf-8")

        if proc.returncode != 0:
            print(f"  ok   {nombre} (detectada por verify:salida)")
        else:
            print(f"  VIVA {nombre} (verify:salida no se dio cuenta)")
            supervivientes.append(nombre)

    print()
    if supervivientes:
        print(f"{len(supervivientes)} de {len(MUTACIONES)} mutaciones sobrevivieron:")
        for nombre in supervivientes:
            print(f"  - {nombre}")
        return 1
    print(f"Las {len(MUTACIONES)} mutaciones del modal mueren. Salir no es un clic accidental.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
