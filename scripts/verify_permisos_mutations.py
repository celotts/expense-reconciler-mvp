#!/usr/bin/env python3
"""Verificacion por mutacion del modelo de roles y permisos por accion.

Un mapa bonito no protege nada si la guardia no lo consulta. Aqui se rompen a
proposito las defensas que hacen que `viewer` sea de solo lectura tambien cuando
se llama a la API con curl, y se exige que los tests mueran:

- `viewer` gana `crear` en el mapa.
- `operator` gana `aprobar`.
- la guardia deja de distinguir aprobar/conciliar/administrar del verbo HTTP.
- un PDF o un Excel vuelve a contar como lectura.
- el router deja de instalar la guardia.
- la base deja de aceptar `viewer`.

Uso:  python3 scripts/verify_permisos_mutations.py
Salida: 0 si toda mutacion muere, 1 si alguna sobrevive o quedo obsoleta.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PRUEBAS = "tests/unit/test_permisos_por_accion.py"

MUTACIONES: list[tuple[str, str, str, str]] = [
    (
        "viewer gana permiso de crear en el mapa",
        "app/core/permisos.py",
        "    RolEmpresa.VIEWER: frozenset({Permiso.LEER}),",
        "    RolEmpresa.VIEWER: frozenset({Permiso.LEER, Permiso.CREAR}),",
    ),
    (
        "operator gana permiso de aprobar en el mapa",
        "app/core/permisos.py",
        "        {Permiso.LEER, Permiso.CREAR, Permiso.EDITAR, Permiso.CERRAR}",
        "        {Permiso.LEER, Permiso.CREAR, Permiso.EDITAR, Permiso.CERRAR, Permiso.APROBAR}",
    ),
    (
        "aprobar vuelve a ser un simple PATCH",
        "app/core/deps.py",
        '    if "/spot-check" in ruta or ruta.endswith("/review"):\n'
        "        return Permiso.APROBAR\n",
        "    if False:\n        return Permiso.APROBAR\n",
    ),
    (
        "conciliar vuelve a ser un simple POST",
        "app/core/deps.py",
        '    if ruta.startswith("/api/v1/reconciliations/run"):\n'
        "        return Permiso.CONCILIAR\n",
        "    if False:\n        return Permiso.CONCILIAR\n",
    ),
    (
        "exportar vuelve a contar como lectura",
        "app/core/deps.py",
        '        if ruta.endswith(".pdf") or "/export/" in ruta or ruta.endswith("/backup"):\n'
        "            return Permiso.EXPORTAR\n",
        "        if False:\n            return Permiso.EXPORTAR\n",
    ),
    (
        "administrar empresas vuelve a ser un borrado cualquiera",
        "app/core/deps.py",
        '    if ruta.startswith("/api/v1/companies") or ruta.startswith("/api/v1/usuarios"):\n'
        "        return Permiso.ADMINISTRAR\n",
        "    if False:\n        return Permiso.ADMINISTRAR\n",
    ),
    (
        "una cabecera basura vuelve a saltar la guardia",
        "app/core/deps.py",
        "            raise HTTPException(\n"
        "                status_code=status.HTTP_400_BAD_REQUEST,\n"
        '                detail="X-Company-ID no es un UUID valido",\n'
        "            ) from exc\n",
        "            return\n",
    ),
    (
        "un rol desconocido se vuelve administrador",
        "app/core/permisos.py",
        "    except ValueError:\n        return None\n",
        "    except ValueError:\n        return RolEmpresa.ADMIN\n",
    ),
    (
        "la guardia deja de instalarse en los routers",
        "app/api/api_router.py",
        "PROTEGIDOS = [Depends(get_current_user), Depends(guardia_de_permisos)]",
        "PROTEGIDOS = [Depends(get_current_user)]",
    ),
    (
        "la API deja de enviar los permisos al front",
        "app/api/auth.py",
        "            permisos=list(permisos_de_rol(rol)),\n",
        "            # permisos omitidos a proposito\n",
    ),
    (
        "la migracion no admite viewer",
        "db/migrations/0024_rol_viewer_y_permisos.sql",
        "    CHECK (rol IN ('owner', 'admin', 'accountant', 'operator', 'viewer'));",
        "    CHECK (rol IN ('owner', 'admin', 'accountant', 'operator'));",
    ),
    (
        "una base nueva nace sin la migracion de viewer",
        "db/init.sql",
        "    ('0024_rol_viewer_y_permisos.sql')\n",
        "    ('0001_inicial.sql')\n",
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
    print(f"Las {len(MUTACIONES)} mutaciones mueren. `viewer` no es decorativo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
