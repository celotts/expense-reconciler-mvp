#!/usr/bin/env python3
"""Mutaciones del front: un boton que no se pinta no es un permiso.

Los verificadores de HTML miran lo renderizado, no el retorno de una funcion.
Aqui se quitan tres guardias a proposito y se exige que el verificador correspondiente
falla. Si alguno sobrevive, el verificador esta comprobando el mapa o el codigo,
no la pantalla que ve quien abre la aplicacion.

- `viewer` gana todos los permisos en la matriz -> `verify:menu` tiene que morir.
- La cola de revision pinta Corregir/Aprobar/Descartar sin permiso -> `verify:permisos`.
- Caja chica pinta "Nueva caja" sin permiso de crear -> `verify:permisos`.

Uso:  python3 scripts/verify_permisos_front_mutations.py
Salida: 0 si toda mutacion muere, 1 si alguna sobrevive o quedo obsoleta.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

MUTACIONES: list[tuple[str, str, str, str, str]] = [
    (
        "viewer gana todos los permisos en la matriz del front",
        "front/src/permisos.ts",
        "  viewer: ['leer'],\n",
        "  viewer: TODOS_LOS_PERMISOS,\n",
        "verify:menu",
    ),
    (
        "la cola pinta botones de firma sin permiso",
        "front/src/pages/ReviewQueue.tsx",
        "        {!editando && puede('editar') && (\n",
        "        {!editando && (\n",
        "verify:permisos",
    ),
    (
        "caja chica pinta 'Nueva caja' sin permiso de crear",
        "front/src/pages/CajaChica.tsx",
        "              {puede('crear') && (\n"
        '                <Button size="sm" onClick={() => setModalCaja(true)} disabled={!companyId}>\n'
        "                  Nueva caja\n"
        "                </Button>\n"
        "              )}",
        '              <Button size="sm" onClick={() => setModalCaja(true)} disabled={!companyId}>\n'
        "                Nueva caja\n"
        "              </Button>",
        "verify:permisos",
    ),
]


def main() -> int:
    supervivientes: list[str] = []
    for nombre, archivo, original, mutado, verificador in MUTACIONES:
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
                ["npm", "--prefix", "front", "run", verificador],
                capture_output=True,
                text=True,
                cwd=RAIZ,
            )
        finally:
            ruta.write_text(fuente, encoding="utf-8")

        if proc.returncode != 0:
            print(f"  ok   {nombre} (detectada por {verificador})")
        else:
            print(f"  VIVA {nombre} ({verificador} no se dio cuenta)")
            supervivientes.append(nombre)

    print()
    if supervivientes:
        print(f"{len(supervivientes)} de {len(MUTACIONES)} mutaciones sobrevivieron:")
        for nombre in supervivientes:
            print(f"  - {nombre}")
        return 1
    print(f"Las {len(MUTACIONES)} mutaciones del front mueren. El menu y los botones no son decorativos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
