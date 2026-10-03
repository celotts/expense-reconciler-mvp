#!/usr/bin/env python3
"""Verifica en Postgres real lo que SQLite no puede comprobar.

QUE HACE ESTE SCRIPT Y POR QUE HACE FALTA

`ticket_documents` es append-only, y esa regla la hace cumplir un TRIGGER de
Postgres, no el codigo de Python. La suite corre sobre SQLite, donde los
triggers de PL/pgSQL no existen y las llaves foraneas estan apagadas por
omision. Un test en SQLite que dijera "el trigger existe" estaria mintiendo: la
suite comprueba el SERVICIO, y el motor se comprueba aqui.

Lo que se verifica contra la base de verdad:

  1. `UPDATE` sobre un documento es RECHAZADO.
  2. `DELETE` de un documento cuyo ticket sigue vivo es RECHAZADO.
  3. INSERT de la version 2: apila, no borra, y guarda actor y motivo.
  4. El original sigue byte-identico.
  5. El vigente es el de mayor `version`.
  6. La cadena no se puede bifurcar (dos versiones reemplazando a la misma).
  7. Un reemplazo sin actor ni motivo lo rechaza la base, no solo Python.
  8. `DELETE` en cascada SI se permite: borrado el gasto, se va el papel.

Se usa `asyncpg`, que es el driver que ya tiene el proyecto, y NO se agrega
`psycopg` para esto.

Uso:

    export POSTGRES_PASSWORD=$(grep POSTGRES_PASSWORD .env | cut -d= -f2)
    python3 scripts/verify_postgres_documentos.py

Salida: 0 si todo se cumple, 1 si algo falla.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

import asyncpg

# El DSN se lee del entorno y no se escribe aqui. Este proyecto tuvo una
# contrasena en un archivo versionado; no se vuelve a escribir una en otro.
DSN = "postgresql://postgres:{}@localhost:5434/expense_db".format(
    os.environ.get("POSTGRES_PASSWORD", ""),
)


class Comprobador:
    def __init__(self) -> None:
        self.ok = 0
        self.fallos: list[str] = []

    def comprueba(self, condicion: bool, descripcion: str) -> None:
        if condicion:
            self.ok += 1
            print(f"  [OK]      {descripcion}")
        else:
            self.fallos.append(descripcion)
            print(f"  [FALLA]   {descripcion}")

    def fallo(self, descripcion: str) -> None:
        self.fallos.append(descripcion)
        print(f"  [FALLA]   {descripcion}")


async def main() -> int:
    if not os.environ.get("POSTGRES_PASSWORD"):
        print(
            "Falta POSTGRES_PASSWORD. Esta en el archivo .env:\n"
            "    export POSTGRES_PASSWORD=$(grep POSTGRES_PASSWORD .env | cut -d= -f2)"
        )
        return 2

    c = Comprobador()
    empresa, ticket = uuid.uuid4(), uuid.uuid4()

    conn = await asyncpg.connect(DSN)
    try:
        print("\nPreparando datos de prueba...")
        await conn.execute(
            "INSERT INTO companies (id, name, tax_id) VALUES ($1, $2, $3)",
            empresa, "Verificacion documentos", f"VDOC{uuid.uuid4().hex[:8]}",
        )
        await conn.execute(
            """INSERT INTO tickets (id, company_id, provider_name, total_amount,
                   tax_amount, expense_date, extraction_status, source_hash)
               VALUES ($1, $2, 'PRUEBA', 10, 0, CURRENT_DATE, 'PENDIENTE', $3)""",
            ticket, empresa, f"h{uuid.uuid4().hex}",
        )
        await conn.execute(
            """INSERT INTO ticket_documents (ticket_id, contenido, tamano, sha256)
               VALUES ($1, decode('7631', 'hex'), 1, 'a1')""",
            ticket,
        )

        print("\n1. UPDATE sobre un documento debe ser RECHAZADO")
        try:
            await conn.execute(
                "UPDATE ticket_documents SET tamano = 999 WHERE ticket_id = $1", ticket
            )
            c.fallo("un UPDATE cambio los bytes de un comprobante y DEBIO ser rechazado")
        except Exception as exc:  # noqa: BLE001
            if "append-only" in str(exc):
                c.comprueba(True, "un UPDATE no cambia los bytes de un comprobante")
            else:
                c.fallo(f"rechazado por el motivo equivocado: {exc}")

        print("\n2. DELETE con el ticket vivo debe ser RECHAZADO")
        try:
            await conn.execute(
                "DELETE FROM ticket_documents WHERE ticket_id = $1", ticket
            )
            c.fallo("se borro el papel de un gasto que sigue existiendo")
        except Exception as exc:  # noqa: BLE001
            if "append-only" in str(exc):
                c.comprueba(True, "no se borra el papel mientras el gasto exista")
            else:
                c.fallo(f"rechazado por el motivo equivocado: {exc}")

        print("\n3. La version 2 se apila y guarda quien y por que")
        await conn.execute(
            """INSERT INTO ticket_documents
                   (ticket_id, contenido, tamano, sha256, reemplaza_a, version, actor, motivo)
               SELECT $1, decode('7632', 'hex'), 1, 'a2', id, 2, 'ana@test.mx', 'prueba'
               FROM ticket_documents WHERE ticket_id = $1""",
            ticket,
        )
        filas = await conn.fetch(
            """SELECT version, contenido, actor, motivo, reemplaza_a IS NOT NULL AS tiene_padre
               FROM ticket_documents WHERE ticket_id = $1 ORDER BY version""",
            ticket,
        )
        c.comprueba(len(filas) == 2, "hay dos versiones: el original NO se borro")
        c.comprueba(filas[0]["contenido"] == b"v1", "la version 1 conserva sus bytes")
        c.comprueba(filas[1]["contenido"] == b"v2", "la version 2 tiene los bytes nuevos")
        c.comprueba(filas[1]["actor"] == "ana@test.mx", "el reemplazo guarda el actor")
        c.comprueba(filas[1]["motivo"] == "prueba", "el reemplazo guarda el motivo")
        c.comprueba(filas[1]["tiene_padre"] is True, "la version 2 apunta a la que reemplaza")

        print("\n4. El vigente es el de mayor version")
        vigente = await conn.fetchrow(
            "SELECT version FROM ticket_documents WHERE ticket_id = $1"
            " ORDER BY version DESC LIMIT 1",
            ticket,
        )
        c.comprueba(vigente["version"] == 2, "el vigente es la version 2")

        print("\n5. La cadena no se puede bifurcar")
        try:
            await conn.execute(
                """INSERT INTO ticket_documents
                       (ticket_id, contenido, tamano, sha256, reemplaza_a, version, actor, motivo)
                   SELECT $1, decode('7633', 'hex'), 1, 'a3', id, 3, 'ana@test.mx', 'x'
                   FROM ticket_documents WHERE ticket_id = $1 AND version = 1""",
                ticket,
            )
            c.fallo("dos versiones reemplazaron a la misma y DEBIO ser rechazado")
        except Exception as exc:  # noqa: BLE001
            if "duplicate key" in str(exc) or "unique" in str(exc).lower():
                c.comprueba(True, "dos versiones no pueden reemplazar a la misma")
            else:
                c.fallo(f"rechazado por el motivo equivocado: {exc}")

        print("\n6. La base exige actor y motivo en un reemplazo")
        try:
            await conn.execute(
                """INSERT INTO ticket_documents
                       (ticket_id, contenido, tamano, sha256, reemplaza_a, version)
                   SELECT $1, decode('7634', 'hex'), 1, 'a4', id, 9
                   FROM ticket_documents WHERE ticket_id = $1 AND version = 2""",
                ticket,
            )
            c.fallo("un reemplazo sin actor ni motivo DEBIO ser rechazado por la base")
        except Exception:  # noqa: BLE001
            c.comprueba(True, "un reemplazo sin actor ni motivo lo rechaza la base")

        print("\n7. DELETE en cascada SI se permite")
        await conn.execute("DELETE FROM tickets WHERE id = $1", ticket)
        try:
            await conn.execute(
                "DELETE FROM ticket_documents WHERE ticket_id = $1", ticket
            )
            quedan = await conn.fetchval(
                "SELECT count(*) FROM ticket_documents WHERE ticket_id = $1", ticket
            )
            c.comprueba(
                quedan == 0,
                "borrado el gasto, su comprobante se va con el (cascade permitido)",
            )
        except Exception as exc:  # noqa: BLE001
            c.fallo(f"la cascada legitima fue bloqueada: {exc}")

        await conn.execute("DELETE FROM companies WHERE id = $1", empresa)
    finally:
        await conn.close()

    print("\n" + "=" * 68)
    if c.fallos:
        print(f"{len(c.fallos)} comprobacion(es) fallaron:")
        for f in c.fallos:
            print(f"  - {f}")
        return 1
    print(f"Las {c.ok} comprobaciones pasaron. El trigger hace lo que dice.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))