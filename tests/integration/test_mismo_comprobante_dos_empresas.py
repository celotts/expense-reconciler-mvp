"""Escenario real: el MISMO comprobante no cruza de una empresa a otra.

Dos preguntas distintas, con dos respuestas distintas:

1. Un ticket identificado por ID pertenece a una empresa. Pedirlo, editarlo o
   aprobarlo con otra empresa activa no se procesa: es 404, no 409 y no 200.
2. Volver a subir los MISMOS bytes con otra empresa es una carga nueva. Eso es
   lo que hoy hace el sistema por diseno (AGENTS.md regla 8), y este archivo lo
   deja escrito como escenario real para decidir con evidencia, no con opinion.

Si el producto decide que (2) tampoco deba registrarse, este es el archivo que
tiene que cambiar: el criterio (1) no se toca.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.enums import ExtractionStatus, RolEmpresa
from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.models.user import UserModel
from app.models.usuario_empresa import UsuarioEmpresaModel
from app.services.parser_service import TicketExtractionResult


COMPROBANTE = b"escenario real: el mismo comprobante, dos empresas"


def _extracted() -> TicketExtractionResult:
    """Una lectura plausible: con datos no basta la opinion del cliente."""
    return TicketExtractionResult(
        provider_name="Proveedor Real SA de CV",
        provider_tax_id="PRR010101AB1",
        invoice_number=None,
        invoice_series=None,
        expense_date=date(2026, 11, 20),
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("16.00"),
        ieps_amount=None,
        total_amount=Decimal("116.00"),
        currency=None,
        exchange_rate=None,
        payment_method=None,
        confidence=Decimal("0.90"),
        confidence_source="llm",
        items=[],
        raw_text="COMPROBANTE REAL",
        validation_errors=[],
        extraction_status=ExtractionStatus.AUTO_APROBADO,
        model_name=None,
    )


async def _persistir(db_session, company_id, contenido=COMPROBANTE):
    from app.api.tickets import _persist_extracted
    from app.core.enums import SourceType

    return await _persist_extracted(
        db_session,
        company_id,
        _extracted(),
        contenido,
        SourceType.IMAGE,
        source_file="comprobante.pdf",
    )


async def _empresa(db_session, nombre: str) -> CompanyModel:
    empresa = CompanyModel(name=nombre, tax_id=f"ESC{uuid4().hex[:9].upper()}")
    db_session.add(empresa)
    await db_session.commit()
    await db_session.refresh(empresa)
    return empresa


async def _miembro(db_session, empresa: CompanyModel, usuario: UserModel) -> None:
    """Sin membresia la cuenta es legacy y ve todo: el test no probaria nada."""
    db_session.add(
        UsuarioEmpresaModel(
            user_id=usuario.id,
            company_id=empresa.id,
            rol=RolEmpresa.ADMIN,
            activo=True,
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_guardar_el_mismo_papel_en_la_segunda_empresa_no_devuelve_el_ticket_ajeno(
    db_session, test_company
):
    """El primer escenario: la segunda empresa no ve el ticket de la primera."""
    otra = await _empresa(db_session, "Segunda empresa")

    primero = await _persistir(db_session, test_company.id)
    segundo = await _persistir(db_session, otra.id)

    assert primero.id != segundo.id
    assert primero.company_id == test_company.id
    assert segundo.company_id == otra.id

    filas = (await db_session.execute(select(TicketModel))).scalars().all()
    assert {fila.company_id for fila in filas} == {test_company.id, otra.id}


@pytest.mark.asyncio
async def test_el_id_de_la_primera_empresa_no_se_lee_desde_la_segunda(
    async_client, db_session, test_company, usuario_de_prueba
):
    """El segundo escenario: un ID ajeno no se procesa ni se muestra."""
    ticket = await _persistir(db_session, test_company.id)
    otra = await _empresa(db_session, "Empresa que no es dueno del ticket")
    # La cuenta pertenece a la segunda empresa, no a la duena del ticket. Sin
    # la membresia seria legacy (ve todo) y el 404 no probaria el alcance.
    await _miembro(db_session, otra, usuario_de_prueba)

    ajeno = await async_client.get(
        f"/api/v1/tickets/{ticket.id}",
        headers={"X-Company-ID": str(otra.id)},
    )
    assert ajeno.status_code == 404, ajeno.text

    # Contraprueba: el MISMO id con la empresa duena si se lee. Sin esto, un
    # 404 por ruta mal escrita daria verde.
    await _miembro(db_session, test_company, usuario_de_prueba)
    propio = await async_client.get(
        f"/api/v1/tickets/{ticket.id}",
        headers={"X-Company-ID": str(test_company.id)},
    )
    assert propio.status_code == 200, propio.text
    assert propio.json()["company_id"] == str(test_company.id)
