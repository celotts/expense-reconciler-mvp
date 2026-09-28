"""Tests de integracion de la cola de revision y del gate en la API.

Estos tests cubren el tramo de mayor riesgo del sistema: la salida de la IA
convirtiendose en filas de la base de datos. Si el gate deja pasar basura, el
error no aparece en este archivo: aparece tres meses despues en un reporte
financiero, y para entonces ya no se sabe de donde vino la cifra.

Por eso se prueba el comportamiento observable (que estado queda, que se ve
en la cola) y no solo que el endpoint responda 200.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.core.enums import SourceType
from app.services.parser_service import TicketExtractionResult


def _extraccion(
    provider_name="TIENDAS RAMIREZ",
    total="1160.00",
    tax="160.00",
    confidence=None,
    subtotal=None,
    rfc="TRAM910101XXX",
    fecha=None,
):
    """Fabricar una extraccion sin pasar por la IA."""
    return TicketExtractionResult(
        provider_name=provider_name,
        provider_tax_id=rfc,
        total_amount=Decimal(total),
        tax_amount=Decimal(tax),
        expense_date=fecha or date.today(),
        raw_text="texto del comprobante",
        confidence=confidence,
        subtotal=Decimal(subtotal) if subtotal is not None else None,
    )


async def _persistir(db_session, company_id, content, **kwargs):
    """Invoca la misma funcion que usa /extract-and-create."""
    from app.api.tickets import _persist_extracted

    return await _persist_extracted(
        db_session,
        company_id,
        _extraccion(**kwargs),
        content,
        SourceType.IMAGE,
        source_file="ticket.jpg",
    )


async def _trio_de_prueba(db_session, company_id):
    """Un pendiente, uno que requiere revision y uno ya cerrado.

    Los tres estados conviven en la misma empresa a proposito: la cola solo
    sirve si distingue los tres.
    """
    return [
        await _persistir(
            db_session, company_id, b"cola-1",
            provider_name="Unknown Provider", total="0.00", tax="0.00", confidence=0.4,
        ),
        await _persistir(db_session, company_id, b"cola-2", confidence=0.70),
        await _persistir(db_session, company_id, b"cola-3", confidence=0.99, subtotal="1000.00"),
    ]


class TestCapturaManualPasaPorElGate:
    @pytest.mark.asyncio
    async def test_captura_manual_queda_aprobada(self, async_client, test_company):
        resp = await async_client.post(
            "/api/v1/tickets/",
            json={
                "company_id": str(test_company.id),
                "provider_name": "WALMART",
                "total_amount": "249.86",
                "tax_amount": "34.46",
                "expense_date": "2025-01-15",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["extraction_status"] == "APROBADO"
        assert data["source_type"] == "manual"
        assert data["confidence_source"] == "manual"
        assert data["validation_errors"] is None

    @pytest.mark.asyncio
    async def test_captura_manual_no_declara_confianza(self, async_client, test_company):
        # Poner confidence=0.0 seria medir de mas: contaminaria el promedio
        # de confianza de la IA con datos que no salieron de la IA, y ese
        # promedio es la metrica que respalda el objetivo de exactitud.
        resp = await async_client.post(
            "/api/v1/tickets/",
            json={
                "company_id": str(test_company.id),
                "provider_name": "WALMART",
                "total_amount": "249.86",
                "expense_date": "2025-01-15",
            },
        )
        assert resp.json()["confidence"] is None

    @pytest.mark.asyncio
    async def test_captura_manual_invalida_se_rechaza(self, async_client, test_company):
        # El schema ya lo bloquea, pero el gate es la segunda linea. Si alguien
        # relaja el schema, esto sigue en pie.
        resp = await async_client.post(
            "/api/v1/tickets/",
            json={
                "company_id": str(test_company.id),
                "provider_name": "WALMART",
                "total_amount": "-5.00",
                "expense_date": "2025-01-15",
            },
        )
        assert resp.status_code == 422


class TestPersistenciaConGate:
    @pytest.mark.asyncio
    async def test_confianza_alta_auto_aprueba(self, db_session, test_company):
        ticket = await _persistir(
            db_session, test_company.id, b"imagen-1",
            confidence=0.96, subtotal="1000.00",
        )
        assert ticket.extraction_status == "AUTO_APROBADO"
        assert ticket.confidence_source == "llm"
        assert float(ticket.confidence) == pytest.approx(0.96)
        assert ticket.validation_errors is None

    @pytest.mark.asyncio
    async def test_confianza_media_va_a_cola(self, db_session, test_company):
        ticket = await _persistir(db_session, test_company.id, b"imagen-2", confidence=0.75)
        assert ticket.extraction_status == "REQUIERE_REVISION"
        assert ticket.is_open_for_review

    @pytest.mark.asyncio
    async def test_documento_ilegible_queda_pendiente_pero_se_guarda(self, db_session, test_company):
        # Lo importante: NO se pierde. Un error invisible es peor que un error
        # visible, porque nadie lo va a corregir.
        ticket = await _persistir(
            db_session, test_company.id, b"imagen-3",
            provider_name="Unknown Provider", total="0.00", tax="0.00",
            confidence=0.99,
        )
        assert ticket.extraction_status == "PENDIENTE"
        assert ticket.id is not None
        assert "provider_missing" in ticket.validation_errors

    @pytest.mark.asyncio
    async def test_confianza_alta_no_salva_aritmetica_rota(self, db_session, test_company):
        # El caso que define el gate: 0.99 de confianza con total imposible.
        ticket = await _persistir(
            db_session, test_company.id, b"imagen-4",
            confidence=0.99, subtotal="10.00",
        )
        assert ticket.extraction_status != "AUTO_APROBADO"
        assert "subtotal_plus_tax_mismatch" in ticket.validation_errors

    @pytest.mark.asyncio
    async def test_confianza_alta_no_salva_rfc_malformado(self, db_session, test_company):
        ticket = await _persistir(
            db_session, test_company.id, b"imagen-5",
            confidence=0.99, rfc="WALM910101",
        )
        assert ticket.extraction_status == "REQUIERE_REVISION"
        assert "malformed_rfc" in ticket.validation_errors

    @pytest.mark.asyncio
    async def test_fecha_futura_no_se_auto_aprueba(self, db_session, test_company):
        futuro = date.today() + timedelta(days=90)
        ticket = await _persistir(
            db_session, test_company.id, b"imagen-6",
            confidence=0.99, fecha=futuro,
        )
        assert ticket.extraction_status != "AUTO_APROBADO"
        assert "date_in_future" in ticket.validation_errors

    @pytest.mark.asyncio
    async def test_se_guarda_el_archivo_origen(self, db_session, test_company):
        # Sin source_file no hay forma de volver al documento cuando el
        # ticket aparece en la cola.
        ticket = await _persistir(db_session, test_company.id, b"imagen-7", confidence=0.96)
        assert ticket.source_file == "ticket.jpg"
        assert ticket.source_hash

    @pytest.mark.asyncio
    async def test_el_total_preserva_decimal(self, db_session, test_company):
        # El camino de montos es Decimal end-to-end. Si un float se cuela,
        # la conciliacion empieza a fallar por centavos que nadie explica.
        ticket = await _persistir(
            db_session, test_company.id, b"imagen-8",
            total="1160.10", tax="0.01", confidence=0.96,
        )
        assert Decimal(str(ticket.total_amount)) == Decimal("1160.10")


class TestIdempotenciaPorHash:
    @pytest.mark.asyncio
    async def test_el_mismo_archivo_no_crea_dos_tickets(self, db_session, test_company):
        # Reintentar la carga de un lote es la operacion normal cuando algo
        # se cae a mitad de camino. Sin esto, cada reintento duplica gastos.
        primero = await _persistir(db_session, test_company.id, b"mismo-archivo", confidence=0.96)
        segundo = await _persistir(db_session, test_company.id, b"mismo-archivo", confidence=0.96)
        assert primero.id == segundo.id

    @pytest.mark.asyncio
    async def test_archivos_distintos_crean_tickets_distintos(self, db_session, test_company):
        a = await _persistir(db_session, test_company.id, b"archivo-a", confidence=0.96)
        b = await _persistir(db_session, test_company.id, b"archivo-b", confidence=0.96)
        assert a.id != b.id

    @pytest.mark.asyncio
    async def test_un_byte_de_diferencia_es_otro_documento(self, db_session, test_company):
        # Mismo nombre de archivo, contenido distinto: son dos tickets.
        a = await _persistir(db_session, test_company.id, b"factura-v1", confidence=0.96)
        b = await _persistir(db_session, test_company.id, b"factura-V1", confidence=0.96)
        assert a.id != b.id


class TestColaDeRevision:
    @pytest.mark.asyncio
    async def test_la_cola_solo_muestra_lo_que_requiere_persona(self, async_client, db_session, test_company):
        await _trio_de_prueba(db_session, test_company.id)

        resp = await async_client.get(f"/api/v1/tickets/review-queue?company_id={test_company.id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_open"] == 2
        estados = {t["extraction_status"] for t in data["tickets"]}
        assert estados == {"PENDIENTE", "REQUIERE_REVISION"}
        assert "AUTO_APROBADO" not in estados

    @pytest.mark.asyncio
    async def test_la_cola_agrupa_por_estado(self, async_client, db_session, test_company):
        await _trio_de_prueba(db_session, test_company.id)

        resp = await async_client.get(f"/api/v1/tickets/review-queue?company_id={test_company.id}")

        por_estado = resp.json()["por_estado"]
        assert por_estado["PENDIENTE"] == 1
        assert por_estado["REQUIERE_REVISION"] == 1
        assert por_estado.get("AUTO_APROBADO") == 1

    @pytest.mark.asyncio
    async def test_la_cola_reporta_antiguedad(self, async_client, db_session, test_company):
        # Sin antiguedad no hay forma de priorizar: todos los pendientes se
        # ven igual de urgentes y la cola nunca se vacia.
        await _trio_de_prueba(db_session, test_company.id)

        resp = await async_client.get(f"/api/v1/tickets/review-queue?company_id={test_company.id}")

        assert resp.json()["antiguedad_promedio_dias"] is not None

    @pytest.mark.asyncio
    async def test_cola_vacia_no_rompe(self, async_client, test_company):
        resp = await async_client.get(f"/api/v1/tickets/review-queue?company_id={test_company.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_open"] == 0
        assert data["tickets"] == []
        assert data["antiguedad_promedio_dias"] is None

    @pytest.mark.asyncio
    async def test_la_cola_se_filtra_por_estado(self, async_client, db_session, test_company):
        await _trio_de_prueba(db_session, test_company.id)

        resp = await async_client.get(
            f"/api/v1/tickets/review-queue?company_id={test_company.id}&status=PENDIENTE"
        )

        data = resp.json()
        assert len(data["tickets"]) == 1
        assert data["tickets"][0]["extraction_status"] == "PENDIENTE"

    @pytest.mark.asyncio
    async def test_filtrar_por_only_open(self, async_client, db_session, test_company):
        await _trio_de_prueba(db_session, test_company.id)

        resp = await async_client.get(f"/api/v1/tickets/?company_id={test_company.id}&only_open=true")

        assert len(resp.json()) == 2


class TestRevisionHumana:
    async def _pendiente(self, db_session, company_id):
        return await _persistir(
            db_session, company_id, b"revisar-1",
            provider_name="Unknown Provider", total="0.00", tax="0.00", confidence=0.4,
        )

    @pytest.mark.asyncio
    async def test_no_se_aprueba_un_ticket_que_sigue_roto(self, async_client, db_session, test_company):
        # Este es el punto del diseno: la revision es una opinion, los checks
        # son una ley. Aprobar sin corregir deja el ticket igual de inservible
        # pero con sello de "validado".
        ticket = await self._pendiente(db_session, test_company.id)

        resp = await async_client.patch(
            f"/api/v1/tickets/{ticket.id}/review",
            json={"action": "approve"},
        )

        assert resp.status_code == 422
        assert "No se puede aprobar" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_corregir_y_aprobar_si_funciona(self, async_client, db_session, test_company):
        ticket = await self._pendiente(db_session, test_company.id)

        resp = await async_client.patch(
            f"/api/v1/tickets/{ticket.id}/review",
            json={
                "action": "approve",
                "provider_name": "OXXO",
                "total_amount": "150.00",
                "tax_amount": "20.69",
                "notes": "Leido a mano del papel",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["extraction_status"] == "APROBADO"
        assert data["provider_name"] == "OXXO"
        assert data["validation_errors"] is None
        assert data["review_notes"] == "Leido a mano del papel"
        assert data["reviewed_at"] is not None

    @pytest.mark.asyncio
    async def test_rechazar_saca_el_ticket_de_la_cola(self, async_client, db_session, test_company):
        ticket = await self._pendiente(db_session, test_company.id)

        resp = await async_client.patch(
            f"/api/v1/tickets/{ticket.id}/review",
            json={"action": "reject", "notes": "Comprobante ilegible"},
        )

        assert resp.status_code == 200
        assert resp.json()["extraction_status"] == "RECHAZADO"

        cola = await async_client.get(f"/api/v1/tickets/review-queue?company_id={test_company.id}")
        assert cola.json()["total_open"] == 0

    @pytest.mark.asyncio
    async def test_rechazar_es_el_escape_para_lo_ilegible(self, async_client, db_session, test_company):
        # Un papel quemado nunca va a ser un ticket. Tiene que haber una
        # forma de sacarlo de la cola o la cola crece para siempre.
        ticket = await self._pendiente(db_session, test_company.id)
        await async_client.patch(
            f"/api/v1/tickets/{ticket.id}/review",
            json={"action": "reject", "notes": "Foto borrosa, se descarta"},
        )
        assert ticket.id is not None

    @pytest.mark.asyncio
    async def test_editar_un_pendiente_lo_saca_de_la_cola(self, async_client, db_session, test_company):
        # Corregir los datos tiene que passar los checks, y si pasan, el
        # ticket sale de la cola. Dejarlo pendiente cuando ya no necesita nada
        # es como se llena la cola de cosas que nadie revisa.
        ticket = await self._pendiente(db_session, test_company.id)

        resp = await async_client.patch(
            f"/api/v1/tickets/{ticket.id}",
            json={"provider_name": "COSTARCO", "total_amount": "500.00"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["extraction_status"] == "APROBADO"
        assert data["validation_errors"] is None

    @pytest.mark.asyncio
    async def test_editar_con_datos_que_siguen_rotos_deja_el_ticket_en_cola(self, async_client, db_session, test_company):
        # Corregir a medias no es corregir. Si el total sigue en 0, el ticket
        # se queda en la cola.
        ticket = await self._pendiente(db_session, test_company.id)

        resp = await async_client.patch(
            f"/api/v1/tickets/{ticket.id}",
            json={"provider_name": "COSTARCO"},
        )

        data = resp.json()
        assert data["extraction_status"] == "REQUIERE_REVISION"
        assert "total_not_positive" in data["validation_errors"]

    @pytest.mark.asyncio
    async def test_accion_invalida_se_rechaza(self, async_client, db_session, test_company):
        ticket = await self._pendiente(db_session, test_company.id)
        resp = await async_client.patch(
            f"/api/v1/tickets/{ticket.id}/review",
            json={"action": "borrar_todo"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_revisar_ticket_inexistente_da_404(self, async_client):
        from uuid import uuid4

        resp = await async_client.patch(
            f"/api/v1/tickets/{uuid4()}/review",
            json={"action": "approve"},
        )
        assert resp.status_code == 404


class TestContratoDeLaCola:
    @pytest.mark.asyncio
    async def test_el_endpoint_no_lucha_con_el_uuid(self, async_client, test_company):
        # /review-queue esta declarado antes de /{ticket_id}. Si alguien mueve
        # el orden, esto devuelve 422 y la cola aparece "vacia" sin error.
        resp = await async_client.get(f"/api/v1/tickets/review-queue?company_id={test_company.id}")
        assert resp.status_code == 200
        assert "tickets" in resp.json()

    @pytest.mark.asyncio
    async def test_los_estados_de_la_cola_coinciden_con_el_enum(self, async_client, db_session, test_company):
        # Si el enum y el filtro divergen, la cola sale vacia en silencio.
        from app.core.enums import ExtractionStatus

        await _trio_de_prueba(db_session, test_company.id)
        resp = await async_client.get(f"/api/v1/tickets/review-queue?company_id={test_company.id}")

        por_estado = resp.json()["por_estado"]
        assert ExtractionStatus.PENDIENTE.value in por_estado
        assert ExtractionStatus.REQUIERE_REVISION.value in por_estado
        assert ExtractionStatus.AUTO_APROBADO.value in por_estado

    @pytest.mark.asyncio
    async def test_la_respuesta_expone_la_trazabilidad(self, async_client, db_session, test_company):
        # Sin estos campos, el revisor no puede saber de donde salio el dato
        # ni que le fallo. Solo ve "algo no cuadra".
        ticket = await _persistir(
            db_session, test_company.id, b"trazabilidad-1",
            confidence=0.99, subtotal="10.00",
        )
        resp = await async_client.get(f"/api/v1/tickets/{ticket.id}")

        data = resp.json()
        for campo in (
            "confidence", "confidence_source", "extraction_status",
            "source_type", "source_file", "validation_errors",
        ):
            assert campo in data, f"falta {campo} en la respuesta"


class TestCadenaDeProveedorDesconocido:
    """El unico contrato que cruza cuatro archivos y dos lenguajes.

    El parser emite `UNKNOWN_PROVIDER` cuando no lee al emisor, el gate lo
    reconoce para marcar `provider_missing`, los schemas lo rechazan al escribir
    y la API lo pone cuando la extraccion viene vacia. Si cualquiera de los
    cuatro deja de usar la constante, un documento ilegible entra a conciliacion
    como si estuviera bien leido: el fallo mas caro y mas silencioso del
    sistema.

    Aqui se comprueba el comportamiento, no la presencia del texto. Comprobar
    que la API usa la constante no dice nada de que la cadena correcta sea la
    que llega a la base; esto si.
    """

    async def test_una_extraccion_sin_proveedor_usa_la_cadena_canonica(
        self, db_session, test_company
    ):
        from app.core.enums import UNKNOWN_PROVIDER

        t = await _persistir(
            db_session, test_company.id, b"sin-proveedor",
            provider_name="", total="0", tax="0",
        )
        # provider_name="" es lo que devuelve el parser cuando no lee nada.
        assert t.provider_name == UNKNOWN_PROVIDER
        assert t.extraction_status == "PENDIENTE"
        assert "provider_missing" in (t.validation_errors or "")

    async def test_el_gate_no_aprueba_un_proveedor_desconocido(
        self, db_session, test_company
    ):
        """Confianza alta no compra al proveedor equivocado.

        Es el caso donde un modelo se equivoca confiado: dice 0.99 sobre algo
        que no leyo. Si el gate lo dejara pasar, el ticket entra a conciliacion
        sin que nadie lo mire.
        """
        from app.core.enums import UNKNOWN_PROVIDER

        t = await _persistir(
            db_session, test_company.id, b"confianza-alta-sin-proveedor",
            provider_name=UNKNOWN_PROVIDER, total="500.00", tax="69.00",
            subtotal="431.00", confidence=0.99,
        )
        assert t.extraction_status != "AUTO_APROBADO"
        assert t.extraction_status in ("PENDIENTE", "REQUIERE_REVISION")
        assert "provider_missing" in (t.validation_errors or "")
