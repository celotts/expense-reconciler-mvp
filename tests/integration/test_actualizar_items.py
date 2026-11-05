"""Que una persona pueda escribir las LINEAS de un ticket de foto.

El bloqueo que estos tests cubren
--------------------------------

`PATCH /tickets/{id}` aceptaba proveedor, RFC, total, IVA, fecha y categoria, y
NO aceptaba `items`. Eso hacia imposible cerrar el circulo de inventario:

1. La ruta OCR llega con `items=NULL` (solo el LLM extrae lineas).
2. `registrar_compra` devuelve `None` cuando `items` es falsy.
3. La unica forma de que una persona aportara las lineas era escribirlas donde
   la API no las guardaba.

O sea: el papel estaba a la vista, el endpoint aceptaba correcciones, y aun asi
el inventario por foto no tenia entrada. No faltaban datos: faltaba por donde
meterlos.

Lo que estos tests fijan
-----------------------

- Que las lineas se guarden, y con los importes EXACTOS.
- Que al guardarlas se abra la compra, en `EN_REVISION` y nunca en `PROCESADO`.
- Que `items` ausente NO borre lo que ya estaba, y que `[]` si lo vacie. Sin esa
  distincion, cualquier correccion del encabezado borraria el detalle y
  dejaria el inventario descuadrado en silencio.
- Que un total con Linea que no cuadra siga siendo detectable: el `PATCH` no es
  un atajo para saltarse la aritmetica.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.enums import EstadoCompra, ExtractionStatus
from app.models.inventario import CompraModel
from app.models.ticket import TicketModel


async def _ticket(db_session, test_company, **kwargs):
    ticket = TicketModel(
        company_id=test_company.id,
        provider_name=kwargs.pop("provider_name", "Tienda"),
        total_amount=kwargs.pop("total_amount", Decimal("97.56")),
        tax_amount=kwargs.pop("tax_amount", Decimal("0.00")),
        expense_date=kwargs.pop("expense_date", date(2026, 9, 28)),
        raw_text="x",
        extraction_status=kwargs.pop("extraction_status", ExtractionStatus.PENDIENTE.value),
        confidence_source="ocr",
        **kwargs,
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)
    return ticket


class TestUnaPersonaPuedeEscribirLasLineas:

    @pytest.mark.asyncio
    async def test_las_lineas_se_guardan(self, async_client, db_session, test_company):
        ticket = await _ticket(db_session, test_company)

        r = await async_client.patch(
            f"/api/v1/tickets/{ticket.id}",
            json={
                "provider_name": "CARNE MART",
                "provider_tax_id": "OCO030116UR4",
                "total_amount": "97.56",
                "subtotal": "97.56",
                "tax_amount": "0.00",
                "expense_date": "2026-09-28",
                "items": [
                    {
                        "description": "MILANESA DE PECHU",
                        "quantity": "1.028",
                        "unit_price": "94.90",
                        "total": "97.56",
                    }
                ],
            },
        )

        assert r.status_code == 200
        await db_session.refresh(ticket)
        assert ticket.items is not None
        assert len(ticket.items) == 1
        assert ticket.items[0]["description"] == "MILANESA DE PECHU"

    @pytest.mark.asyncio
    async def test_el_importe_no_pierde_exactitud(
        self, async_client, db_session, test_company
    ):
        """`1.028` y `94.90` se guardan exactos, no redondeados ni como float.

        Una cantidad que se guarda como 1.03 en vez de 1.028 cambia el stock, y el
        stock es `SUM(movimientos_inventario)`: el error no se va, se acumula para
        siempre. Por eso el importe va como texto y no como float.
        """
        ticket = await _ticket(db_session, test_company)

        await async_client.patch(
            f"/api/v1/tickets/{ticket.id}",
            json={
                "provider_name": "X SA DE CV",
                "total_amount": "97.56",
                "expense_date": "2026-09-28",
                "items": [
                    {"description": "PECHU", "quantity": "1.028", "unit_price": "94.90", "total": "97.56"}
                ],
            },
        )

        await db_session.refresh(ticket)
        # El JSON guarda texto, y el texto tiene que ser el que se escribio.
        assert ticket.items[0]["quantity"] == "1.028"
        assert ticket.items[0]["unit_price"] == "94.90"

    @pytest.mark.asyncio
    async def test_al_escribir_lineas_se_abre_la_compra(
        self, async_client, db_session, test_company
    ):
        """Este es el test que muere si se quita la llamada a `registrar_compra`."""
        ticket = await _ticket(db_session, test_company)
        antes = await db_session.execute(
            select(CompraModel).where(CompraModel.ticket_id == ticket.id)
        )
        assert antes.scalar_one_or_none() is None

        await async_client.patch(
            f"/api/v1/tickets/{ticket.id}",
            json={
                "provider_name": "CARNE MART",
                "total_amount": "97.56",
                "expense_date": "2026-09-28",
                "items": [{"description": "PECHU", "quantity": "1", "total": "97.56"}],
            },
        )

        compra = await db_session.execute(
            select(CompraModel).where(CompraModel.ticket_id == ticket.id)
        )
        assert compra.scalar_one_or_none() is not None, (
            "escribir las lineas tiene que abrir la compra"
        )

    @pytest.mark.asyncio
    async def test_la_compra_nunca_nace_procesada(
        self, async_client, db_session, test_company
    ):
        """`EN_REVISION`, SIEMPRE. `PROCESADO` exige firma de una persona.

        Es la regla que evita que el inventario cuente algo sin que nadie lo haya
        autorizado: `ck_compras_confirmacion` lo prohibe en la base, y este test
        lo comprueba del lado de la aplicacion.
        """
        ticket = await _ticket(db_session, test_company)

        await async_client.patch(
            f"/api/v1/tickets/{ticket.id}",
            json={
                "provider_name": "X SA DE CV",
                "total_amount": "97.56",
                "expense_date": "2026-09-28",
                "items": [{"description": "PECHU", "quantity": "1", "total": "97.56"}],
            },
        )

        compra = await db_session.execute(
            select(CompraModel).where(CompraModel.ticket_id == ticket.id)
        )
        fila = compra.scalar_one_or_none()
        assert fila is not None
        assert fila.estado == EstadoCompra.EN_REVISION.value
        assert fila.confirmada_por is None


class TestLoQueNoSeBorra:

    @pytest.mark.asyncio
    async def test_no_mandar_items_no_borra_las_que_ya_hay(
        self, async_client, db_session, test_company
    ):
        """Corregir el encabezado NO puede dejar el ticket sin detalle.

        Es la distincion que hace `exclude_unset`: "no me digas nada de las lineas"
        y "dejame las lineas vacias" son dos peticiones distintas. Sin esto,
        cualquier correccion del RFC borraba el inventario del ticket.
        """
        ticket = await _ticket(db_session, test_company, items=[
            {"description": "PECHU", "quantity": "1", "total": "97.56"},
        ])

        await async_client.patch(
            f"/api/v1/tickets/{ticket.id}",
            json={"provider_name": "CARNE MART CORREGIDO"},
        )

        await db_session.refresh(ticket)
        assert ticket.items is not None, "las lineas existentes no se borran por corregir el nombre"
        assert ticket.provider_name == "CARNE MART CORREGIDO"

    @pytest.mark.asyncio
    async def test_una_lista_vacia_si_vacia_las_lineas(
        self, async_client, db_session, test_company
    ):
        """`[]` es una peticion explicita y si se cumple.

        Es la contraparte del test anterior: sin esto, no habria forma de quitar
        unas lineas que se leyeron mal.
        """
        ticket = await _ticket(db_session, test_company, items=[
            {"description": "PECHU", "quantity": "1", "total": "97.56"},
        ])

        await async_client.patch(
            f"/api/v1/tickets/{ticket.id}",
            json={
                "provider_name": "X SA DE CV",
                "total_amount": "97.56",
                "expense_date": "2026-09-28",
                "items": [],
            },
        )

        await db_session.refresh(ticket)
        assert ticket.items == []


class TestLoQueSigueSinPasar:

    @pytest.mark.asyncio
    async def test_un_total_que_no_cuadra_con_el_subtotal_no_se_aprueba(
        self, async_client, db_session, test_company
    ):
        """El `PATCH` no es un atajo para saltarse la aritmetica.

        Se manda `subtotal 97.56` con `total 234.00` y `IVA 0.00`: el ticket tiene
        que quedar en `REQUIERE_REVISION` con el motivo, no `APROBADO`. La regla 1
        del gate —"la confianza alta nunca compensa un check roto"— aplica igual
        cuando los datos los puso una persona.
        """
        ticket = await _ticket(db_session, test_company)

        await async_client.patch(
            f"/api/v1/tickets/{ticket.id}",
            json={
                "provider_name": "X SA DE CV",
                "total_amount": "234.00",
                "subtotal": "97.56",
                "tax_amount": "0.00",
                "expense_date": "2026-09-28",
                "items": [{"description": "PECHU", "quantity": "1", "total": "234.00"}],
            },
        )

        await db_session.refresh(ticket)
        assert ticket.extraction_status == ExtractionStatus.REQUIERE_REVISION.value
        assert "subtotal_plus_tax_mismatch" in (ticket.validation_errors or "")

    @pytest.mark.asyncio
    async def test_un_total_cero_no_se_acepta(
        self, async_client, db_session, test_company
    ):
        """`gt=0` en el schema: es el caso de `IMG_4222.jpeg`, el peor lectura.

        Un total de cero no es un gasto y admitiriamos un ticket que no describe
        nada, con compra y todo.
        """
        ticket = await _ticket(db_session, test_company)

        r = await async_client.patch(
            f"/api/v1/tickets/{ticket.id}",
            json={
                "provider_name": "X SA DE CV",
                "total_amount": "0.00",
                "expense_date": "2026-09-28",
                "items": [{"description": "X", "quantity": "1", "total": "0.00"}],
            },
        )

        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_un_rfc_inventado_no_se_acepta(
        self, async_client, db_session, test_company
    ):
        """El RFC tiene que estar en forma, no "parecido".

        Es la defensa que hacia imposible inventar las letras que el OCR perdio:
        `???030116UR4` es un RFC con la forma correcta y la empresa equivocada.
        """
        ticket = await _ticket(db_session, test_company)

        r = await async_client.patch(
            f"/api/v1/tickets/{ticket.id}",
            json={"provider_name": "X SA DE CV", "provider_tax_id": "???030116UR4"},
        )

        assert r.status_code == 422