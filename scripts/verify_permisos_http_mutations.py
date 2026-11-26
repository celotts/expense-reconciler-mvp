#!/usr/bin/env python3
"""Mutaciones de la guardia HTTP: el 403 se mide por cable, no por el mapa.

`verify_permisos_mutations.py` rompe el mapa y la clasificacion. Aqui se rompen
las dos mitades que faltaban:

- **El guardia desconectado del router.** El mapa puede estar perfecto y la
  peticion seguir entrando si alguien quita `Depends(guardia_de_permisos)`.
- **La guardia que no distingue lectura de exportacion.** `GET /cierre-mensual`
  y `.pdf` son la misma informacion; si vuelven a contar igual, `viewer` se
  lleva el archivo.
- **Una cabecera basura que salta la guardia.** El 400 no es cosmético: sin el,
  un `X-Company-ID` invalido deja pasar la escritura en routers que no piden
  empresa activa.

Cada mutacion tiene que hacer fallar `tests/integration/test_permisos_http.py`.
Si una sobrevive, la defensa esta escrita pero no esta defendiendo nada.

Uso:  python3 scripts/verify_permisos_http_mutations.py
Salida: 0 si toda mutacion muere, 1 si alguna sobrevive o quedo obsoleta.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PRUEBAS = "tests/integration/test_permisos_http.py"

MUTACIONES: list[tuple[str, str, str, str]] = [
    (
        "la guardia se desconecta del router",
        "app/api/api_router.py",
        "PROTEGIDOS = [Depends(get_current_user), Depends(guardia_de_permisos)]",
        "PROTEGIDOS = [Depends(get_current_user)]",
    ),
    (
        "el PDF vuelve a contar como lectura",
        "app/core/deps.py",
        '        if ruta.endswith(".pdf") or "/export/" in ruta or ruta.endswith("/backup"):\n'
        "            return Permiso.EXPORTAR\n",
        "        if False:\n            return Permiso.EXPORTAR\n",
    ),
    (
        "una cabecera basura vuelve a saltar la guardia por HTTP",
        "app/core/deps.py",
        "            raise HTTPException(\n"
        "                status_code=status.HTTP_400_BAD_REQUEST,\n"
        '                detail="X-Company-ID no es un UUID valido",\n'
        "            ) from exc\n",
        "            return\n",
    ),
    (
        "una busqueda por POST vuelve a exigir permiso de crear",
        "app/core/deps.py",
        '_RUTAS_DE_SOLO_LECTURA = frozenset({"/api/v1/duplicados/buscar"})',
        "_RUTAS_DE_SOLO_LECTURA = frozenset()",
    ),
    (
        "el company_id declarado vuelve a creerse sin comprobar",
        "app/core/deps.py",
        '    if declarada is not None and not request.url.path.startswith("/api/v1/usuarios"):',
        "    if False:",
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
                [sys.executable, "-m", "pytest", "-x", "-q", PRUEBAS],
                capture_output=True,
                text=True,
                cwd=RAIZ,
            )
        finally:
            ruta.write_text(fuente, encoding="utf-8")

        if proc.returncode != 0:
            print(f"  ok   {nombre} (detectada)")
        else:
            print(f"  VIVA {nombre} (nadie se dio cuenta)")
            supervivientes.append(nombre)

    print()
    if supervivientes:
        print(f"{len(supervivientes)} de {len(MUTACIONES)} mutaciones sobrevivieron:")
        for nombre in supervivientes:
            print(f"  - {nombre}")
        return 1
    print(f"Las {len(MUTACIONES)} mutaciones HTTP mueren. El 403 no es decorativo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
