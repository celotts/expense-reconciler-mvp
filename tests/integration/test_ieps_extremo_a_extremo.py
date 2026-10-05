"""El IEPS tiene que LLEGAR, no solo existir en el schema.

LOS TRES BUGS QUE ESTE ARCHIVO EXISTE PARA QUE NO VUELVAN
==========================================================

Este proyecto ya ha pagado tres veces el mismo error, y siempre con la misma
forma: un valor se calcula bien en un punto, existe en el modelo, y se pierde
antes de llegar a la tabla.

1. Las LINEAS. `ai_extractor.ExtractedInvoice.items` existia desde antes que el
   inventario, y `capture.invoice_to_result` no las copiaba al
   `TicketExtractionResult`. La linea desaparecia antes de llegar al gate.
2. El `Decimal` en `items`. `tickets.items` es JSON y `json.dumps` reventaba
   con `TypeError`, el INSERT moria y el comprobante se perdia entero.
3. El `ieps_amount` esta vez. El campo se agrego al gate, a la tabla y al
   export, y NO al `TicketExtractionResult`. Como esa clase vive en
   `parser_service.py` y no en `app/schemas/ticket.py`, donde uno la busca por
   el nombre, el error se manifesto como `AttributeError` en el escaner: tres
   capas mas abajo y sin ninguna pista de donde venia.

Los tres se ven con una sola linea. Por eso este archivo no prueba
una funcion: prueba la RECORRIDA, de la lectura cruda del modelo hasta el JSON
que ve el cliente, sin saltarse una capa. Un test por capa habria pasado en los
tres casos.

Y LA REGLA DE ESTOS TESTS
=========================

Ningun test de este archivo construye un `TicketExtractionResult` a mano para
probar el gate. El gate ya tiene sus tests en `test_impuestos.py`. Aqui lo que
se comprueba es que el DATO LLEGA, que es lo que estuvo roto tres veces.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

# El ticket de supermercado real de esta maquina. No inventado: el desvio que
# motivo todo el cambio son estos numeros.
#
#     SUBTOTAL       217.27
#     IVA 16.0%        8.14     <- solo una parte de las partidas esta a 16%
#     IEPS 8.0%        8.59     <- alimentos preparados y bebidas
#     TOTAL          234.00
#
# Y el detalle que hace que un heuristico no serviria: 8.59 sobre 217.27 es
# 4.0%, no 8%. El impuesto se aplica por PARTIDA y aqui hubo lineas a 0%, a 16%
# y con IEPS. Por eso la unica forma de comprobarlo es sumar los numeros que
# imprime el papel, no buscar una tasa que explique la diferencia.
SUBTOTAL = Decimal("217.27")
IVA = Decimal("8.14")
IEPS = Decimal("8.59")
TOTAL = Decimal("234.00")


def _invoice_del_modelo(**cambios):
    """Lo que devuelve el LLM, con los numeros del ticket real.

    Se construye el `ExtractedInvoice` del modulo del extractor y no un
    `TicketExtractionResult`: el objetivo es que la conversion de una capa a
    otra sea parte del test.
    """
    from app.services.ai_extractor import ExtractedInvoice

    datos = dict(
        provider_name="NUEVA WAL MART DE MEXICO S DE RL DE CV",
        provider_tax_id="WAL910101XXX",
        invoice_date=date(2026, 9, 23),
        subtotal=SUBTOTAL,
        tax_amount=IVA,
        ieps_amount=IEPS,
        total=TOTAL,
        confidence=0.95,
    )
    datos.update(cambios)
    return ExtractedInvoice(**datos)


class TestElIepsLlegaDesdeLaLectura:

    @pytest.mark.asyncio
    async def test_la_lectura_del_modelo_lleva_el_ieps_al_gate(
        self, db_session, test_company
    ):
        """`invoice_to_result` copia el campo. Si no, no hay nada que comprobar.

        Es el paso donde se perdio el campo la primera vez, y donde se perderia
        de nuevo: `ExtractedInvoice` (el modelo) y `TicketExtractionResult` (lo
        que consume el gate) son dos clases distintas, en dos archivos distintos.
        """
        from app.services.capture import invoice_to_result

        resultado = invoice_to_result(_invoice_del_modelo())

        assert resultado.ieps_amount == IEPS

    @pytest.mark.asyncio
    async def test_un_comprobante_con_iva_e_ieps_se_guarda_como_resuelto(
        self, db_session, test_company
    ):
        """El caso que el gate RECHAZABA antes de este cambio.

        `217.27 + 8.14 = 225.41` contra un total de `234.00`: con la tolerancia
        de un centimo, `subtotal_plus_tax_mismatch` era seguro. El comprobante
        era correcto y el gate lo rechazaba, que es peor que un check flojo:
        hace que `subtotal_plus_tax_mismatch` deje de significar "leiste mal".
        """
        from app.api.tickets import _persist_extracted
        from app.core.enums import SourceType
        from app.services.capture import invoice_to_result

        ticket = await _persist_extracted(
            db_session, test_company.id,
            invoice_to_result(_invoice_del_modelo()),
            b"%PDF-1.4 contenido del comprobante",
            SourceType.PDF,
            source_file="walmart_ieps.pdf",
        )

        assert ticket.extraction_status in ("AUTO_APROBADO", "APROBADO"), (
            f"un comprobante con IVA e IEPS bien leidos no puede ir a revision: "
            f"{ticket.validation_errors}"
        )
        assert "subtotal_plus_tax_mismatch" not in (ticket.validation_errors or "")

    @pytest.mark.asyncio
    async def test_el_ieps_queda_en_la_fila_no_solo_en_el_veredicto(
        self, db_session, test_company
    ):
        """La columna existe, y esto es lo que prueba que se LLENO.

        Una columna que se crea y nunca se escribe es el sintoma de todos los
        bugs anteriores: el gate approves el ticket, la respuesta trae el
        veredicto, y el numero no esta en ningun sitio. Sin esta comprobacion,
        "el gate lo acepto" parece suficiente y no lo es.
        """
        from sqlalchemy import select

        from app.api.tickets import _persist_extracted
        from app.core.enums import SourceType
        from app.models.ticket import TicketModel
        from app.services.capture import invoice_to_result

        await _persist_extracted(
            db_session, test_company.id,
            invoice_to_result(_invoice_del_modelo()),
            b"%PDF-1.4 contenido del comprobante",
            SourceType.PDF,
            source_file="walmart_ieps.pdf",
        )

        fila = (
            await db_session.execute(
                select(TicketModel).where(TicketModel.source_file == "walmart_ieps.pdf")
            )
        ).scalar_one()

        assert fila.ieps_amount == IEPS
        # Y los otros tres siguen intactos: agregar un campo no puede corronper
        # los que ya estaban.
        assert fila.subtotal == SUBTOTAL
        assert fila.tax_amount == IVA, (
            "tax_amount es el IVA. Si aqui metiera IVA+IEPS, el contador recibe "
            "un IVA que el comprobante no tiene: ver export_service."
        )
        assert fila.total_amount == TOTAL

    @pytest.mark.asyncio
    async def test_sin_ieps_el_ticket_no_lo_inventa(
        self, db_session, test_company
    ):
        """Un comprobante SIN IEPS guarda `NULL`, y no `0.00`.

        La diferencia no es academica: es la que usa el gate. Con `0.00` el
        gate probaria `subtotal + IVA + 0 == total`, que es el mismo check de
        siempre pero por el camino equivocado, y el `0.00` persisted diria
        "lei el IEPS y era cero" sobre un papel que no imprime IEPS.
        """
        from sqlalchemy import select

        from app.api.tickets import _persist_extracted
        from app.core.enums import SourceType
        from app.models.ticket import TicketModel
        from app.services.capture import invoice_to_result

        # El mismo ticket, pero sin IEPS: el papel de una ferreteria.
        ticket = await _persist_extracted(
            db_session, test_company.id,
            invoice_to_result(_invoice_del_modelo(
                provider_name="FERRETERIA DEL SUR SA DE CV",
                provider_tax_id="FDE960117QA9",
                subtotal=Decimal("1000.00"),
                tax_amount=Decimal("160.00"),
                ieps_amount=None,
                total=Decimal("1160.00"),
            )),
            b"%PDF-1.4 ferreteria",
            SourceType.PDF,
            source_file="ferreteria.pdf",
        )

        fila = (
            await db_session.execute(
                select(TicketModel).where(TicketModel.source_file == "ferreteria.pdf")
            )
        ).scalar_one()

        assert fila.ieps_amount is None
        assert ticket.extraction_status in ("AUTO_APROBADO", "APROBADO")


class TestElIepsSalePorLaApi:

    @pytest.mark.asyncio
    async def test_el_ticket_responde_con_el_ieps(
        self, db_session, test_company
    ):
        """`TicketResponse` lo declara, y por eso el JSON lo trae.

        Sin esto, quien revisa el ticket ve `Subtotal 217.27 / IVA 8.14 /
        Total 234.00` y no tiene donde poner los 8.59 que sobran. El descuadre
        se ve pero no se explica, y eso es peor que un dato de mas.
        """
        from app.api.tickets import _persist_extracted
        from app.core.enums import SourceType
        from app.schemas.ticket import TicketResponse
        from app.services.capture import invoice_to_result

        ticket = await _persist_extracted(
            db_session, test_company.id,
            invoice_to_result(_invoice_del_modelo()),
            b"%PDF-1.4 contenido del comprobante",
            SourceType.PDF,
            source_file="walmart_ieps.pdf",
        )

        respuesta = TicketResponse.model_validate(ticket)

        assert respuesta.ieps_amount == IEPS
        # Y el de `datos` del escaner, que es un schema distinto y por eso es
        # un segundo chances de que el campo se pierda.
        from app.schemas.scan import DatosTicketResponse

        assert DatosTicketResponse.model_validate(ticket).ieps_amount == IEPS


class TestCorregirYVolverAComprobar:

    @pytest.mark.asyncio
    async def test_aprobar_un_ticket_con_ieps_no_pide_que_lo_inventes(
        self, async_client, db_session, test_company
    ):
        """El ciclo completo: corregir el IEPS y aprobar, sin romper la aritmetica.

        Este es el caso humano. El OCR leyo `IVA 0.00` y el total `234.00`
        (medido: ticket `FC3CB9F8-5AB1-4546-B9A9-016C69A158C5`), asi que el
        `subtotal_plus_tax_mismatch` que sale es real y no se puede resolver
        inventando un `tax_amount`. La persona mira el papel, escribe `8.14` y
        `8.59`, y el ticket tiene que aprobarse.

        Si `PATCH` guardara el campo pero la aprobacion no lo leyera, el
        `422` seria permanente y el ticket se quedaria en la cola para siempre,
        sin ninguna explicacion util.
        """
        from sqlalchemy import select

        from app.api.tickets import _persist_extracted
        from app.core.enums import SourceType
        from app.models.ticket import TicketModel
        from app.services.capture import invoice_to_result

        ticket = await _persist_extracted(
            db_session, test_company.id,
            invoice_to_result(_invoice_del_modelo(
                tax_amount=Decimal("0.00"), ieps_amount=None
            )),
            b"%PDF-1.4 contenido del comprobante",
            SourceType.PDF,
            source_file="walmart_ieps.pdf",
        )
        await db_session.commit()

        # 1. La persona corrige el IVA y el IEPS mirando el papel. El PATCH
        #    guarda el campo por el `setattr` generico, asi que esto ya funciona;
        #    el paso 2 es el que se rompia.
        r = await async_client.patch(
            f"/api/v1/tickets/{ticket.id}",
            json={"tax_amount": "8.14", "ieps_amount": "8.59"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["ieps_amount"] == "8.59"

        # 2. Y lo aprueba DESDE LA COLA, que es por donde una persona corrige de
        # verdad. Este es el paso que se rompia, y por dos razones: el schema de
        # la revision (`TicketReviewRequest`) no aceptaba `ieps_amount`, y la
        # aprobacion re-corre el gate sin leerlo.
        r = await async_client.patch(
            f"/api/v1/tickets/{ticket.id}/review",
            json={
                "action": "approve",
                "tax_amount": "8.14",
                "ieps_amount": "8.59",
            },
        )
        assert r.status_code == 200, (
            f"aprobar un comprobante con IVA e IEPS correctos no puede fallar: {r.text}"
        )
        assert r.json()["extraction_status"] == "APROBADO"

        fila = (
            await db_session.execute(
                select(TicketModel).where(TicketModel.id == ticket.id)
            )
        ).scalar_one()
        assert fila.extraction_status == "APROBADO"
        assert fila.validation_errors is None
