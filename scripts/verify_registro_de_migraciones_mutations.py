#!/usr/bin/env python3
"""El libro de migraciones de una base nueva no puede mentir.

`init.sql` siembra `schema_migrations` con el nombre EXACTO de cada archivo de
`db/migrations/`. Si un nombre se recorta (0020 sin `.sql`) o falta (0027), el
arranque sigue funcionando porque esas migraciones son idempotentes; lo que
pasa es que el servicio las vuelve a aplicar y el registro dice algo que no
corresponde a ningun archivo. Un registro que miente no avisa: por eso la
defensa se comprueba con mutaciones.

Uso:  PYTHONPATH=. python3 scripts/verify_registro_de_migraciones_mutations.py
Salida: 0 si toda mutacion muere, 1 si alguna sobrevive o quedo obsoleta.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PRUEBA = (
    "tests/unit/test_arranque_por_healthcheck.py::"
    "TestLasMigracionesSeAplicanAlArrancar::"
    "test_el_seed_registra_cada_archivo_con_su_nombre_exacto"
)

MUTACIONES: list[tuple[str, str, str]] = [
    (
        "el seed deja de registrar la migracion del costo del kardex",
        "    ('0027_costo_del_kardex.sql'),\n",
        "",
    ),
    (
        "el seed registra 0020 sin el sufijo del archivo",
        "    ('0020_auditoria_segura.sql'),\n",
        "    ('0020_auditoria_segura'),\n",
    ),
    (
        "el seed registra 0028 sin el sufijo del archivo",
        "    ('0028_sesiones_activas.sql')\n",
        "    ('0028_sesiones_activas')\n",
    ),
]


def main() -> int:
    ruta = RAIZ / "db/init.sql"
    fuente = ruta.read_text(encoding="utf-8")
    supervivientes: list[str] = []

    for nombre, original, mutado in MUTACIONES:
        if original not in fuente:
            print(f"  ?    {nombre}")
            print("        NO SE ENCONTRO el texto original en db/init.sql.")
            supervivientes.append(f"{nombre} (mutacion obsoleta)")
            continue

        try:
            ruta.write_text(fuente.replace(original, mutado, 1), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-x", "-q", PRUEBA],
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
    print(f"Las {len(MUTACIONES)} mutaciones del registro de migraciones mueren.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
