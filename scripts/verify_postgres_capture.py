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
  4. Que el hash de contenido deduplique en Postgres y no solo en SQLite, y
     que deduplique DENTRO de una empresa sin mezclar empresas: el hash es el
     SHA-256 del archivo y no lleva empresa dentro, asi que buscar solo por
     hash hacia que la segunda empresa que subia el mismo comprobante recibiera
     el ticket de la primera.
  5. Que el tope de `raw_text` llegue a cumplir en la columna, no solo en
     memoria: un PDF de 100 paginas deja varios MB si no se recorta.

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
from app.services.confidence_gate import compute_source_hash  # noqa: E402

DSN = "postgresql+asyncpg://postgres:CAMBIA_ESTA_PASSWORD@localhost:5434/expense_db"

fallos: list[str] = []

# Empresas que creo la corrida actual. Es una LISTA y no una sola empresa
# porque la comprobacion 4b crea una segunda a proposito: verificar que el
# mismo comprobante puede vivir en dos empresas necesita dos. Con una variable
# sola, la segunda empresa se escapaba de la limpieza y la siguiente corrida
# encontra una empresa de mas.
#
# Vive aqui para que la limpieza pueda correr aunque `main` reviente a mitad:
# ver `_limpiar_si_hubo_excepcion`.
_empresas_de_la_corrida: list[uuid.UUID] = []


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
    if not _empresas_de_la_corrida:
        return

    async def borrar() -> None:
        motor = create_async_engine(DSN)
        try:
            async with async_sessionmaker(motor, expire_on_commit=False)() as db:
                for empresa_id in _empresas_de_la_corrida:
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

    _empresas_de_la_corrida.append(empresa_id)

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

        # --- 4b. El mismo archivo en OTRA empresa ---------------------------
        # Lo que se rompió aquí no era un duplicado: era una fuga. El hash es el
        # SHA-256 del archivo y no lleva empresa dentro, así que buscando solo
        # por hash la segunda empresa encontraba el ticket de la primera y lo
        # devolvía. El gasto no se guardaba para ella y de paso veía un ticket
        # ajeno.
        #
        # Esto solo se puede comprobar en Postgres. En SQLite el índice único
        # del modelo no se crea (es DDL de Postgres) y el INSERT de la segunda
        # empresa pasa, comprobando nada.
        print("\n4b. El mismo comprobante en otra empresa (lo que se fugaba)")
        otra_id = uuid.uuid4()
        _empresas_de_la_corrida.append(otra_id)
        db.add(CompanyModel(
            id=otra_id,
            name=f"Verificacion captura B {otra_id.hex[:6]}",
            tax_id=f"VB{otra_id.hex[:9].upper()}",
        ))
        await db.commit()

        # El mismo archivo, misma extraccion, otra empresa. Es un caso legitimo:
        # el mismo papel, dos cuentas distintas.
        en_otra = await _persist_extracted(
            db, otra_id, extracted, contenido, SourceType.PDF, "impreso.pdf"
        )
        check(
            "la segunda empresa SI puede registrar su propio ticket",
            en_otra.id != ticket.id,
        )
        fila_otra = (await db.execute(
            select(TicketModel).where(TicketModel.id == en_otra.id)
        )).scalar_one()
        check("y el ticket guardado es de la segunda empresa", fila_otra.company_id == otra_id)
        check(
            "y no se le devuelve el ticket de la primera",
            fila_otra.company_id != empresa_id,
        )

        # Y el indice tiene que sujetar la unicidad DENTRO de la empresa, que es
        # lo que evita el gasto duplicado. Se fuerza con un INSERT que no pasa
        # por `_persist_extracted` a proposito: esa funcion ya filtra, asi que
        # con ella no se puede comprobar que la base loImpida.
        hash_del_archivo = compute_source_hash(contenido)
        duplicado_intra = False
        try:
            db.add(TicketModel(
                company_id=otra_id,
                provider_name="Duplicado forzado",
                total_amount=Decimal("1.00"),
                expense_date=date(2025, 3, 15),
                source_hash=hash_del_archivo,
            ))
            await db.commit()
        except Exception:
            await db.rollback()
            duplicado_intra = True
        check(
            "el indice unico (company_id, source_hash) impide el duplicado en la misma empresa",
            duplicado_intra,
        )

        # Idempotencia dentro de la segunda empresa: tambien se comporta bien.
        repetido_otra = await _persist_extracted(
            db, otra_id, extracted, contenido, SourceType.PDF, "impreso.pdf"
        )
        check(
            "y dentro de la segunda empresa la idempotencia sigue funcionando",
            repetido_otra.id == en_otra.id,
        )

        # --- 4c. El tope de raw_text -----------------------------------------
        # Un PDF de 100 paginas deja varios MB en una fila. El recorte vive en
        # el validador de `TicketExtractionResult`, asi que aqui se comprueba lo
        # que importa: que a la base llegue recortado, con las dos puntas.
        print("\n4c. El tope de raw_text")
        from app.services.parser_service import (
            RAW_TEXT_MAX_CHARS, recortar_raw_text,
        )

        enorme = "PROVEEDOR EN LA PRIMERA LINEA\n" + ("relleno " * 60_000) + \
                  "\nTOTAL 1,100.00"
        recortado = recortar_raw_text(enorme)
        check(
            "un texto enorme no llega entero a la columna",
            len(recortado) <= RAW_TEXT_MAX_CHARS + 200,
        )
        check("el recorte se marca en el texto", "caracteres omitidos" in recortado)
        check("y se conserva la cabeza (el proveedor)", "PROVEEDOR EN LA PRIMERA" in recortado)
        check(
            "y se conserva la cola (el total), que es donde esta el dato que se revisa",
            "TOTAL 1,100.00" in recortado,
        )
        check(
            "un texto corto no se marca, porque no se recorto",
            recortar_raw_text("TOTAL 100.00") == "TOTAL 100.00",
        )

        ticket_enorme = await _persist_extracted(
            db, empresa_id,
            await capture_ticket(enorme.encode(), "text"),
            enorme.encode(), SourceType.PDF, "enorme.txt",
        )
        fila_enorme = (await db.execute(
            select(TicketModel).where(TicketModel.id == ticket_enorme.id)
        )).scalar_one()
        check(
            "y lo que llega a Postgres ya viene recortado, no en memoria",
            len(fila_enorme.raw_text or "") <= RAW_TEXT_MAX_CHARS + 200,
        )
        check(
            "el total del documento sigue disponible para el revisor",
            "TOTAL 1,100.00" in (fila_enorme.raw_text or ""),
        )
        await db.execute(
            TicketModel.__table__.delete().where(TicketModel.id == ticket_enorme.id)
        )
        await db.commit()

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
        # Las dos empresas, no solo la primera: la 4b creo la segunda y dejarla
        # haria que la siguiente corrida mezclara filas.
        for id_para_borrar in (empresa_id, otra_id):
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
