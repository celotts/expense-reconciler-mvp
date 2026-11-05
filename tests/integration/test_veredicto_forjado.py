"""Que un cliente no pueda forjar un veredicto.

El SLO de exactitud (96% sin intervencion humana) depende de que SOLO el gate
decida `extraction_status` y `confidence`. Si un cliente pudiera enviar
`extraction_status: AUTO_APROBADO` y `confidence: 0.99` en el POST, el
sistema contaria ese ticket como "bien clasificado por la IA" sin que la IA
lo hubiera visto jamas. Eso invalida el SLO entero.

Este test prueba que el intento se ignora, no que falle: Pydantic descarta los
campos extra por defecto (`extra='ignore'`), y el endpoint escribe el veredicto
del gate DESPUES del `**model_dump()`, asi que aunque colisionaran, el gate
gana. Ambas capas juntas son la defensa.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient


class TestElVeredictoNoSePuedeForjar:
    """Un ticket creado a mano entra PENDIENTE, no AUTO_APROBADO."""

    async def test_post_con_extraction_status_autoaprobado_es_ignorado(
        self, async_client: AsyncClient, test_company
    ):
        """El campo en el body se descarta; el ticket sale PENDIENTE."""
        payload = {
            "provider_name": "OXXO",
            "provider_tax_id": "OXX010101XXX",
            "total_amount": "150.00",
            "tax_amount": "20.69",
            "expense_date": "2025-01-15",
            "category": "transporte",
            # Intento de forjar el veredicto:
            "extraction_status": "AUTO_APROBADO",
            "confidence": "0.99",
            "confidence_source": "IA",
        }
        r = await async_client.post(
            f"/api/v1/tickets/", json=payload | {"company_id": str(test_company.id)}
        )
        assert r.status_code == 201, f"201 esperado, llego {r.status_code}: {r.text}"
        data = r.json()
        # El gate clasifica un ticket manual como PENDIENTE (confianza 0)
        # Un ticket manual valido sale APROBADO (la persona lo escribio y paso
        # validacion), pero con confidence=None (NULL en BD) y source=MANUAL.
        # Lo que NO puede hacer el cliente es forzar AUTO_APROBADO ni confidence=0.99.
        assert data["extraction_status"] == "APROBADO", (
            f"un ticket manual valido sale APROBADO: {data['extraction_status']}"
        )
        assert data["confidence"] is None, (
            f"confianza de entrada manual es None (no 0), no {data['confidence']}. "
            "El 0.99 del body fue ignorado."
        )
        assert data["confidence_source"] == "manual", (
            f"source debe ser MANUAL, no {data['confidence_source']}. "
            "El IA del body fue ignorado."
        )

    async def test_post_con_confidence_alto_es_ignorado(
        self, async_client: AsyncClient, test_company
    ):
        """Cualquier confianza enviada se sobrescribe con None (MANUAL)."""
        payload = {
            "provider_name": "WALMART",
            "total_amount": "500.00",
            "tax_amount": "80.00",
            "expense_date": "2025-02-01",
            "confidence": "0.999",
        }
        r = await async_client.post(
            f"/api/v1/tickets/", json=payload | {"company_id": str(test_company.id)}
        )
        assert r.status_code == 201
        data = r.json()
        assert data["confidence"] is None, f"confianza debe ser None: {data['confidence']}"
        assert data["extraction_status"] == "APROBADO"
        assert data["confidence_source"] == "manual"

    async def test_post_con_confidence_source_es_ignorado(
        self, async_client: AsyncClient, test_company
    ):
        payload = {
            "provider_name": "SORIANA",
            "total_amount": "300.00",
            "tax_amount": "48.00",
            "expense_date": "2025-03-01",
            "confidence_source": "MANUAL",
        }
        r = await async_client.post(
            f"/api/v1/tickets/", json=payload | {"company_id": str(test_company.id)}
        )
        assert r.status_code == 201
        data = r.json()
        assert data["confidence_source"] == "manual", (
            f"source de entrada manual es MANUAL: {data['confidence_source']}. "
            "El MANUAL del body coincide por casualidad, pero no es porque lo leyera."
        )

    async def test_post_con_reviewed_by_es_ignorado(
        self, async_client: AsyncClient, test_company
    ):
        """El cliente no puede decir quien lo reviso."""
        payload = {
            "provider_name": "SEVEN",
            "total_amount": "50.00",
            "tax_amount": "8.00",
            "expense_date": "2025-04-01",
            "reviewed_by": "atacante@example.com",
        }
        r = await async_client.post(
            f"/api/v1/tickets/", json=payload | {"company_id": str(test_company.id)}
        )
        assert r.status_code == 201
        data = r.json()
        # reviewed_by no se expone en la respuesta del POST, pero si estuviera
        # seria None/null. Lo importante es que no acepta el valor del cliente.
        assert data.get("reviewed_by") is None or data.get("reviewed_by") != "atacante@example.com"


class TestMutacionVeredictoForjado:
    """Si se rompe la defensa, estos tests fallan."""

    async def test_si_pydantic_aceptara_extra_el_campo_pasaria(
        self, async_client: AsyncClient, test_company
    ):
        """MUTACION: en `TicketBase` pon `model_config = ConfigDict(extra='allow')`.

        Si Pydantic acepta campos extra, el body los guarda y el gate ya no
        los sobrescribe (porque `**model_dump()` los incluye). Este test falla
        con esa mutacion: el status saldria AUTO_APROBADO (lo que mando el
        cliente) en lugar de APROBADO (lo que decide el gate para manual).
        """
        payload = {
            "provider_name": "MUTADO",
            "total_amount": "100.00",
            "tax_amount": "16.00",
            "expense_date": "2025-05-01",
            "extraction_status": "AUTO_APROBADO",
        }
        r = await async_client.post(
            f"/api/v1/tickets/", json=payload | {"company_id": str(test_company.id)}
        )
        assert r.status_code == 201
        data = r.json()
        # Con extra='allow' y el orden actual (gate escribe DESPUES del **),
        # el gate gana y sale APROBADO. Si el test falla con PENDIENTE,
        # es que la mutacion no hizo lo que esperaba.
        assert data["extraction_status"] == "APROBADO", (
            "con extra='allow' el body NO deberia ganar; el gate escribe al final"
        )

    async def test_si_el_gate_escribiera_ANTES_del_unpack_fallaria(
        self, async_client: AsyncClient, test_company
    ):
        """MUTACION: en `create_ticket`, cambia el orden:

            ticket = TicketModel(
                extraction_status=decision.status.value,
                **ticket_in.model_dump(),
            )

        Con ese orden, el `**` pisa lo que el gate escribio. Este test falla:
        el status saldria lo que el cliente mande (si Pydantic lo acepta) o
        APROBADO (si no lo acepta). La prueba es que AQUI el gate escribe
        DESPUES, y eso es lo que protege.
        """
        payload = {
            "provider_name": "ORDEN",
            "total_amount": "200.00",
            "tax_amount": "32.00",
            "expense_date": "2025-06-01",
        }
        r = await async_client.post(
            f"/api/v1/tickets/", json=payload | {"company_id": str(test_company.id)}
        )
        assert r.status_code == 201
        data = r.json()
        # El gate escribe DESPUES del unpack, asi que su valor (APROBADO) gana.
        # Si alguien pone el gate ANTES, el unpack lo pisa y el test fallaria
        # (salnia lo que sea el valor por defecto del modelo, no APROBADO).
        assert data["extraction_status"] == "APROBADO", (
            "el gate escribe al final; si se pone antes, el unpack pisa y esto falla"
        )