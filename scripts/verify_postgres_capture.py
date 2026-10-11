"""Prueba de la ruta de captura contra Postgres real.

Los tests corren contra SQLite. SQLite construye el esquema desde el modelo y
acepta cosas que Postgres rechaza, asi que un test en verde no dice que la
fila se pueda guardar en la base de verdad.

Este script usa la base real migrada y el codigo real de la API, y comprueba lo
que solo se puede comprobar con Postgres:

  1. Que `confidence_source` acepte 'pdf_text' y 'rules'. La columna es
     VARCHAR(20) y no tiene CHECK, asi que deberia, pero "deberia" no es una
     verificacion. Si alguien agrega una constraint restricting los origenes
     permitidos y no incluye estos, aqui se ve.
  2. Que un documento sin fecha se guarde con la fecha local y quede con
     `date_missing` visible. La columna es NOT NULL, y el marcador es lo unico
     que distingue "fechado hoy" de "registrado hoy".
  3. Que el PDF impreso se auto-apruebe con la confianza de la evidencia, y no
     con la de un modelo.
  4. Que el hash de contenido deduplique en Postgres y no solo en SQLite.

No usa la IA: la ruta que se verifica aqui es la determinista, y para la parte
de vision basta con comprobar que el PDF escaneado se renderiza a imagen, que es
lo que nunca funciono porque `_pdf_to_images` importaba `fitz`.

    python3 scripts/verify_postgres_capture.py
"""

from __future__ import annotations

import asyncio
import io
import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from app.api.tickets import _persist_extracted, get_review_queue  # noqa: E402
from app.core.enums import (  # noqa: E402
    AUTO_APPROVE_CONFIDENCE,
    UNKNOWN_PROVIDER,
    ConfidenceSource,
    ExtractionStatus,
    SourceType,
)
from app.models.company import CompanyModel  # noqa: E402
from app.models.ticket import TicketModel  # noqa: E402
from app.services.capture import confianza_por_campos, capture_ticket  # noqa: E402

DSN = "postgresql+asyncpg://postgres:CAMBIA_ESTA_PASSWORD@localhost:5434/expense_db"

fallos: list[str] = []

# Empresa que creo la corrida actual. Vive aqui para que la limpieza pueda
# correr aunque `main` reviente a mitad: ver `_limpiar_si_hubo_excepcion`.
_empresa_de_la_corrida: uuid.UUID | None = None


def _limpiar_si_hubo_excepcion() -> None:
    """Borra lo que creo la corrida, incluso si esta fallo.

    Un script de verificacion que deja datos ensucia la siguiente corrida. Peor
    que ensuciar: la ensucia de forma silenciosa, porque los conteos dan bien y
    el script termina reportando que la base esta correcta cuando en realidad
    esta mezclando filas de dos corridas distintas.

    No es hipotetico. Paso aqui: una excepcion en la ultima comprobacion dejo
    tres tickets, y la corrida siguiente reporto "hay 4 filas" contando 3
    propias y 1 vieja. Por eso la limpieza va en un `finally` y no al final del
    camino feliz.
    """
    if _empresa_de_la_corrida is None:
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
                await db.commit()
        finally:
            await motor.dispose()

    try:
        asyncio.run(borrar())
    except Exception as exc:  # noqa: BLE001
        print(f"  AVISO: no se pudo limpiar la empresa de prueba: {exc}")


def check(desc: str, condicion: bool) -> None:
    print(f"  {'OK   ' if condicion else 'FALLA'} {desc}")
    if not condicion:
        fallos.append(desc)


TICKET_BUENO = (
    "Tiendas Ramirez SA de CV\n"
    "RFC: TRAM910101XXX\n"
    "FECHA EXPEDICION: 15/03/2025\n"
    "DESCRIPCION                IMPORTE\n"
    "Cafe en grano 1kg          250.00\n"
    "Refresco 600ml              35.50\n"
    "SUBTOTAL 964.00\n"
    "IVA (16%) 136.00\n"
    "TOTAL 1,100.00\n"
)

TICKET_SIN_FECHA = (
    "Tiendas Ramirez SA de CV\n"
    "RFC: TRAM910101XXX\n"
    "TOTAL 1,100.00\n"
)


def pdf_impreso() -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Courier", size=11)
    for linea in TICKET_BUENO.strip().split("\n"):
        pdf.cell(0, 6, text=linea, new_x="LMARGIN", new_y="NEXT")
    buffer = io.BytesIO()
    pdf.output(buffer)
    return buffer.getvalue()


def pdf_escaneado(destino: Path) -> bytes:
    sys.path.insert(0, str(RAIZ / "scripts"))
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "make_sample_receipts", RAIZ / "scripts" / "make_sample_receipts.py"
    )
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    ruta = destino / "escaneado.pdf"
    modulo._es_escaneado(ruta)
    return ruta.read_bytes()


async def main() -> int:
    motor = create_async_engine(DSN)
    sesiones = async_sessionmaker(motor, expire_on_commit=False)
    empresa_id = uuid.uuid4()

    global _empresa_de_la_corrida
    _empresa_de_la_corrida = empresa_id

    async with sesiones() as db:
        db.add(CompanyModel(
            id=empresa_id,
            name=f"Verificacion captura {empresa_id.hex[:6]}",
            tax_id=f"VC{empresa_id.hex[:9].upper()}",
        ))
        await db.commit()

        # --- 1. PDF impreso: reglas, sin IA, con su propio origen ------------
        print("\n1. PDF impreso (texto extraible, sin IA)")
        contenido = pdf_impreso()
        extracted = await capture_ticket(contenido, "pdf")
        check(
            "el proveedor se lee en minuscula sostenida (Tiendas Ramirez SA de CV)",
            extracted.provider_name == "Tiendas Ramirez SA de CV",
        )
        check("el subtotal se extrae", extracted.subtotal == Decimal("964.00"))

        ticket = await _persist_extracted(
            db, empresa_id, extracted, contenido, SourceType.PDF, "impreso.pdf"
        )
        row = (await db.execute(
            select(TicketModel).where(TicketModel.id == ticket.id)
        )).scalar_one()

        check(
            "confidence_source es 'pdf_text' en la base, no 'llm'",
            row.confidence_source == ConfidenceSource.PDF_TEXT.value,
        )
        check("la columna acepta 'pdf_text' de verdad (VARCHAR + sin CHECK)", row.confidence_source is not None)
        check("el total es el del documento", row.total_amount == Decimal("1100.00"))
        check("el IVA es el del documento", row.tax_amount == Decimal("136.00"))
        check("la fecha es la del documento, no la de hoy", row.expense_date == date(2025, 3, 15))
        check(
            "el estado es AUTO_APROBADO (checks ok y confianza alta)",
            row.extraction_status == ExtractionStatus.AUTO_APROBADO.value,
        )
        check(
            "la confianza guardada es la de la evidencia, no una redonda inventada",
            float(row.confidence) == pytest_approx(
                confianza_por_campos(tiene_rfc=True, tiene_subtotal=True, tiene_fecha=True)
            ),
        )
        check(
            f"la confianza supera el umbral de auto-aprobacion ({AUTO_APPROVE_CONFIDENCE})",
            float(row.confidence) >= AUTO_APPROVE_CONFIDENCE,
        )

        # --- 2. Texto plano: reglas, origen 'rules' -------------------------
        print("\n2. Texto plano")
        contenido_txt = TICKET_BUENO.encode()
        extracted_txt = await capture_ticket(contenido_txt, "text")
        ticket_txt = await _persist_extracted(
            db, empresa_id, extracted_txt, contenido_txt, SourceType.PDF, "ticket.txt"
        )
        row_txt = (await db.execute(
            select(TicketModel).where(TicketModel.id == ticket_txt.id)
        )).scalar_one()
        check("confidence_source es 'rules'", row_txt.confidence_source == ConfidenceSource.RULES.value)

        # --- 3. Documento sin fecha ------------------------------------------
        print("\n3. Documento sin fecha (el bug mas caro que se corrigio)")
        contenido_sin_fecha = TICKET_SIN_FECHA.encode()
        extracted_sin = await capture_ticket(contenido_sin_fecha, "text")
        check("la extraccion no inventa fecha", extracted_sin.expense_date is None)

        ticket_sin = await _persist_extracted(
            db, empresa_id, extracted_sin, contenido_sin_fecha, SourceType.PDF, "sin_fecha.txt"
        )
        row_sin = (await db.execute(
            select(TicketModel).where(TicketModel.id == ticket_sin.id)
        )).scalar_one()
        check("la columna NOT NULL se respeta con el marcador", row_sin.expense_date is not None)
        check("el marcador es la fecha local, no la de UTC", row_sin.expense_date == date.today())
        check("el motivo 'date_missing' queda visible en la fila", "date_missing" in (row_sin.validation_errors or ""))
        check("el ticket queda en PENDIENTE, no aprobado", row_sin.extraction_status == ExtractionStatus.PENDIENTE.value)
        check("y aparece en la cola de revision", row_sin.is_open_for_review)

        # --- 4. Idempotencia por hash ----------------------------------------
        print("\n4. Idempotencia de carga masiva")
        repetido = await _persist_extracted(
            db, empresa_id, extracted, contenido, SourceType.PDF, "impreso.pdf"
        )
        check("el mismo archivo no crea un ticket nuevo", repetido.id == ticket.id)
        total = (await db.execute(
            select(TicketModel).where(TicketModel.company_id == empresa_id)
        )).scalars().all()
        print(f"        (filas creadas: {len(total)} -> "
              f"{[t.source_file for t in total]})")
        check("siguen siendo 3 filas, no 4", len(total) == 3)

        # --- 5. PDF escaneado: renderiza a imagen ----------------------------
        print("\n5. PDF escaneado (sin capa de texto)")
        from app.services.parser_service import extract_pdf_text, render_pdf_pages

        escaneado = pdf_escaneado(Path("/tmp"))
        check("pdfplumber no encuentra texto (es una imagen)", extract_pdf_text(escaneado).strip() == "")
        paginas = render_pdf_pages(escaneado)
        check("la pagina se renderiza a imagen (esto antes devolvia lista vacia)", len(paginas) == 1)
        check("y es un JPEG de verdad", paginas[0].startswith(b"\xff\xd8\xff"))

        # --- 6. Lo que la cola muestra ---------------------------------------
        print("\n6. Lo que ve un humano en la cola")
        cola = await get_review_queue(company_id=empresa_id, status=None, limit=100, db=db)
        print(f"        (cola: total_open={cola.total_open}, por_estado={cola.por_estado}, "
              f"tickets={len(cola.tickets)})")
        en_cola = [t for t in cola.tickets if t.id == row_sin.id]
        check("el ticket sin fecha esta en la cola", len(en_cola) == 1)
        if en_cola:
            check("la cola explica por que esta ahi", "date_missing" in (en_cola[0].validation_errors or ""))

        # --- limpieza ---------------------------------------------------------
        await db.execute(
            TicketModel.__table__.delete().where(TicketModel.company_id == empresa_id)
        )
        await db.execute(
            CompanyModel.__table__.delete().where(CompanyModel.id == empresa_id)
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


def pytest_approx(valor: float):
    class _Aprox:
        def __eq__(self, otro) -> bool:
            return abs(float(otro) - valor) < 1e-9

    return _Aprox()


if __name__ == "__main__":
    codigo = 1
    try:
        codigo = asyncio.run(main())
    finally:
        _limpiar_si_hubo_excepcion()
    raise SystemExit(codigo)
