#!/usr/bin/env python3
"""Mutaciones del cierre de sesion del lado del navegador.

`npm run verify:sesion` mira la peticion que SALE de `salir()`, no el texto del
codigo. Aqui se quitan cuatro decisiones y se exige que el verificador muera:

- sin la llamada al logout, borrar el token sigue siendo solo un aviso local;
- sin borrar el token, la interfaz sigue mandandolo aunque el servidor ya lo
  revoco;
- sin `sinAviso401`, un 401 del propio logout se cuenta como una transaccion a
  medias;
- sin el guard de token vencido, se gasta un viaje para recibir el 401 que ya
  se sabia.

Uso:  python3 scripts/verify_sesion_front_mutations.py
Salida: 0 si toda mutacion muere, 1 si alguna sobrevive o quedo obsoleta.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ARCHIVO = "front/src/services/auth.ts"
VERIFICADOR = "verify:sesion"

MUTACIONES: list[tuple[str, str, str]] = [
    (
        "el navegador deja de avisar al servidor al salir",
        "      await fetchApi<void>('/auth/logout', {\n"
        "        method: 'POST',\n"
        "        sinAviso401: true,\n"
        "      });\n",
        "      // desactivada: solo se borra el token local\n",
    ),
    (
        "la interfaz no borra el token aunque el servidor lo revocara",
        "  } finally {\n    borrarToken();\n  }\n",
        "  } finally {\n    // desactivada: el token se queda en localStorage\n  }\n",
    ),
    (
        "un 401 del logout vuelve a parecer una caducidad con transaccion a medias",
        "        sinAviso401: true,\n",
        "",
    ),
    (
        "un token ya vencido se manda igual al servidor",
        "    if (token && !estaVencido(token)) {\n",
        "    if (token) {\n",
    ),
]


def main() -> int:
    supervivientes: list[str] = []
    ruta = RAIZ / ARCHIVO
    fuente = ruta.read_text(encoding="utf-8")

    for nombre, original, mutado in MUTACIONES:
        if original not in fuente:
            print(f"  ?    {nombre}")
            print(f"        NO SE ENCONTRO el texto original en {ARCHIVO}.")
            supervivientes.append(f"{nombre} (mutacion obsoleta)")
            continue

        try:
            ruta.write_text(fuente.replace(original, mutado, 1), encoding="utf-8")
            proc = subprocess.run(
                ["npm", "--prefix", "front", "run", VERIFICADOR],
                capture_output=True,
                text=True,
                cwd=RAIZ,
            )
        finally:
            ruta.write_text(fuente, encoding="utf-8")

        if proc.returncode != 0:
            print(f"  ok   {nombre} (detectada por {VERIFICADOR})")
        else:
            print(f"  VIVA {nombre} ({VERIFICADOR} no se dio cuenta)")
            supervivientes.append(nombre)

    print()
    if supervivientes:
        print(f"{len(supervivientes)} de {len(MUTACIONES)} mutaciones sobrevivieron:")
        for nombre in supervivientes:
            print(f"  - {nombre}")
        return 1
    print(f"Las {len(MUTACIONES)} mutaciones del cierre de sesion del front mueren.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
