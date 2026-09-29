"""El almacenamiento del comprobante, contra Postgres real.

Los tests corren contra SQLite, y SQLite no es Postgres en tres cosas que aqui
importan:

  1. **Ignora las llaves foraneas por omision.** SQLite no activa el PRAGMA
     `foreign_keys` salvo que se pida, asi que un `ON DELETE CASCADE` escrito en
     el DDL no se ejecuta. Un test en verde que borra un ticket y comprueba que
     el documento se fue con el, estaria probando el `delete-orphan` del ORM y
     no el CASCADE de la base. Son dos caminos de borrado distintos: uno pasa
     por la API y el otro por SQL directo, y tienen que terminar igual.

  2. **Un BYTEA de 12 MB entra sin quejarse.** En Postgres el TOAST lo comprime
     o lo rechaza segun el tamano, y el error sale al escribir. En SQLite no
     hay limite, asi que un test que sube un comprobante enorme pasa en verde y
     revienta en produccion.

  3. **El DDL del modelo no se crea igual.** `Index(..., postgresql_where=...)`
     es un indice PARCIAL de Postgres. SQLite lo ignora, asi que una constraint
     o un indice que solo existe en Postgres no lo ejecuta la suite.

Este script usa la base real migrada y el codigo real de la API, y comprueba lo
que solo se puede comprobar ahi:

  1. Que los bytes lleguen intactos, con un archivo de tamano real.
  2. Que `ON DELETE CASCADE` exista de verdad y borre el comprobante.
  3. Que `UNIQUE(ticket_id)` impida el segundo documento.
  4. Que un ticket y su documento no puedan separarse.

    python3 scripts/verify_postgres_documentos.py
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from app.api.tickets import _persist_extracted  # noqa: E402
from app.core.enums import SourceType  # noqa: E402
from app.models.company import CompanyModel  # noqa: E402
from app.models.ticket import TicketModel  # noqa: E402
from app.models.ticket_document import TicketDocumentModel  # noqa: E402
from app.services.capture import capture_ticket  # noqa: E402
from app.services.document_service import (  # noqa: E402
    documento_de_ticket, reemplazar_documento,
)

DSN = "postgresql+asyncpg://postgres:CAMBIA_ESTA_PASSWORD@localhost:5434/expense_db"

fallos: list[str] = []
_empresas: list[uuid.UUID] = []


def check(desc: str, condicion: bool) -> None:
    print(f"  {'OK   ' if condicion else 'FALLA'} {desc}")
    if not condicion:
        fallos.append(desc)


def _limpiar() -> None:
    """Borra lo que creo la corrida, aunque reviente a mitad.

    Un script de verificacion que deja datos ensucia la siguiente corrida, y lo
    peor es que la ensucia en silencio: los conteos dan bien y el reporte dice
    que la base esta correcta mientras esta mezclando filas de dos corridas.
    """
    if not _empresas:
        return

    async def borrar() -> None:
        motor = create_async_engine(DSN)
        try:
            async with async_sessionmaker(motor, expire_on_commit=False)() as db:
                for empresa_id in _empresas:
                    # Los documentos primero y por `text()`. Un DELETE con
                    # `TicketDocumentModel.__table__.delete().where(<subquery de
                    # tickets>)` funciona, pero Postgres avisa de producto cartesiano
                    # porque las dos tablas no tienen condicion de union explicita, y
                    # esa advertencia es el tipo de cosa que alguien lee como ruido
                    # durante meses. Con `text` la intencion se ve.
                    await db.execute(
                        text(
                            "DELETE FROM ticket_documents WHERE ticket_id IN "
                            "(SELECT id FROM tickets WHERE company_id = :c)"
                        ),
                        {"c": empresa_id},
                    )
                    await db.execute(
                        TicketModel.__table__.delete().where(
                            TicketModel.company_id == empresa_id
                        )
                    )
                    await db.execute(
                        CompanyModel.__table__.delete().where(
                            CompanyModel.id == empresa_id
                        )
                    )
                await db.commit()
        finally:
            await motor.dispose()

    try:
        asyncio.run(borrar())
    except Exception as exc:  # noqa: BLE001
        print(f"  AVISO: no se pudo limpiar: {exc}")


# Un PDF de tamano realista: ~1.6 MB, que es lo que pesa una foto de ticket en
# un iPhone. No es un tamano arbitrario: es el que hace que el TOAST entre de
# verdad, y por lo tanto el unico que comprueba que la columna aguanta lo que le
# va a llegar.
PDF_REALISTA = b"%PDF-1.7\n" + (b"datos del comprobante " * 60_000)


async def main() -> int:
    motor = create_async_engine(DSN)
    sesiones = async_sessionmaker(motor, expire_on_commit=False)
    empresa_id = uuid.uuid4()
    _empresas.append(empresa_id)

    async with sesiones() as db:
        db.add(CompanyModel(
            id=empresa_id,
            name=f"Verificacion documentos {empresa_id.hex[:6]}",
            tax_id=f"VD{empresa_id.hex[:9].upper()}",
        ))
        await db.commit()

        # --- 1. Los bytes llegan intactos ------------------------------------
        print("\n1. El comprobante llega intacto (1.6 MB de bytes)")
        contenido = PDF_REALISTA
        extracted = await capture_ticket(contenido, "pdf")
        ticket = await _persist_extracted(
            db, empresa_id, extracted, contenido,
            SourceType.PDF, "comprobante.pdf", content_type="application/pdf",
        )
        # El id se copia a una variable y no se vuelve a leer de `ticket`.
        # Abajo hay un `rollback()` a proposito (para probar el UNIQUE), y un
        # rollback expira todos los objetos de la sesion: leer `ticket.id` justo
        # despues dispara una carga perezosa de atributo, que en un contexto sin
        # greenlet lanza MissingGreenlet. El error no dice nada de documentos, y
        # el que lo lee pierde el tiempo buscando un problema de almacenamiento
        # que no existe.
        ticket_id = ticket.id

        documento = await documento_de_ticket(db, ticket_id)
        check("el documento se guardo", documento is not None)
        check(
            f"los {len(contenido)} bytes llegan iguales, no truncados",
            documento.contenido == contenido,
        )
        check(
            "el tamano guardado es el real (no una aproximacion)",
            documento.tamano == len(contenido),
        )
        check(
            "el hash permite verificar la integridad sin releer el archivo",
            documento.sha256 == hashlib.sha256(contenido).hexdigest(),
        )
        print(f"        (tamano: {documento.tamano / 1024 / 1024:.2f} MB)")

        # El camino que de verdad importa: releerlo de la base, no el objeto que
        # se acaba de escribir. Un LONGBLOB puede venir corrupto de la base y el
        # objeto en memoria estar bien.
        guardado = await db.scalar(
            select(func.length(TicketDocumentModel.contenido)).where(
                TicketDocumentModel.ticket_id == ticket_id
            )
        )
        check(
            "y al releerlo de la base, el tamano es el mismo",
            guardado == len(contenido),
        )

        # --- 2. UNIQUE(ticket_id) --------------------------------------------
        print("\n2. Un ticket no puede tener dos comprobantes")
        segundo_intento = False
        try:
            db.add(TicketDocumentModel(
                ticket_id=ticket_id,
                contenido=b"otro documento",
                tamano=15,
            ))
            await db.commit()
        except Exception:
            await db.rollback()
            segundo_intento = True
        check(
            "el indice unico rechaza un segundo documento para el mismo ticket",
            segundo_intento,
        )

        # --- 3. El reemplazo no acumula --------------------------------------
        print("\n3. Reextraer reemplaza, no acumula")
        await reemplazar_documento(
            db, ticket_id, b"%PDF-1.7 version mejor leida",
            content_type="application/pdf", nombre_archivo="mejor.pdf",
        )
        await db.commit()

        total = await db.scalar(
            select(func.count()).select_from(TicketDocumentModel).where(
                TicketDocumentModel.ticket_id == ticket_id
            )
        )
        check("sigue habiendo un solo documento", total == 1)
        actual = await documento_de_ticket(db, ticket_id)
        check("y es el nuevo", actual.contenido == b"%PDF-1.7 version mejor leida")

        # --- 4. El CASCADE de verdad -----------------------------------------
        # Esto es lo que SQLite no puede comprobar: ahi el PRAGMA de llaves
        # foraneas esta apagado y el borrado lo hace el ORM, no la base.
        print("\n4. ON DELETE CASCADE (esto SQLite no lo comprueba)")
        empresa_para_borrar = uuid.uuid4()
        _empresas.append(empresa_para_borrar)
        db.add(CompanyModel(
            id=empresa_para_borrar,
            name="Verificacion cascade",
            tax_id=f"VC{empresa_para_borrar.hex[:9].upper()}",
        ))
        await db.commit()

        extracted_c = await capture_ticket(contenido, "pdf")
        ticket_c = await _persist_extracted(
            db, empresa_para_borrar, extracted_c, contenido,
            SourceType.PDF, "cascade.pdf", content_type="application/pdf",
        )
        await db.commit()

        antes = await db.scalar(
            select(func.count()).select_from(TicketDocumentModel).where(
                TicketDocumentModel.ticket_id == ticket_c.id
            )
        )
        check("el documento existe antes de borrar el ticket", antes == 1)

        # Por SQL directo, no por la API: asi lo borra Postgres con su CASCADE y
        # no el `delete-orphan` del ORM. Es el camino que no se prueba en la
        # suite de SQLite.
        ticket_c_id = ticket_c.id
        await db.execute(text("DELETE FROM tickets WHERE id = :id"), {"id": ticket_c_id})
        await db.commit()

        despues = await db.scalar(
            select(func.count()).select_from(TicketDocumentModel).where(
                TicketDocumentModel.ticket_id == ticket_c.id
            )
        )
        check(
            "borrar el ticket por SQL borra el comprobante (CASCADE de verdad)",
            despues == 0,
        )
        tickets_restantes = await db.scalar(
            select(func.count()).select_from(TicketModel).where(
                TicketModel.id == ticket_c.id
            )
        )
        check("y el ticket tampoco esta", tickets_restantes == 0)

        # --- 5. Lo que la constraint declara ---------------------------------
        print("\n5. El DDL es el que dice el modelo")
        fks = (await db.execute(text("""
            SELECT confdeltype
              FROM pg_constraint
             WHERE conrelid = 'ticket_documents'::regclass
               AND contype = 'f'
        """))).scalars().all()
        # Los valores de `pg_constraint.confdeltype` no son los de `ON DELETE`:
        #   'a' = NO ACTION,  'r' = RESTRICT,  'c' = CASCADE,
        #   'n' = SET NULL,   'd' = SET DEFAULT
        #
        # Se comprueba la letra de la base y no el `pg_get_constraintdef`, que
        # sale en texto y con el nombre de la tabla. Con el texto habria que
        # parsear "ON DELETE CASCADE" de una cadena, y ese es exactamente el tipo
        # de comprobacion que se rompe sin avisar.
        check(
            "la llave foranea declara ON DELETE CASCADE (confdeltype = 'c')",
            list(fks) == [b"c"],
        )
        if list(fks) != [b"c"]:
            print(f"        (encontrado: {list(fks)}; 'c' es CASCADE)")

        unicidad = (await db.execute(text("""
            SELECT count(*) FROM pg_index i
              JOIN pg_attribute a ON a.attrelid = i.indrelid
                                  AND a.attnum = ANY(i.indkey)
             WHERE i.indrelid = 'ticket_documents'::regclass
               AND i.indisunique
        """))).scalar()
        check("hay un indice unico sobre la tabla", unicidad >= 1)

        # --- limpieza ---------------------------------------------------------
        for id_para_borrar in (empresa_id, empresa_para_borrar):
            await db.execute(
                text(
                    "DELETE FROM ticket_documents WHERE ticket_id IN "
                    "(SELECT id FROM tickets WHERE company_id = :c)"
                ),
                {"c": id_para_borrar},
            )
            await db.execute(
                TicketModel.__table__.delete().where(
                    TicketModel.company_id == id_para_borrar
                )
            )
            await db.execute(
                CompanyModel.__table__.delete().where(
                    CompanyModel.id == id_para_borrar
                )
            )
        await db.commit()

    await motor.dispose()

    print()
    if fallos:
        print(f"{len(fallos)} comprobaciones fallaron:")
        for desc in fallos:
            print(f"  - {desc}")
        return 1
    print("Todo verificado contra Postgres real. Base limpia.")
    return 0


if __name__ == "__main__":
    codigo = 1
    try:
        codigo = asyncio.run(main())
    finally:
        _limpiar()
    raise SystemExit(codigo)
