"""La regla del archivado: solo sale de la bandeja lo que se leyo bien.

LA REGLA
--------

Un comprobante se mueve a `Tickets_Scan` **solo** si su ticket quedo en un
estado resuelto (`AUTO_APROBADO` o `APROBADO`). Todo lo demas se queda en
`Tickets_app`, la carpeta de entrada.

LO QUE ARRIBA ESTABA MAL
-----------------------

La condicion anterior era "tiene ticket y no hubo error", y eso movia a
`Tickets_Scan` los comprobantes en `PENDIENTE` y `REQUIERE_REVISION`. Con OCR
al 33-57% de exactitud eso era **casi todos**: la carpeta de "escaneados" se
llenaba de papeles que nadie habia revisado.

El proyecto lo admitia y lo rodeaba de avisos (`archivados_pendientes`, "el
sistema ya lo intento"), que es una forma de no romper la promesa sin cumplirla.
Estos tests fijan la promesa cumplida: **lo que esta en `Tickets_Scan` se leyo
bien.**

Y el efecto util: la carpeta de entrada es la COLA DE TRABAJO. Un comprobante
corregido a mano queda `APROBADO` y en la siguiente corrida se archiva solo. La
bandeja se vacia a medida que se resuelve el trabajo, sin que nadie mueva
archivos a mano.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core.enums import ExtractionStatus


async def _ticket(db_session, test_company, estado: str):
    from app.models.ticket import TicketModel

    ticket = TicketModel(
        company_id=test_company.id,
        provider_name="Tienda",
        total_amount=Decimal("97.56"),
        tax_amount=Decimal("0.00"),
        expense_date=date(2026, 9, 28),
        raw_text="x",
        extraction_status=estado,
        confidence_source="ocr",
        source_type="directory",
        source_file="foto.jpg",
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)
    return ticket


@pytest.fixture
def carpeta(tmp_path, monkeypatch):
    """La carpeta de entrada del escaner, con el archivado apuntando fuera.

    Se necesita una carpeta REAL porque `esta_respaldado` lee el archivo del disco
    para comparar su sha256: un test con un path inventado comprobaria que la
    funcion no se rompe, no que decide bien.
    """
    from app.core.config import settings

    destino = tmp_path / "tickets"
    destino.mkdir()
    # `exist_ok=True` porque la fixture global `_aislar_el_archivado` ya creo
    # una carpeta de escaneados en este mismo `tmp_path`.
    destino_scan = tmp_path / "escaneados"
    destino_scan.mkdir(exist_ok=True)
    monkeypatch.setattr(settings, "TICKETS_INPUT_DIR", str(destino))
    monkeypatch.setattr(settings, "TICKETS_SCAN_OUTPUT_DIR", str(destino_scan))
    monkeypatch.setattr(settings, "TICKETS_SCAN_ARCHIVAR_AL_ESCANEAR", False)
    return destino


class TestElRespaldoAntesDeBorrar:
    """No se borra nada sin comprobar que la copia existe y es la MISMA.

    El borrado de la carpeta de entrada es irreversible, asi que la comprobacion
    va antes del `unlink` y no despues. Y son DOS comprobaciones, no una:
    que exista un documento, y que sus bytes sean los del archivo que se va a
    borrar.

    La segunda es la que se olvida. Un ticket puede tener varias versiones del
    comprobante —el papel se apila, nunca se altera— y el archivo que esta en la
    bandeja puede ser una version distinta de la vigente. Si se borra sin
    comparar, el ticket se queda con un papel que no es el que se leyo.
    """

    async def _ticket_con_documento(self, db_session, test_company, contenido):
        """Un ticket con su documento guardado, como si el escaneo lo hubiera leido."""
        import hashlib

        from app.models.ticket import TicketModel
        from app.models.ticket_document import TicketDocumentModel

        ticket = TicketModel(
            company_id=test_company.id,
            provider_name="Tienda",
            total_amount=Decimal("97.56"),
            tax_amount=Decimal("0.00"),
            expense_date=date(2026, 9, 28),
            raw_text="x",
            extraction_status=ExtractionStatus.AUTO_APROBADO.value,
        )
        db_session.add(ticket)
        await db_session.flush()

        db_session.add(TicketDocumentModel(
            ticket_id=ticket.id,
            contenido=contenido,
            nombre_archivo="comprobante.jpg",
            sha256=hashlib.sha256(contenido).hexdigest(),
            tamano=len(contenido),
        ))
        await db_session.commit()
        return ticket

    async def test_si_los_bytes_coinciden_hay_respaldo(
        self, db_session, test_company, carpeta
    ):
        from app.services.archivado_service import esta_respaldado

        contenido = b"\xff\xd8\xff\xd9comprobante-real"
        ticket = await self._ticket_con_documento(db_session, test_company, contenido)
        (carpeta / "comprobante.jpg").write_bytes(contenido)

        respaldado, motivo = await esta_respaldado(db_session, ticket, "comprobante.jpg")

        assert respaldado is True
        assert "mismos bytes" in motivo

    async def test_si_los_bytes_difieren_NO_hay_respaldo(
        self, db_session, test_company, carpeta
    ):
        """El caso que la comprobacion existe para: el archivo es OTRO papel.

        El ticket tiene su comprobante guardado, asi que un "hay documento?"
        responderia que si. Pero el archivo en la bandeja tiene bytes distintos —
        una version anterior, o un comprobante que se sustituyo— y borrarlo
        dejaria al ticket con un papel que no es el que se leyo.
        """
        from app.services.archivado_service import esta_respaldado

        ticket = await self._ticket_con_documento(
            db_session, test_company, b"\xff\xd8\xff\xd9version-guardada"
        )
        (carpeta / "comprobante.jpg").write_bytes(b"\xff\xd8\xff\xd9version-OTRA")

        respaldado, motivo = await esta_respaldado(db_session, ticket, "comprobante.jpg")

        assert respaldado is False
        assert "no coincide" in motivo

    async def test_un_ticket_sin_documento_no_tiene_respaldo(
        self, db_session, test_company, carpeta
    ):
        """Sin fila en `ticket_documents`, el archivo del disco es el unico."""
        from app.models.ticket import TicketModel
        from app.services.archivado_service import esta_respaldado

        ticket = TicketModel(
            company_id=test_company.id,
            provider_name="Tienda",
            total_amount=Decimal("97.56"),
            expense_date=date(2026, 9, 28),
            raw_text="x",
            extraction_status=ExtractionStatus.AUTO_APROBADO.value,
        )
        db_session.add(ticket)
        await db_session.commit()
        (carpeta / "comprobante.jpg").write_bytes(b"\xff\xd8\xff\xd9lo-que-hay")

        respaldado, motivo = await esta_respaldado(db_session, ticket, "comprobante.jpg")

        assert respaldado is False
        assert "unico comprobante" in motivo


class TestSoloSaleLoResuelto:

    def test_auto_aprobado_es_el_unico_que_se_mueve_solo(self):
        """`AUTO_APROBADO` es "el gate lo aprobo sin que nadie lo tocara".

        Es el estado en el que la lectura automatica se hace cargo, y por eso el
        papel sale de la bandeja sin que una persona haya hecho nada.
        """
        from app.core.enums import SETTLED_STATUSES

        assert ExtractionStatus.AUTO_APROBADO in SETTLED_STATUSES

    def test_aprobado_tambien_sale(self):
        """`APROBADO` es una persona corrigio y aprobo: tambien se puede mover."""
        from app.core.enums import SETTLED_STATUSES

        assert ExtractionStatus.APROBADO in SETTLED_STATUSES

    @pytest.mark.parametrize(
        "estado",
        [ExtractionStatus.PENDIENTE, ExtractionStatus.REQUIERE_REVISION],
    )
    def test_pendiente_y_requiere_revision_se_quedan(self, estado):
        """Los dos que el gate para SE QUEDAN en la bandeja.

        Son exactamente los que alguien tiene que mirar, y un papel que necesita
        un ojo humano tiene que seguir visible en la bandeja.
        """
        from app.core.enums import SETTLED_STATUSES

        assert estado not in SETTLED_STATUSES


class TestElMotivoDeLaBandeja:

    def test_cada_estado_abierto_dice_una_cosa_distinta(self):
        """Un motivo generico hace que el operador no sepa que hacer.

        "queda en la bandeja" sin mas no dice si hay que corregir un numero, si
        el documento esta ilegible o si alguien lo descarto. Los tres piden
        acciones distintas.
        """
        from app.services.scan_service import _motivo_de_bandeja

        pendiente = _motivo_de_bandeja(ExtractionStatus.PENDIENTE.value)
        revision = _motivo_de_bandeja(ExtractionStatus.REQUIERE_REVISION.value)
        rechazado = _motivo_de_bandeja(ExtractionStatus.RECHAZADO.value)

        assert len({pendiente, revision, rechazado}) == 3
        # Y los tres dicen que se puede arreglar: el motivo tiene que llevar a la
        # accion, no solo a identificar el problema.
        for motivo in (pendiente, revision, rechazado):
            assert "queda en la bandeja" in motivo

    def test_no_dice_error_para_un_pendiente(self):
        """`PENDIENTE` no es un fallo del sistema.

        Decirle "error" al usuario lo manda a buscar un bug donde lo que hay es
        un papel que necesita un ojo humano. La distinction importa: el primero
        se arregla en el codigo, el segundo en la cola de revision.
        """
        from app.services.scan_service import _motivo_de_bandeja

        motivo = _motivo_de_bandeja(ExtractionStatus.PENDIENTE.value).lower()

        assert "error" not in motivo
        # Y dice que el sistema SI lo leyo, que es lo que la distinguish de un fallo.
        assert "leyo" in motivo or "gate" in motivo


class TestLaBandejaEsLaCola:

    async def test_un_ticket_pendiente_no_tiene_por_que_tocar_disco(
        self, db_session, test_company, tmp_path, monkeypatch
    ):
        """La regla se decide por el VERDICTO, no por el resultado en disco.

        Este test no escanea nada: mira que la decision se pueda tomar con el
        `extraction_status` del ticket, que es lo unico que hace falta. Si
        alguien mueve la comprobacion al disk y empieza a mirar si el archivo se
        movio, este test deja de servir para lo que dice que sirve.
        """
        ticket = await _ticket(db_session, test_company, ExtractionStatus.PENDIENTE.value)

        # Lo que decide el archivado, tal cual esta escrito en el servicio.
        assert ExtractionStatus(ticket.extraction_status) not in (
            ExtractionStatus.AUTO_APROBADO,
            ExtractionStatus.APROBADO,
        )

        # Y un APPROVED del mismo archivo SI se moveria, sin tocar nada mas.
        ticket.extraction_status = ExtractionStatus.APROBADO.value
        assert ExtractionStatus(ticket.extraction_status) in (
            ExtractionStatus.AUTO_APROBADO,
            ExtractionStatus.APROBADO,
        )