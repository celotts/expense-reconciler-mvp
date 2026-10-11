"""Prueba de extremo a extremo contra Postgres real.

Los tests del proyecto corren contra SQLite, que construye el esquema desde el
modelo. Eso no alcanza para dos cosas:

  1. SQLite no aplica `postgresql_where` de los indices, asi que un indice
     parcial mal escrito pasa los tests y no existe en produccion.
  2. SQLite acepta cosas que Postgres rechaza (tipos, defaults, nullability) y
     al reves: una columna que el codigo espera se llama distinto de como
     esta en la base solo se descubre con UndefinedColumnError en produccion.

Este script usa la MISMA base migrada y el MISMO codigo de la API: el gate, la
persistencia idempotente, la cola de revision y la revision humana.

    python3 scripts/verify_postgres_gate.py
"""

import asyncio
import sys
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.models.user import UserModel
from app.core.enums import SourceType
from app.api.tickets import _persist_extracted, get_review_queue, review_ticket
from app.schemas.ticket import TicketReviewRequest
from app.services.parser_service import TicketExtractionResult

DSN = "postgresql+asyncpg://postgres:CAMBIA_ESTA_PASSWORD@localhost:5434/expense_db"

# La empresa que creo la corrida actual, para poder borrarla aunque el script
# reviente a mitad. Ver `_limpiar_si_hubo_excepcion`.
_empresa_de_la_corrida = None
_correo_de_la_corrida = None


def _limpiar_si_hubo_excepcion() -> None:
    """Borra los datos de prueba aunque `main` falle.

    Este script no limpiebaba nada: cada corrida dejaba una empresa y sus
    tickets en la base. Como la idempotencia por hash uniquifica el contenido en
    cada corrida, los conteos no se rompian y el script pasaba en verde mientras
    la base se llenaba de datos de verificacion. Un verificador que acumula lo
    que verifica deja de poder verificar: al cabo de un rato cualquier consulta
    a la base incluye filas de prueba y nadie sabe cuales son.
    """
    if _empresa_de_la_corrida is None and _correo_de_la_corrida is None:
        return

    async def borrar() -> None:
        motor = create_async_engine(DSN)
        try:
            async with async_sessionmaker(motor, expire_on_commit=False)() as db:
                await db.execute(
                    TicketModel.__table__.delete().where(
                        TicketModel.company_id == _empresa_de_la_corrida
                    )
                )
                await db.execute(
                    CompanyModel.__table__.delete().where(
                        CompanyModel.id == _empresa_de_la_corrida
                    )
                )
                if _correo_de_la_corrida is not None:
                    await db.execute(
                        UserModel.__table__.delete().where(
                            UserModel.email == _correo_de_la_corrida
                        )
                    )
                await db.commit()
        finally:
            await motor.dispose()

    try:
        asyncio.run(borrar())
    except Exception as exc:  # noqa: BLE001
        print(f"  AVISO: no se pudo limpiar la empresa de prueba: {exc}")

fallos: list[str] = []


def check(desc: str, condicion: bool) -> None:
    print(f"  {'OK  ' if condicion else 'FALLA'}  {desc}")
    if not condicion:
        fallos.append(desc)


def extraccion(**kw):
    base = dict(
        provider_name="TIENDAS RAMIREZ",
        provider_tax_id="TRAM910101XXX",
        total_amount=Decimal("1160.00"),
        tax_amount=Decimal("160.00"),
        expense_date=date.today(),
        raw_text="texto",
        confidence=0.96,
        subtotal=Decimal("1000.00"),
    )
    base.update(kw)
    return TicketExtractionResult(**base)


def contenido(base: str, corrida: str) -> bytes:
    """Contenido unico por corrida.

    El script tiene que poder repetirse sobre la misma base. Como la
    idempotencia por hash es justamente lo que se esta probando, usar el mismo
    contenido dos veces devolveria el ticket de la corrida anterior y las
    aserciones medirian otra cosa. Se uniquifica el contenido, no el
    comportamiento.
    """
    return f"{base}:{corrida}".encode()


async def main() -> int:
    engine = create_async_engine(DSN, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        empresa = CompanyModel(name=f"Verify {uuid4().hex[:6]}", tax_id=f"V{uuid4().hex[:9].upper()}")
        db.add(empresa)
        await db.commit()
        await db.refresh(empresa)
        cid = empresa.id
        global _empresa_de_la_corrida, _correo_de_la_corrida
        _empresa_de_la_corrida = cid
        corrida = uuid4().hex[:8]

        # `review_ticket` escribe en `reviewed_by` el correo del token, así que
        # hace falta una cuenta de verdad. No un objeto simulado a propósito: si
        # el endpoint guardara un texto fijo, el simulacro lo taparía, y la
        # comprobación de más abajo leería ese texto fijo y pasaría.
        from app.core.security import hashear_contrasena

        _correo_de_la_corrida = f"gate{uuid4().hex[:10]}@verify.local"
        usuario = UserModel(
            email=_correo_de_la_corrida,
            nombre="Verificación Gate",
            password_hash=hashear_contrasena("contrasena-de-verificacion"),
        )
        db.add(usuario)
        await db.commit()
        await db.refresh(usuario)

        print("\n1) Persistencia con el gate contra Postgres real")
        auto = await _persist_extracted(
            db, cid, extraccion(), contenido("archivo-auto", corrida), SourceType.IMAGE, source_file="a.jpg"
        )
        check("confianza alta + checks OK -> AUTO_APROBADO", auto.extraction_status == "AUTO_APROBADO")

        check(
            "confidence se guardó como Decimal exacto",
            Decimal(str(auto.confidence)) == Decimal("0.960"),
        )
        check("source_hash presente", bool(auto.source_hash))

        print("\n2) Idempotencia por hash (índice único parcial real)")
        otra = await _persist_extracted(
            db, cid, extraccion(), contenido("archivo-auto", corrida), SourceType.IMAGE, source_file="a.jpg"
        )
        check("mismo contenido -> mismo ticket", auto.id == otra.id)

        distinto = await _persist_extracted(
            db, cid, extraccion(), contenido("archivo-otro", corrida), SourceType.IMAGE, source_file="b.jpg"
        )
        check("contenido distinto -> ticket distinto", auto.id != distinto.id)

        print("\n3) Documento ilegible se guarda y NO se pierde")
        ilegible = await _persist_extracted(
            db, cid,
            extraccion(
                provider_name="Unknown Provider", total_amount=Decimal("0"),
                tax_amount=Decimal("0"), confidence=0.99, subtotal=None,
            ),
            contenido("papel-quemado", corrida), SourceType.IMAGE, source_file="c.jpg",
        )
        check("estado PENDIENTE", ilegible.extraction_status == "PENDIENTE")
        check("tiene id: no se descartó", ilegible.id is not None)
        check("guardó el motivo", "provider_missing" in (ilegible.validation_errors or ""))

        print("\n4) Confianza alta NO salva checks rotos")
        roto = await _persist_extracted(
            db, cid, extraccion(subtotal=Decimal("10.00"), confidence=0.99),
            contenido("aritmetica-rota", corrida), SourceType.IMAGE, source_file="d.jpg",
        )
        check("no auto-aprobado", roto.extraction_status != "AUTO_APROBADO")
        check("motivo con los dos números", "leido=1160.00" in (roto.validation_errors or ""))

        print("\n5) Cola de revisión")
        cola = await get_review_queue(company_id=cid, status=None, limit=100, db=db)
        check("la cola trae los 2 abiertos", cola.total_open == 2)
        check("PENDIENTE cuenta 1", cola.por_estado.get("PENDIENTE") == 1)
        # auto y distinto son los dos AUTO_APROBADO: mismo contenido y mismo
        # idempotente no cuentan, pero `distinto` es otro documento y sí cuenta.
        check("AUTO_APROBADO cuenta 2 (auto + distinto)", cola.por_estado.get("AUTO_APROBADO") == 2)
        check("REQUIERE_REVISION cuenta 1", cola.por_estado.get("REQUIERE_REVISION") == 1)
        check("antigüedad calculada sin error", cola.antiguedad_promedio_dias is not None)
        check(
            "la cola trae los tickets con datos rotos",
            all(t.extraction_status in ("PENDIENTE", "REQUIERE_REVISION") for t in cola.tickets),
        )
        check(
            "serializa un total de 0 sin reventar",
            any(t.total_amount == Decimal("0.00") for t in cola.tickets),
        )

        filtrada = await get_review_queue(company_id=cid, status="PENDIENTE", limit=100, db=db)
        check("filtro por estado devuelve 1", len(filtrada.tickets) == 1)
        check("el conteo global no salta al filtrar", filtrada.total_open == 2)

        print("\n6) Revisión humana: aprobar lo que sigue roto debe fallar")
        try:
            await review_ticket(
                ilegible.id,
                TicketReviewRequest(action="approve"),
                usuario,
                db=db,
            )
            check("rechaza aprobar datos rotos", False)
        except Exception as exc:  # HTTPException
            check("rechaza aprobar datos rotos (422)", "422" in str(exc) or "No se puede aprobar" in str(exc))

        print("\n7) Corregir y aprobar")
        aprobado = await review_ticket(
            ilegible.id,
            TicketReviewRequest(
                action="approve",
                provider_name="OXXO",
                total_amount=Decimal("150.00"),
                tax_amount=Decimal("20.69"),
                notes="Leído a mano",
            ),
            usuario,
            db=db,
        )
        check("queda APROBADO", aprobado.extraction_status == "APROBADO")
        check("limpió los errores", aprobado.validation_errors is None)
        check("guardó reviewed_at", aprobado.reviewed_at is not None)
        check("guardó la nota", aprobado.review_notes == "Leído a mano")
        check(
            "reviewed_by es el correo de quien revisó, no un texto fijo",
            aprobado.reviewed_by == _correo_de_la_corrida,
        )

        print("\n8) Rechazar un ilegible: la cola se puede vaciar")
        await review_ticket(
            roto.id,
            TicketReviewRequest(action="reject", notes="ilegible"),
            usuario,
            db=db,
        )
        cola_final = await get_review_queue(company_id=cid, status=None, limit=100, db=db)
        check("la cola quedó vacía", cola_final.total_open == 0)

        print("\n9) created_at vuelve con zona horaria (timestamptz real)")
        check("created_at es aware", auto.created_at is not None and auto.created_at.tzinfo is not None)

        print("\n10) Índices parciales existen y son parciales de verdad")
        from sqlalchemy import text as sql_text

        r = await db.execute(sql_text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename='tickets' AND indexname='ix_tickets_review_queue'"
        ))
        idx = r.scalar_one()
        check("ix_tickets_review_queue tiene WHERE", "WHERE" in idx)
        check("  ...sobre los estados abiertos", "REQUIERE_REVISION" in idx and "PENDIENTE" in idx)

        r = await db.execute(sql_text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename='tickets' AND indexname='ix_tickets_source_hash'"
        ))
        idx = r.scalar_one()
        check("ix_tickets_source_hash es UNIQUE", "UNIQUE INDEX" in idx)
        check("  ...solo cuando source_hash IS NOT NULL", "source_hash IS NOT NULL" in idx)

    await engine.dispose()

    print()
    if fallos:
        print(f"FALLARON {len(fallos)}:")
        for f in fallos:
            print(f"  - {f}")
        return 1
    print("TODO OK contra Postgres real")
    return 0


codigo = 1
try:
    codigo = asyncio.run(main())
finally:
    _limpiar_si_hubo_excepcion()
sys.exit(codigo)
