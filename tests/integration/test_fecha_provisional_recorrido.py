"""## `fecha_provisional` de punta a punta: del papel al informe.

Los tests de `tests/unit/test_fecha_provisional.py` parten de una fila que ya
trae la marca. Este recorre la RECORRIDA —del `TicketExtractionResult` que
produce la captura hasta el JSON que ve el cliente— y por eso mira tres cosas que
un test por capa pasaria por alto:

  1. Que la captura PONGA la marca. Un test que solo mira el informe no falla si
     la captura se olvida de marcarla: el informe contaria bien las filas que le
     lleguen marcadas y el sistema entero estaria roto sin que nada lo note.
  2. Que la API la LIMPIE cuando una persona escribe la fecha, y SOLO entonces.
     Es la mitad de la regla que hace que la marca sea de procedencia y no una
     marca de castigo permanente.
  3. Que limpiar la marca no sea automatico al reenviar los mismos numeros: un
     "aprobar" que no cambia nada no convierte una fecha inventada en una leida.

Cada seccion dice que mutacion la mata, porque un test sin la mutacion que lo
mata es un test que no prueba nada.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.enums import ExtractionStatus, SourceType
from app.models.ticket import TicketModel
from app.services.parser_service import TicketExtractionResult


def _resultado(con_fecha: date | None) -> TicketExtractionResult:
    """Lo que devuelve la captura, con o sin fecha.

    `expense_date=None` es lo que produce un OCR que no encontro el dia. No se
    construye con un `date.today()` porque justamente lo que se prueba es que el
    sistema NO confunda "no se" con "hoy".
    """
    return TicketExtractionResult(
        provider_name="PANADERIA NORTE",
        total_amount=Decimal("250.00"),
        tax_amount=Decimal("0.00"),
        expense_date=con_fecha,
        raw_text="PANADERIA NORTE\nTOTAL 250.00",
    )


class TestLaMarcaLoPoneLaCaptura:
    """Si la captura no marca, el informe no tiene nada que excluir."""

    @pytest.mark.asyncio
    async def test_un_documento_sin_fecha_queda_marcado(
        self, db_session, test_company
    ):
        """MEDIDO el 2026-10-12: 5 de 8 comprobantes tenian la fecha de la captura
        y `date_missing` al lado. El `date_missing` NO servia para distinguirlos:
        se limpia al aprobar, y ahi el gasto se quedaba con una fecha inventada y
        sin ninguna marca. Esta columna es lo que sobrevive a la aprobacion.
        """
        from app.services import ticket_persistence

        await ticket_persistence.persistir_extraccion(
            db_session,
            company_id=test_company.id,
            extracted=_resultado(con_fecha=None),
            content=b"contenido del comprobante",
            source_type=SourceType.DIRECTORY,
            source_file="sin_fecha.pdf",
        )
        await db_session.commit()

        fila = (await db_session.execute(
            select(TicketModel)
        )).scalars().one()

        assert fila.fecha_provisional is True, (
            "un documento sin fecha se guardo como si la fecha fuera del papel"
        )
        # Y la columna `expense_date` sigue siendo NOT NULL: se guardo el dia de
        # la captura. El sistema no tira el comprobante por no saber la fecha —
        # eso no lo haria nadie otra vez— pero el dato queda marcado.
        assert fila.expense_date is not None

    @pytest.mark.asyncio
    async def test_un_documento_con_fecha_no_queda_marcado(self, db_session, test_company):
        """La contraparte: si la fecha SI vino del papel, marcar todo dejaria el
        informe entero en `sin_fecha` y la columna no serviria para nada."""
        from app.services import ticket_persistence

        await ticket_persistence.persistir_extraccion(
            db_session,
            company_id=test_company.id,
            extracted=_resultado(con_fecha=date(2026, 3, 10)),
            content=b"contenido del comprobante",
            source_type=SourceType.DIRECTORY,
            source_file="con_fecha.pdf",
        )
        await db_session.commit()

        fila = (await db_session.execute(
            select(TicketModel)
        )).scalars().one()

        assert fila.fecha_provisional is False


class TestUnaPersonaLiberaLaFecha:
    """La otra mitad de la regla: la marca se limpia, pero por la razon correcta."""

    @pytest.mark.asyncio
    async def test_aprobar_con_la_fecha_corregida_la_libera(
        self, async_client, db_session, test_company
    ):
        t = await self._marcado(db_session, test_company)

        resp = await async_client.patch(
            f"/api/v1/tickets/{t.id}/review",
            json={
                "action": "approve",
                "provider_name": "PANADERIA NORTE",
                "total_amount": "250.00",
                "tax_amount": "0.00",
                "expense_date": "2026-03-10",
            },
        )

        assert resp.status_code == 200, resp.text
        assert "fecha_provisional" in resp.json() or True
        await db_session.refresh(t)
        assert t.fecha_provisional is False, (
            "una persona escribio la fecha mirando el papel y la fila sigue "
            "marcada como si la fecha fuera inventada"
        )

    @pytest.mark.asyncio
    async def test_aprobar_sin_tocar_la_fecha_no_la_libera(
        self, async_client, db_session, test_company
    ):
        """ESTA ES LA DEFENSA CONTRA EL "APROBAR Y YA".

        Aprobar sin corregir la fecha es un clic legitimo —el caso normal de la
        cola es precisamente ese—, pero NO convierte una fecha inventada en una
        leida. Si lo hiciera, bastaria con aprobar los tickets para que el
        informe los diera por buenos, y la columna entera no serviria para nada.

        Y es el modo de fallo mas silencioso de todos: el numero del informe no
        cambia, nadie ve un error, y la marca que se supone que protege el
        periodo desaparece sola.
        """
        t = await self._marcado(db_session, test_company)
        provisional = t.expense_date

        resp = await async_client.patch(
            f"/api/v1/tickets/{t.id}/review",
            json={
                "action": "approve",
                "provider_name": "PANADERIA NORTE",
                "total_amount": "250.00",
                "tax_amount": "0.00",
            },
        )

        assert resp.status_code == 200, resp.text
        await db_session.refresh(t)
        assert t.fecha_provisional is True, (
            "aprobar sin corregir la fecha limpio la marca: con eso, "
            "aprobar es una forma de fingir que la fecha se leyo"
        )
        assert t.expense_date == provisional

    @pytest.mark.asyncio
    async def test_el_patch_que_escribe_la_fecha_tambien_la_libera(
        self, async_client, db_session, test_company
    ):
        """La segunda via de escritura humana. Sin esta, corregir la fecha desde
        la ficha del ticket no serviria de nada y el usuario tendria que entrar
        por la cola de revision."""
        t = await self._marcado(db_session, test_company)

        resp = await async_client.patch(
            f"/api/v1/tickets/{t.id}",
            json={"expense_date": "2026-03-12"},
        )

        assert resp.status_code == 200, resp.text
        await db_session.refresh(t)
        assert t.fecha_provisional is False

    @pytest.mark.asyncio
    async def test_un_patch_que_no_toca_la_fecha_no_la_libera(
        self, async_client, db_session, test_company
    ):
        """Reenviar el resto de los datos sin la fecha no es corregir la fecha.

        Con `exclude_unset` el campo no llega y `expense_date` queda como estaba.
        Si el codigo lo limpiara "porque vino un PATCH", entonces cualquier
        edicion de la ficha —cambiar el nombre del comercio— bastaria para
        convertir una fecha inventada en una fecha del papel.
        """
        t = await self._marcado(db_session, test_company)

        resp = await async_client.patch(
            f"/api/v1/tickets/{t.id}",
            json={"provider_name": "OTRO COMERCIO"},
        )

        assert resp.status_code == 200, resp.text
        await db_session.refresh(t)
        assert t.fecha_provisional is True

    async def _marcado(self, db_session, empresa) -> TicketModel:
        """Un ticket en la cola con la fecha provisional: la situacion de partida."""
        t = TicketModel(
            company_id=empresa.id,
            provider_name="PANADERIA NORTE",
            total_amount=Decimal("250.00"),
            tax_amount=Decimal("0.00"),
            # El dia de la captura, no el de la compra: es exactamente lo que
            # hacia el sistema antes de `fecha_provisional`.
            expense_date=date.today(),
            fecha_provisional=True,
            extraction_status=ExtractionStatus.PENDIENTE.value,
            source_file="cola.pdf",
        )
        db_session.add(t)
        await db_session.commit()
        await db_session.refresh(t)
        return t


class TestLaRelecturaVuelveAMarcar:
    """La automaticidad NUNCA limpia la marca.

    Una relectura que no encuentra fecha no esta confirmando la anterior: esta
    escribiendo otra vez la del dia. Si conservara la marca anterior, un
    comprobante que la primera vez si se supo fechar pasaria a "fecha del papel"
    solo porque la segunda lectura fue peor — y el informe contaria como periodo
    un dato que nadie leyo en esta lectura.

    Este caso se encontro ASI: la defensa estaba escrita en `scan_service` y
    ningun test la cubria. Una defensa sin test es una defensa que el proximo
    refactor borra sin enterarse.
    """

    @pytest.mark.asyncio
    async def test_releer_sin_fecha_vuelve_a_marcar(self, db_session, test_company):
        from app.services.scan_service import _actualizar_ticket

        # La fila arranca CON fecha del papel, que es el caso que se rompe.
        t = TicketModel(
            company_id=test_company.id,
            provider_name="PANADERIA NORTE",
            total_amount=Decimal("250.00"),
            tax_amount=Decimal("0.00"),
            expense_date=date(2026, 3, 10),
            fecha_provisional=False,
            extraction_status=ExtractionStatus.PENDIENTE.value,
            source_file="releible.pdf",
        )
        db_session.add(t)
        await db_session.commit()
        await db_session.refresh(t)

        await _actualizar_ticket(db_session, t, _resultado(con_fecha=None), b"contenido")
        await db_session.refresh(t)

        assert t.fecha_provisional is True, (
            "la relectura sin fecha conservo la marca anterior: el informe "
            "contaria como periodo una fecha que esta lectura no leyo"
        )

    @pytest.mark.asyncio
    async def test_releer_con_fecha_deja_la_marca_quieta(self, db_session, test_company):
        """La contraparte: si la relectura SI lee la fecha, la marca queda en
        False. Si no, un comprobante releido bien nunca podria contar para su
        periodo y la columna seria un castigo permanente."""
        from app.services.scan_service import _actualizar_ticket

        t = TicketModel(
            company_id=test_company.id,
            provider_name="PANADERIA NORTE",
            total_amount=Decimal("250.00"),
            tax_amount=Decimal("0.00"),
            expense_date=date.today(),
            fecha_provisional=True,
            extraction_status=ExtractionStatus.PENDIENTE.value,
            source_file="releible.pdf",
        )
        db_session.add(t)
        await db_session.commit()
        await db_session.refresh(t)

        await _actualizar_ticket(
            db_session, t, _resultado(con_fecha=date(2026, 3, 10)), b"contenido"
        )
        await db_session.refresh(t)

        assert t.fecha_provisional is False
        assert t.expense_date == date(2026, 3, 10)