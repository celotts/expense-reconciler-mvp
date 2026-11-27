#!/usr/bin/env python3
"""Mutaciones del contrato de revocacion y de la auditoria por empresa.

Cada defensa del esquema nuevo tiene que doler si se quita:

- sin los triggers, una revocacion se puede borrar y el token robado revive;
- sin el CHECK, se puede escribir un motivo que nadie audita;
- con `ON DELETE CASCADE`, borrar una cuenta borraria su revocacion;
- con un `UPDATE` de backfill en 0026, la migracion revienta contra el trigger
  append-only de `auditoria` y la API nunca arranca;
- sin el registro en `schema_migrations`, una base ya migrada se salta el cambio.

Corre `tests/unit/test_revocacion_ddl.py` contra el archivo mutado y lo restaura
siempre, incluso si pytest explota. La mitad HTTP la cubre
`verify_sesion_y_revocacion_mutations.py` y la del navegador
`verify_sesion_front_mutations.py`.

Uso:  PYTHONPATH=. python3 scripts/verify_revocacion_mutations.py
Salida: 0 si toda mutacion muere, 1 si alguna sobrevive o quedo obsoleta.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PRUEBAS = "tests/unit/test_revocacion_ddl.py"

MUTACIONES: list[tuple[str, str, str, str]] = [
    (
        "el trigger que impide actualizar una revocacion desaparece",
        "db/migrations/0025_tokens_revocados.sql",
        "CREATE TRIGGER trg_tokens_revocados_no_actualizar\n"
        "    BEFORE UPDATE ON tokens_revocados\n"
        "    FOR EACH ROW EXECUTE FUNCTION tokens_revocados_no_actualizar();",
        "-- desactivada",
    ),
    (
        "el trigger que impide borrar una revocacion desaparece del init",
        "db/init.sql",
        "CREATE TRIGGER trg_tokens_revocados_no_borrar\n"
        "    BEFORE DELETE ON tokens_revocados\n"
        "    FOR EACH ROW EXECUTE FUNCTION tokens_revocados_no_borrar();",
        "-- desactivada",
    ),
    (
        "el reset administrativo deja de ser un motivo declarado",
        "db/migrations/0025_tokens_revocados.sql",
        "CHECK (motivo IN ('logout', 'cambio_contrasena', 'reset_administrativo', 'baja_cuenta'));",
        "CHECK (motivo IN ('logout', 'cambio_contrasena', 'baja_cuenta'));",
    ),
    (
        "borrar una cuenta se lleva por delante su revocacion",
        "db/migrations/0025_tokens_revocados.sql",
        "user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,",
        "user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,",
    ),
    (
        "la empresa de auditoria deja de declararse en la migracion",
        "db/migrations/0026_auditoria_company_id.sql",
        "    ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES companies(id) ON DELETE SET NULL;",
        "    ADD COLUMN IF NOT EXISTS company_id UUID;",
    ),
    (
        "el indice por empresa y fecha desaparece del init",
        "db/init.sql",
        "CREATE INDEX IF NOT EXISTS ix_auditoria_company_fecha ON auditoria (company_id, fecha DESC);",
        "-- sin indice",
    ),
    (
        "alguien intenta backfillear una tabla append-only",
        "db/migrations/0026_auditoria_company_id.sql",
        "CREATE INDEX IF NOT EXISTS ix_auditoria_company_fecha",
        "UPDATE auditoria SET company_id = NULL;\n\nCREATE INDEX IF NOT EXISTS ix_auditoria_company_fecha",
    ),
    (
        "el init deja de registrar la migracion de revocaciones",
        "db/init.sql",
        "    ('0025_tokens_revocados.sql'),\n",
        "",
    ),
    (
        "la migracion de auditoria deja de registrarse",
        "db/migrations/0026_auditoria_company_id.sql",
        "VALUES ('0026_auditoria_company_id.sql')",
        "VALUES ('0026_auditoria_sin_registro.sql')",
    ),
    (
        "el modelo deja de declarar la empresa en auditoria",
        "app/models/auditoria.py",
        "    company_id = Column(UUID(as_uuid=True), nullable=True, index=True)\n",
        "    # company_id = Column(UUID(as_uuid=True), nullable=True, index=True)\n",
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
    print(f"Las {len(MUTACIONES)} mutaciones del esquema de revocacion mueren.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
