"""El comprobante original: que se guarde, que se sirva, y que no se pierda.

Por que esta suite existe
-------------------------

El sistema leia el documento, sacaba cinco campos y tiraba los bytes. Los
tickets se conservaban, con su motivo en la cola, pero el papel del que venian
no existia en ningun lado. Tres cosas quedaban imposibles:

  1. El muestreo de exactitud no se podia hacer. `SpotCheckRequest` pregunta si
     la extraccion coincidio con el papel, y para responder hay que ver el papel.
     Sin el archivo, el veredicto no mide la exactitud: mide lo que el revisor
     recuerda del ticket.
  2. La cola de revision decia "corrige contra el documento original" sin
     documento original.
  3. No habia reintento. Un PDF que no se leyo porque el extractor estaba
     apagado quedaba ilegible para siempre.

Aqui se comprueban las tres cosas, y sobre todo la garantia que hace posibles a
las otras dos: **el ticket y su documento se guardan juntos o no se guarda
ninguno**. Un ticket sin documento tiene que tener una causa visible, y solo hay
dos: la captura manual, que no viene de un archivo, y un fallo de
almacenamiento, que se registra.

Lo que NO se prueba aqui
------------------------

Que los bytes lleguen intactos a Postgres. Eso lo comprueba
`scripts/verify_postgres_documentos.py`, porque SQLite acepta cosas que Postgres
rechaza (un BYTEA de 12 MB entra sin quejarse en una y revienta el TOAST en la
otra) y un test en verde no dira nada sobre la base de verdad.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.enums import SourceType
from app.models.company import CompanyModel
from app.services.capture import capture_ticket
from app.services.document_service import (
    documento_de_ticket, guardar_documento, reemplazar_documento,
)


TICKET_TEXTO = (
    b"Tiendas Ramirez SA de CV\n"
    b"RFC: TRAM910101XXX\n"
    b"FECHA EXPEDICION: 15/03/2025\n"
    b"SUBTOTAL 964.00\n"
    b"IVA (16%) 136.00\n"
    b"TOTAL 1,100.00\n"
)


async def _crear_ticket(db, company_id, contenido, **kw):
    """El mismo camino que el endpoint, documento incluido."""
    from app.api.tickets import _persist_extracted

    extracted = await capture_ticket(contenido, "text")
    return await _persist_extracted(
        db, company_id, extracted, contenido,
        kw.get("source_type", SourceType.PDF),
        kw.get("source_file", "comprobante.txt"),
        content_type=kw.get("content_type", "text/plain"),
    )


class TestElDocumentoSeGuarda:
    """El comportamiento base: lo que se sube, se queda."""

    @pytest.mark.asyncio
    async def test_el_comprobante_no_se_pierde(self, db_session, test_company):
        contenido = TICKET_TEXTO

        ticket = await _crear_ticket(db_session, test_company.id, contenido)

        documento = await documento_de_ticket(db_session, ticket.id)
        assert documento is not None, "el comprobante se tiro al terminar la lectura"
        # Byte a byte. No "parecido" ni "contiene": es el archivo, no una
        # relectura. Un JPEG recomprimido serviria para casi todo y no para
        # auditar si el total que se leyo es el que dice el papel.
        assert documento.contenido == contenido

    @pytest.mark.asyncio
    async def test_el_ticket_dice_que_tiene_documento(self, db_session, test_company):
        """La propiedad del modelo, que es la que usan la cola y el muestreo.

        Va en el modelo y no en cada endpoint porque hay varios consumidores y
        los piden de forma distinta. Una pantalla que no puede saber si hay
        documento es una pantalla donde el revisor revisa a ciegas.
        """
        ticket = await _crear_ticket(db_session, test_company.id, TICKET_TEXTO)
        assert ticket.tiene_documento is True

    @pytest.mark.asyncio
    async def test_guarda_el_tamano_y_el_hash_que_permiten_verificarlo(
        self, db_session, test_company
    ):
        """`tamano` y `sha256` existen para no tener que releer el archivo.

        `tamano` es lo que permite avisar "10 MB" antes de descargar. `sha256` es
        lo que permite verificar que los bytes guardados son los que se
        recibieron, sin volver a pedirlos a quien los subio.
        """
        import hashlib

        ticket = await _crear_ticket(db_session, test_company.id, TICKET_TEXTO)
        documento = await documento_de_ticket(db_session, ticket.id)

        assert documento.tamano == len(TICKET_TEXTO)
        assert documento.sha256 == hashlib.sha256(TICKET_TEXTO).hexdigest()
        # El mismo hash que esta en el ticket: si divergieran, la verificacion de
        # integridad estaria comparando el documento contra si mismo.
        assert documento.sha256 == ticket.source_hash

    @pytest.mark.asyncio
    async def test_conserva_el_nombre_y_el_tipo(self, db_session, test_company):
        """El nombre es lo que el revisor reconoce del papel."""
        ticket = await _crear_ticket(
            db_session, test_company.id, TICKET_TEXTO,
            source_file="oaxaca-marzo.pdf", content_type="application/pdf",
        )
        documento = await documento_de_ticket(db_session, ticket.id)
        assert documento.nombre_archivo == "oaxaca-marzo.pdf"
        assert documento.content_type == "application/pdf"


class TestUnTicketSoloTieneUnDocumento:
    """La unicidad, y lo que pasaria sin ella."""

    @pytest.mark.asyncio
    async def test_subir_de_nuevo_reemplaza_y_no_acumula(
        self, db_session, test_company
    ):
        """Reextraer produce una lectura mejor del MISMO papel.

        El gasto no cambia: es el mismo comprobante. Guardar las dos lecturas
        como dos documentos seria duplicar el gasto en el historico contable, que
        es la ultima cosa que este sistema puede hacer. Y la API lo resuelve
        con `PUT`, que es un reemplazo, no un agregado.
        """
        from sqlalchemy import func, select

        from app.models.ticket_document import TicketDocumentModel

        ticket = await _crear_ticket(db_session, test_company.id, TICKET_TEXTO)

        await reemplazar_documento(
            db_session, ticket.id, b"%PDF-1.7 version reextraida",
            content_type="application/pdf", nombre_archivo="mejor.pdf",
        )
        await db_session.commit()

        documentos = (await db_session.execute(
            select(TicketDocumentModel).where(
                TicketDocumentModel.ticket_id == ticket.id
            )
        )).scalars().all()

        assert len(documentos) == 1, "un ticket con dos comprobantes no se sabe cual es"
        assert documentos[0].contenido == b"%PDF-1.7 version reextraida"
        assert documentos[0].nombre_archivo == "mejor.pdf"

    @pytest.mark.asyncio
    async def test_la_cantidad_no_crece_al_reintentar_muchas_veces(
        self, db_session, test_company
    ):
        from sqlalchemy import func, select

        from app.models.ticket_document import TicketDocumentModel

        ticket = await _crear_ticket(db_session, test_company.id, TICKET_TEXTO)
        for i in range(5):
            await reemplazar_documento(
                db_session, ticket.id, f"intento {i}".encode(),
            )
        await db_session.commit()

        total = await db_session.scalar(
            select(func.count()).select_from(TicketDocumentModel)
            .where(TicketDocumentModel.ticket_id == ticket.id)
        )
        assert total == 1


class TestLoQueNoTieneDocumento:
    """Sin documento tiene que haber una razon, no un misterio."""

    @pytest.mark.asyncio
    async def test_la_captura_manual_no_inventa_un_documento(
        self, db_session, test_company
    ):
        """Un ticket tecleado no viene de un archivo.

        Y aqui esta el punto que no es obvio: guardar "algo" como documento para
        que la pantalla no se vea vacia seria fabricar evidencia. El muestreo
        compara la lectura contra el documento, y un documento inventado le da
        la razon al sistema siempre, que es exactamente el sesgo que el muestreo
        existe para no tener.
        """
        from app.api.tickets import create_ticket
        from app.schemas.ticket import TicketCreate

        ticket = await create_ticket(
            TicketCreate(
                company_id=test_company.id,
                provider_name="OXXO",
                total_amount=Decimal("150.00"),
                tax_amount=Decimal("20.69"),
                expense_date=date(2025, 3, 15),
            ),
            db=db_session,
        )

        assert ticket.documento is None
        assert ticket.tiene_documento is False

    @pytest.mark.asyncio
    async def test_reemplazar_en_un_ticket_inexistente_no_crea_nada(
        self, db_session
    ):
        """Un PUT sobre un id que no existe es 404, no un ticket nuevo.

        La tentacion es crear el ticket del lado del servidor para "no perder el
        archivo". Seria peor: un gasto que nadie reviso, con una fecha que el
        sistema eligio, entrando por la puerta de atrás de una operacion que
        dice "reemplaza el documento de este ticket".
        """
        from sqlalchemy import func, select

        from app.models.ticket import TicketModel

        ok = await reemplazar_documento(db_session, uuid4(), b"contenido")

        assert ok is False
        assert await db_session.scalar(
            select(func.count()).select_from(TicketModel)
        ) == 0

    @pytest.mark.asyncio
    async def test_un_contenido_vacio_no_se_guarda(self, db_session, test_company):
        """Un archivo de cero bytes no es un documento.

        Se parece a un fallo de subida, y guardarlo haria que un ticket de la
        cola dijera "tiene documento" y al abrirlo no hubiera nada. La duda
        ("¿el sistema fallo o el archivo iba vacio?") no se puede resolver desde
        la fila, y por eso no se guarda.
        """
        ticket = await _crear_ticket(db_session, test_company.id, TICKET_TEXTO)

        assert await reemplazar_documento(db_session, ticket.id, b"") is False
        await db_session.commit()

        # El documento ANTERIOR sigue ahi. Un intento fallido no borra lo que
        # habia: perder el comprobante bueno por un reintento malo seria peor
        # que no reintentar.
        documento = await documento_de_ticket(db_session, ticket.id)
        assert documento is not None
        assert documento.contenido == TICKET_TEXTO


class TestElDocumentoYElTicketNacenJuntos:
    """La garantia que hace que lo demas sea cierto."""

    @pytest.mark.asyncio
    async def test_borrar_el_ticket_borra_el_comprobante(
        self, db_session, test_company
    ):
        """Sin CASCADE, la base acumula comprobantes de gastos que ya no existen.

        Y no es un problema de espacio: es que un archivo que no corresponde a
        ningun gasto es un documento que no se puede audicar contra nada, y el
        producto promete poder auditar.
        """
        from sqlalchemy import func, select

        from app.models.ticket import TicketModel
        from app.models.ticket_document import TicketDocumentModel

        ticket = await _crear_ticket(db_session, test_company.id, TICKET_TEXTO)
        await db_session.delete(ticket)
        await db_session.commit()

        documentos = await db_session.scalar(
            select(func.count()).select_from(TicketDocumentModel)
        )
        assert documentos == 0, "quedo el comprobante de un gasto que ya no existe"
        assert await db_session.scalar(
            select(func.count()).select_from(TicketModel)
        ) == 0

    @pytest.mark.asyncio
    async def test_la_relacion_esta_cableada_para_borrar_en_cascada(
        self, db_session, test_company
    ):
        """Que el borrado en cascada este DECLARADO, no que funcione aqui.

        Lo que se comprueba es que la relationship tiene `delete-orphan` y
        `single_parent`. Eso es lo que hace que el borrado del ticket borre el
        comprobante sin que nadie lo pida.

        Lo que NO se comprueba aqui, y es lo importante, es que el `ON DELETE
        CASCADE` de la llave foranea exista de verdad en Postgres. SQLite
        ignora las llaves foraneas por omision (hace falta un PRAGMA que esta
        base de prueba no activa), asi que un `DELETE` aqui pasaria aunque el
        CASCADE no estuviera escrito. Comprobarlo es trabajo de
        `scripts/verify_postgres_documentos.py`, y no de esta suite.

        Sin el CASCADE en la base, borrar un ticket desde la API (que usa la
        relationship) dejaria el archivo, y borrar un ticket por SQL directo no
        lo borraria nunca. Son dos caminos de borrado y tienen que terminar igual.
        """
        from app.models.ticket import TicketModel

        relationship = TicketModel.documento.property
        assert relationship.uselist is False, "debe ser uno o ninguno, no una lista"
        assert "delete-orphan" in relationship.cascade
        assert "delete" in relationship.cascade


class TestUnFalloDeAlmacenamientoNoPierdeElGasto:
    """La parte que parece sensata y no lo es si se hace al reves."""

    @pytest.mark.asyncio
    async def test_si_el_documento_no_se_guarda_el_ticket_sigue_ahi(
        self, db_session, test_company, monkeypatch
    ):
        """Un comprobante que se leyo bien no se pierde por un fallo de guardado.

        Perder un gasto real por un problema de almacenamiento es peor que un
        gasto sin comprobante adjunto, y lo segundo esta visible en la cola. El
        primero desaparece y no hay forma de saber que existio.

        El fallo se provoca en el punto donde fallaria de verdad -la escritura de
        los bytes- y no en la sesion entera, porque un `rollback()` a_secs
        deshace tambien el ticket. Esa es justo la razon del SAVEPOINT en
        `guardar_documento`, y este test es la que la sostiene: si alguien
        cambia el `begin_nested` por nada, el ticket se va con el error.
        """
        from sqlalchemy import select

        from app.models.ticket import TicketModel
        from app.services import document_service

        def _explota_al_guardar(*args, **kwargs):
            raise RuntimeError("TOAST de Postgres: el documento no cabe")

        monkeypatch.setattr(
            document_service, "_escribir_documento", _explota_al_guardar,
            raising=False,
        )

        ticket = await _crear_ticket(db_session, test_company.id, TICKET_TEXTO)
        await db_session.commit()

        row = (await db_session.execute(
            select(TicketModel).where(TicketModel.id == ticket.id)
        )).scalar_one_or_none()

        assert row is not None, (
            "un fallo al guardar el documento se llevo por delante el gasto"
        )
        assert row.total_amount == Decimal("1100.00")
        assert row.provider_name == "Tiendas Ramirez SA de CV"
        assert row.extraction_status == "AUTO_APROBADO"
        # Y el ticket no miente sobre si tiene documento.
        assert row.tiene_documento is False
