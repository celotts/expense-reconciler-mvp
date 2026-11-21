"""Que los endpoints que escriben dejan rastro en `auditoria`.

`tests/unit/test_auditoria.py` prueba los helpers sueltos. Este archivo
prueba el cableado: que el endpoint de verdad llama al helper con la sesion
del request y el usuario del token, y que la fila queda en la misma base que
el test lee.

Solo tres casos, los que sostienen el resto del cableado: crear un ticket,
borrar un ticket y un login fallido con cuenta existente. Los demas
endpoints (empresas, usuarios, clasificar, veredicto, reset) usan las mismas
llamadas y los mismos helpers.
"""

import pytest
from sqlalchemy import select

from app.models import Auditoria


def _ticket_manual(company_id):
    """Un ticket manual valido: total positivo, fecha de ayer, sin subtotal."""
    return {
        "provider_name": "Tienda Prueba",
        "total_amount": "100.00",
        "expense_date": "2026-11-19",
        "company_id": str(company_id),
    }


@pytest.mark.asyncio
async def test_crear_ticket_registra_evento(
    async_client, db_session, test_company, usuario_de_prueba
):
    """POST /tickets/ escribe una fila `crear_ticket` con quien lo creo."""
    respuesta = await async_client.post(
        "/tickets/", json=_ticket_manual(test_company.id)
    )
    assert respuesta.status_code == 201, respuesta.text
    ticket_id = respuesta.json()["id"]

    fila = (
        await db_session.execute(
            select(Auditoria).where(Auditoria.accion == "crear_ticket")
        )
    ).scalar_one()

    assert fila.modulo == "tickets"
    assert fila.usuario_id == usuario_de_prueba.id
    assert str(fila.registro_afectado) == ticket_id
    assert fila.resultado == "exito"


@pytest.mark.asyncio
async def test_eliminar_ticket_registra_evento(
    async_client, db_session, test_company, usuario_de_prueba
):
    """DELETE /tickets/{id} escribe una fila `delete` con quien lo borro."""
    creado = await async_client.post(
        "/tickets/", json=_ticket_manual(test_company.id)
    )
    assert creado.status_code == 201, creado.text
    ticket_id = creado.json()["id"]

    borrado = await async_client.delete(f"/tickets/{ticket_id}")
    assert borrado.status_code == 204, borrado.text

    fila = (
        await db_session.execute(
            select(Auditoria).where(Auditoria.accion == "delete")
        )
    ).scalar_one()

    assert fila.modulo == "tickets"
    assert fila.usuario_id == usuario_de_prueba.id
    assert str(fila.registro_afectado) == ticket_id
    assert fila.resultado == "exito"


@pytest.mark.asyncio
async def test_login_fallido_registra_evento(
    async_client_sin_autenticar, db_session, usuario_de_prueba
):
    """Un login con contrasena incorrecta y cuenta existente guarda el id.

    La cuenta existe, asi que `usuario_id` si se pone: es lo que distingue
    este caso de un correo que no esta dado de alta, donde queda NULL.
    """
    respuesta = await async_client_sin_autenticar.post(
        "/auth/login",
        json={"email": usuario_de_prueba.email, "password": "contrasena-incorrecta"},
    )
    assert respuesta.status_code == 401, respuesta.text

    fila = (
        await db_session.execute(
            select(Auditoria).where(Auditoria.accion == "login_fallido")
        )
    ).scalar_one()

    assert fila.modulo == "auth"
    assert fila.usuario_id == usuario_de_prueba.id
    assert fila.resultado == "fallo"
    assert usuario_de_prueba.email in (fila.detalles or "")
