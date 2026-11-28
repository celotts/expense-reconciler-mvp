"""El explicador es de solo lectura y no cruza empresas.

Un ticket ajeno por id da 404, igual que en `tickets.py`. Con la funcion
apagada el endpoint responde porque dice explicitamente que no hay explicacion:
un 404 podria confundirse con "el ticket no existe".
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.api.ia_asistida import get_explicador_ia
from app.main import app
from app.core.enums import ExtractionStatus, RolEmpresa, SourceType
from app.models.company import CompanyModel
from app.models.usuario_empresa import UsuarioEmpresaModel
from app.schemas.ia_asistida import ExplicacionTicketResponse
from app.services.ai_client import AIResponse
from app.services.parser_service import TicketExtractionResult


def _empresa(db_session, nombre: str) -> CompanyModel:
    empresa = CompanyModel(name=nombre, tax_id=f"IA{uuid4().hex[:9].upper()}")
    db_session.add(empresa)
    return empresa


async def _miembro(db_session, empresa: CompanyModel, usuario) -> None:
    db_session.add(
        UsuarioEmpresaModel(
            user_id=usuario.id,
            company_id=empresa.id,
            rol=RolEmpresa.ADMIN,
            activo=True,
        )
    )


async def _ticket(db_session, empresa: CompanyModel):
    from app.api.tickets import _persist_extracted

    return await _persist_extracted(
        db_session,
        empresa.id,
        TicketExtractionResult(
            provider_name="Comercio de la IA",
            provider_tax_id=None,
            invoice_number=None,
            invoice_series=None,
            expense_date=date.today(),
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("0.00"),
            ieps_amount=None,
            total_amount=Decimal("100.00"),
            currency=None,
            exchange_rate=None,
            payment_method=None,
            confidence=Decimal("0.90"),
            confidence_source="llm",
            items=[],
            raw_text="original",
            validation_errors=[],
            extraction_status=ExtractionStatus.AUTO_APROBADO,
            model_name=None,
        ),
        f"ia-{uuid4().hex}".encode(),
        SourceType.IMAGE,
        source_file="ia.pdf",
    )


class ExplicadorFalso:
    def __init__(self, respuesta: str):
        self.respuesta = respuesta
        self.visto: list[str] = []

    async def explicar(self, ticket) -> ExplicacionTicketResponse:
        self.visto.append(str(ticket.id))
        return ExplicacionTicketResponse(
            ticket_id=ticket.id,
            estado="disponible",
            explicacion=self.respuesta,
            datos_verificables=[],
        )


@pytest.mark.asyncio
async def test_ticket_ajeno_no_se_explica(
    async_client, db_session, test_company, usuario_de_prueba
):
    segunda = _empresa(db_session, "Otra para la IA")
    await db_session.flush()
    await _miembro(db_session, test_company, usuario_de_prueba)
    await _miembro(db_session, segunda, usuario_de_prueba)
    await db_session.commit()
    ticket = await _ticket(db_session, test_company)

    respuesta = await async_client.get(
        f"/api/v1/ia-asistida/tickets/{ticket.id}/explicacion",
        headers={"X-Company-ID": str(segunda.id)},
    )
    assert respuesta.status_code == 404, respuesta.text


@pytest.mark.asyncio
async def test_apagado_dice_que_no_hay_explicacion(
    async_client, db_session, test_company, usuario_de_prueba
):
    await _miembro(db_session, test_company, usuario_de_prueba)
    await db_session.commit()
    ticket = await _ticket(db_session, test_company)

    respuesta = await async_client.get(
        f"/api/v1/ia-asistida/tickets/{ticket.id}/explicacion",
        headers={"X-Company-ID": str(test_company.id)},
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "no_disponible"
    assert cuerpo["explicacion"] == ""
    assert "100" in " ".join(cuerpo["datos_verificables"])


@pytest.mark.asyncio
async def test_con_modelo_inyectado_la_explicacion_llega(
    async_client, db_session, test_company, usuario_de_prueba
):
    await _miembro(db_session, test_company, usuario_de_prueba)
    await db_session.commit()
    ticket = await _ticket(db_session, test_company)
    falso = ExplicadorFalso("El total leido fue 100.00.")

    app.dependency_overrides[get_explicador_ia] = lambda: falso
    try:
        respuesta = await async_client.get(
            f"/api/v1/ia-asistida/tickets/{ticket.id}/explicacion",
            headers={"X-Company-ID": str(test_company.id)},
        )
    finally:
        app.dependency_overrides.pop(get_explicador_ia, None)

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["explicacion"] == "El total leido fue 100.00."
    assert falso.visto == [str(ticket.id)]


@pytest.mark.asyncio
async def test_sin_token_no_entra(
    async_client_sin_autenticar, db_session, test_company
):
    ticket = await _ticket(db_session, test_company)
    respuesta = await async_client_sin_autenticar.get(
        f"/api/v1/ia-asistida/tickets/{ticket.id}/explicacion",
        headers={"X-Company-ID": str(test_company.id)},
    )
    assert respuesta.status_code == 401, respuesta.text
