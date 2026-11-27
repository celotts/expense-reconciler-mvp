#!/usr/bin/env python3
"""Mutaciones de la sesion que se cierra en el SERVIDOR.

Cada defensa nueva tiene que doler si se quita:

- sin `jti`, logout no puede revocar nada;
- sin la consulta a `tokens_revocados`, cerrar sesion es solo un aviso al
  navegador y un token robado vive 8 horas;
- si la inactividad se mide desde `emitida_at` y no desde la ultima peticion,
  el reloj no es deslizante;
- si la fila vencida se borra, la peticion siguiente la crea de nuevo y el
  token vuelve a servir sin que nadie haya vuelto a entrar;
- si `SESSION_IDLE_MINUTES` se pone a 0, la caducidad por inactividad desaparece
  sin que ninguna pantalla lo diga;
- sin el espejo en `init.sql` o sin el registro de la migracion, una base nueva
  nace sin la tabla y la guardia revienta con 500.

Corre las pruebas contra el archivo mutado y lo restaura siempre, incluso si
pytest explota.

Uso:  PYTHONPATH=. python3 scripts/verify_sesion_y_revocacion_mutations.py
Salida: 0 si toda mutacion muere, 1 si alguna sobrevive o quedo obsoleta.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PRUEBAS = [
    "tests/integration/test_sesion_y_revocacion.py",
    "tests/unit/test_revocacion_ddl.py",
    "tests/unit/test_init_sql_espeja_los_modelos.py",
]

MUTACIONES: list[tuple[str, str, str, str]] = [
    (
        "el token deja de llevar jti",
        "app/core/security.py",
        '        "jti": str(uuid4()),\n',
        "",
    ),
    (
        "la guardia deja de mirar tokens_revocados",
        "app/core/deps.py",
        "        revocada = (\n"
        "            await db.execute(\n"
        "                select(TokenRevocado).where(TokenRevocado.jti == jti)\n"
        "            )\n"
        "        ).scalar_one_or_none()\n"
        "        if revocada is not None:\n"
        "            raise _no_autorizado(\n"
        '                "La sesion fue cerrada. Vuelve a iniciar sesion."\n'
        "            )\n",
        "        # desactivada: la revocacion ya no se consulta\n",
    ),
    (
        "una sesion vencida se borra y puede reabrirse sola",
        "app/core/deps.py",
        "                # NO se actualiza ni se borra la fila: si se borrara, la\n"
        "                # peticion siguiente la crearia de nuevo y el token volveria\n"
        "                # a servir sin que nadie hubiera vuelto a entrar.\n"
        "                raise _no_autorizado(\n",
        "                await db.delete(sesion)\n"
        "                await db.commit()\n"
        "                raise _no_autorizado(\n",
    ),
    (
        "la inactividad se mide desde el login, no desde la ultima peticion",
        "app/core/deps.py",
        "            inactiva = ahora - como_utc(sesion.ultima_actividad)\n",
        "            inactiva = ahora - como_utc(sesion.emitida_at)\n",
    ),
    (
        "el logout vuelve a ser solo un aviso al navegador",
        "app/api/auth.py",
        '    await revocar_token_actual(request, db, usuario.id, "logout")\n',
        "    pass  # desactivada: el logout no revoca\n",
    ),
    (
        "la caducidad por inactividad se apaga",
        "app/core/config.py",
        "    SESSION_IDLE_MINUTES: int = 30\n",
        "    SESSION_IDLE_MINUTES: int = 0\n",
    ),
    (
        "el init.sql deja de crear la tabla de sesiones",
        "db/init.sql",
        "CREATE TABLE IF NOT EXISTS sesiones_activas (",
        "CREATE TABLE IF NOT EXISTS sesiones_activas_vieja (",
    ),
    (
        "el init.sql deja de registrar la migracion de sesiones",
        "db/init.sql",
        "    ('0028_sesiones_activas.sql')\n",
        "",
    ),
    (
        "la migracion 0028 deja de registrarse a si misma",
        "db/migrations/0028_sesiones_activas.sql",
        "VALUES ('0028_sesiones_activas.sql')",
        "VALUES ('0028_sin_registro.sql')",
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
                [sys.executable, "-m", "pytest", "-x", "-q", *PRUEBAS],
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
    print(f"Las {len(MUTACIONES)} mutaciones de la sesion y la revocacion mueren.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
