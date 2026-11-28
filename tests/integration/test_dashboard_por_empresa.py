"""El tablero pertenece a la empresa de `X-Company-ID`, no a todas.

MEDIDO el 2026-11-27: el front dejo de mandar `?company_id=` para no pelearse
con la cabecera, pero `GET /dashboard/` seguia leyendo solo la query. Con
`company_id=None` el endpoint sumaba las dos empresas, asi que al cambiar de
empresa en Inicio las cifras y el nombre no cambiaban. El ticket si tenia
`company_id` en la fila; lo que faltaba era que el tablero lo usara.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.enums import ExtractionStatus, RolEmpresa, SourceType
from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.models.usuario_empresa import UsuarioEmpresaModel
from app.services.parser_service import TicketExtractionResult


def _empresa(db_session, nombre: str) -> CompanyModel:
    empresa = CompanyModel(name=nombre, tax_id=f"TAB{uuid4().hex[:9].upper()}")
    db_session.add(empresa)
    return empresa


async def _miembro(db_session, empresa: CompanyModel, usuario) -> None:
    """Sin membresia la cuenta es legacy y ve todo: el test no probaria nada."""
    db_session.add(
        UsuarioEmpresaModel(
            user_id=usuario.id,
            company_id=empresa.id,
            rol=RolEmpresa.ADMIN,
            activo=True,
        )
    )


async def _ticket(db_session, empresa: CompanyModel, total: str) -> TicketModel:
    from app.api.tickets import _persist_extracted

    return await _persist_extracted(
        db_session,
        empresa.id,
        TicketExtractionResult(
            provider_name="Proveedor del tablero",
            provider_tax_id=None,
            invoice_number=None,
            invoice_series=None,
            expense_date=date.today(),
            subtotal=Decimal(total),
            tax_amount=Decimal("0"),
            ieps_amount=None,
            total_amount=Decimal(total),
            currency=None,
            exchange_rate=None,
            payment_method=None,
            confidence=Decimal("0.90"),
            confidence_source="llm",
            items=[],
            raw_text="COMPROBANTE DEL TABLERO",
            validation_errors=[],
            extraction_status=ExtractionStatus.AUTO_APROBADO,
            model_name=None,
        ),
        f"tablero-{total}".encode(),
        SourceType.IMAGE,
        source_file=f"tablero-{total}.pdf",
    )


@pytest.mark.asyncio
async def test_cambiar_de_empresa_cambia_las_cifras_y_el_id(
    async_client, db_session, test_company, usuario_de_prueba
):
    segunda = _empresa(db_session, "Segunda del tablero")
    await db_session.flush()
    await _miembro(db_session, test_company, usuario_de_prueba)
    await _miembro(db_session, segunda, usuario_de_prueba)
    await db_session.commit()

    await _ticket(db_session, test_company, "100.00")
    await _ticket(db_session, segunda, "250.00")

    primera_resp = await async_client.get(
        "/api/v1/dashboard/", headers={"X-Company-ID": str(test_company.id)}
    )
    segunda_resp = await async_client.get(
        "/api/v1/dashboard/", headers={"X-Company-ID": str(segunda.id)}
    )
    assert primera_resp.status_code == 200, primera_resp.text
    assert segunda_resp.status_code == 200, segunda_resp.text

    primero = primera_resp.json()
    segundo = segunda_resp.json()
    assert primero["company_id"] == str(test_company.id)
    assert segundo["company_id"] == str(segunda.id)
    assert primero["company_name"] == test_company.name
    assert segundo["company_name"] == segunda.name
    assert Decimal(primero["mes"]["monto"]) == Decimal("100.00")
    assert Decimal(segundo["mes"]["monto"]) == Decimal("250.00")

    # Los tickets siguen en su empresa: el tablero no cruza ni mueve filas.
    filas = (await db_session.execute(select(TicketModel))).scalars().all()
    assert {fila.company_id for fila in filas} == {test_company.id, segunda.id}


@pytest.mark.asyncio
async def test_el_id_de_la_respuesta_no_se_puede_suplantar_con_la_query(
    async_client, db_session, test_company, usuario_de_prueba
):
    segunda = _empresa(db_session, "Otra del tablero")
    await db_session.flush()
    await _miembro(db_session, test_company, usuario_de_prueba)
    await _miembro(db_session, segunda, usuario_de_prueba)
    await db_session.commit()

    sin_elegir = await async_client.get("/api/v1/dashboard/")
    assert sin_elegir.status_code == 409, sin_elegir.text

    mezclada = await async_client.get(
        "/api/v1/dashboard/",
        params={"company_id": str(segunda.id)},
        headers={"X-Company-ID": str(test_company.id)},
    )
    assert mezclada.status_code == 409, mezclada.text
