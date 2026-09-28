"""Quien firmo cada veredicto sale del token, no de un texto fijo.

La columna existia (`reviewed_by`) pero se llenaba con la constante `"user"`.
Eso hace que la columna no responda a la unica pregunta para la que sirve:
"quien cambio esto". Este archivo usa el cliente SIN el override de
autenticacion, porque con el override todos los usuarios serian el mismo y la
atribucion no se podria distinguir de nada.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import crear_token, hashear_contrasena
from app.models.ticket import TicketModel
from app.models.user import UserModel

P = "/api/v1"


async def _usuario(db_session: AsyncSession, nombre: str) -> tuple[UserModel, str]:
    usuario = UserModel(
        email=f"{nombre}{uuid4().hex[:6]}@empresa.mx",
        nombre=nombre,
        password_hash=hashear_contrasena("contrasena-larga"),
    )
    db_session.add(usuario)
    await db_session.commit()
    await db_session.refresh(usuario)
    return usuario, crear_token(str(usuario.id), usuario.email)[0]


async def _ticket_para_aprobar(db_session: AsyncSession, company_id) -> TicketModel:
    """Un ticket roto, que es el caso real de la cola: la revision existe
    justamente para arreglarlo antes de aprobarlo."""

    ticket = TicketModel(
        company_id=company_id,
        provider_name="Unknown Provider",
        total_amount=Decimal("0.00"),
        tax_amount=Decimal("0.00"),
        expense_date=date(2025, 1, 15),
        extraction_status="PENDIENTE",
        source_type="image",
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)
    return ticket


async def _ticket_muestreado(db_session: AsyncSession, company_id) -> TicketModel:
    """Un ticket AUTO_APROBADO con `source_hash`, que es lo unico que se puede
    verificar contra el papel."""

    ticket = TicketModel(
        company_id=company_id,
        provider_name="WALMART",
        total_amount=Decimal("100.00"),
        tax_amount=Decimal("16.00"),
        expense_date=date(2025, 1, 15),
        extraction_status="AUTO_APROBADO",
        confidence=0.95,
        confidence_source="llm",
        source_type="image",
        source_hash=uuid4().hex,
        spot_check_status="PENDIENTE",
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)
    return ticket


class TestLaRevisionDiceQuien:
    async def test_quien_aprobo_es_el_dueño_del_token(
        self, async_client_sin_autenticar, db_session, test_company
    ):
        (_, token) = await _usuario(db_session, "ana")
        ticket = await _ticket_para_aprobar(db_session, test_company.id)

        respuesta = await async_client_sin_autenticar.patch(
            f"{P}/tickets/{ticket.id}/review",
            json={
                "action": "approve",
                "provider_name": "OXXO",
                "total_amount": "50.00",
                "tax_amount": "8.00",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert respuesta.status_code == 200

        await db_session.refresh(ticket)
        assert ticket.reviewed_by != "user"
        assert ticket.reviewed_by.endswith("@empresa.mx")
        assert ticket.reviewed_by.startswith("ana")

    async def test_dos_personas_dejan_autoria_distinta(
        self, async_client_sin_autenticar, db_session, test_company
    ):
        """La prueba de verdad. Con la constante "user", las dos revisiones
       -producian la misma fila y no habria forma de saber quien habia tocado
        que. Con el token, cada una deja su correo y el historico responde a
        "quien rechazo esto"."""

        (_, token_ana) = await _usuario(db_session, "ana")
        (_, token_beto) = await _usuario(db_session, "beto")

        primero = await _ticket_para_aprobar(db_session, test_company.id)
        segundo = await _ticket_para_aprobar(db_session, test_company.id)

        for ticket, token in ((primero, token_ana), (segundo, token_beto)):
            respuesta = await async_client_sin_autenticar.patch(
                f"{P}/tickets/{ticket.id}/review",
                json={"action": "reject", "notes": "no se ve el RFC"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert respuesta.status_code == 200

        await db_session.refresh(primero)
        await db_session.refresh(segundo)

        assert primero.reviewed_by != segundo.reviewed_by
        assert primero.reviewed_by.startswith("ana")
        assert segundo.reviewed_by.startswith("beto")

    async def test_sin_token_no_se_puede_revisar(
        self, async_client_sin_autenticar, db_session, test_company
    ):
        """Sin esto, la revision del ticket mas sensible del sistema (la que
        decide si un gasto entra al historico contable) seria la unica
        operacion sin control de acceso."""

        ticket = await _ticket_para_aprobar(db_session, test_company.id)

        respuesta = await async_client_sin_autenticar.patch(
            f"{P}/tickets/{ticket.id}/review", json={"action": "reject"}
        )

        assert respuesta.status_code == 401
        await db_session.refresh(ticket)
        assert ticket.reviewed_by is None
        assert ticket.reviewed_at is None


class TestElMuestreoDiceQuien:
    async def test_quien_verifico_es_el_dueño_del_token(
        self, async_client_sin_autenticar, db_session, test_company
    ):
        (_, token) = await _usuario(db_session, "ana")
        ticket = await _ticket_muestreado(db_session, test_company.id)

        respuesta = await async_client_sin_autenticar.patch(
            f"{P}/tickets/{ticket.id}/spot-check",
            json={"correct": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert respuesta.status_code == 200

        await db_session.refresh(ticket)
        assert ticket.spot_checked_by is not None
        assert ticket.spot_checked_by.startswith("ana")

    async def test_sin_token_no_se_puede_registrar_un_veredicto(
        self, async_client_sin_autenticar, db_session, test_company
    ):
        """El veredicto del muestreo ES el dato que sostiene el "96% exacto".
        Si cualquiera, sin entrar, pudiera escribir veredictos, la cifra seria
        un numero que se puede escribir a mano, y no una medicion."""

        ticket = await _ticket_muestreado(db_session, test_company.id)

        respuesta = await async_client_sin_autenticar.patch(
            f"{P}/tickets/{ticket.id}/spot-check", json={"correct": True}
        )

        assert respuesta.status_code == 401
        await db_session.refresh(ticket)
        assert ticket.spot_check_status == "PENDIENTE"
        assert ticket.spot_checked_by is None

    async def test_dos_verificadores_dos_autores(
        self, async_client_sin_autenticar, db_session, test_company
    ):
        """El reporte promedia veredictos de varias personas. Sin saber cuales,
        el promedio no tiene dueño y cuando sale mal no hay a quien preguntarle.
        """

        (_, token_ana) = await _usuario(db_session, "ana")
        (_, token_beto) = await _usuario(db_session, "beto")

        for token, correcto in ((token_ana, True), (token_beto, False)):
            ticket = await _ticket_muestreado(db_session, test_company.id)
            respuesta = await async_client_sin_autenticar.patch(
                f"{P}/tickets/{ticket.id}/spot-check",
                json={"correct": correcto, "campos_incorrectos": ["total_amount"]},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert respuesta.status_code == 200
            await db_session.refresh(ticket)
            assert ticket.spot_checked_by is not None

        autores = (
            await db_session.execute(
                select(TicketModel.spot_checked_by).where(
                    TicketModel.spot_checked_by.is_not(None)
                )
            )
        ).scalars().all()

        assert len(autores) == 2
        assert len(set(autores)) == 2
