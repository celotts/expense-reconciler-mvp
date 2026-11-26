"""La guardia de permisos, medida por HTTP y no por el mapa.

`test_permisos_por_accion.py` comprueba que el mapa dice lo que queremos. Aqui
se comprueba lo que importa al sistema: que una peticion con un rol `viewer`
recibe un 403 aunque el front no pinte el boton. Un mapa correcto con una
guardia desconectada es decorativo.

Las pruebas que pueden fallar por el motivo equivocado estan escritas
con la contraprueba al lado: con `admin` la misma ruta TIENE que pasar. Si no,
un 403 por una razon distinta (falta de empresa, de token, de cuerpo) daria
verde sin probar nada.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import RolEmpresa
from app.models.company import CompanyModel
from app.models.user import UserModel
from app.models.usuario_empresa import UsuarioEmpresaModel


async def _miembro(
    db_session: AsyncSession,
    empresa: CompanyModel,
    usuario: UserModel,
    rol: RolEmpresa,
) -> None:
    db_session.add(
        UsuarioEmpresaModel(
            user_id=usuario.id,
            company_id=empresa.id,
            rol=rol,
            activo=True,
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_viewer_no_puede_digitalizar(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
) -> None:
    await _miembro(db_session, test_company, usuario_de_prueba, RolEmpresa.VIEWER)
    headers = {"X-Company-ID": str(test_company.id)}

    respuesta = await async_client.post(
        "/api/v1/expenses/upload-batch",
        headers=headers,
        data={"company_id": str(test_company.id)},
        files={"files": ("ticket.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
    )
    # 403 y no 422: la guardia corre ANTES de que el endpoint mire el archivo.
    assert respuesta.status_code == 403, respuesta.text
    assert "crear" in respuesta.json()["detail"]


@pytest.mark.asyncio
async def test_admin_si_puede_digitalizar(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
) -> None:
    """Contraprueba: sin esto, el 403 anterior podria venir de otra parte."""
    await _miembro(db_session, test_company, usuario_de_prueba, RolEmpresa.ADMIN)
    respuesta = await async_client.post(
        "/api/v1/expenses/upload-batch",
        headers={"X-Company-ID": str(test_company.id)},
        data={"company_id": str(test_company.id)},
        files={"files": ("ticket.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
    )
    assert respuesta.status_code == 200, respuesta.text


@pytest.mark.asyncio
async def test_viewer_no_puede_exportar(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
) -> None:
    """Un PDF es una salida de datos, no una lectura inofensiva."""
    await _miembro(db_session, test_company, usuario_de_prueba, RolEmpresa.VIEWER)
    params = {"company_id": str(test_company.id), "periodo": "2026-10"}
    respuesta = await async_client.get(
        "/api/v1/reports/cierre-mensual.pdf",
        headers={"X-Company-ID": str(test_company.id)},
        params=params,
    )
    assert respuesta.status_code == 403, respuesta.text
    assert "exportar" in respuesta.json()["detail"]


@pytest.mark.asyncio
async def test_get_del_mismo_informe_si_es_lectura(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
) -> None:
    """El JSON y el PDF son la misma informacion: solo el PDF se lleva fuera."""
    await _miembro(db_session, test_company, usuario_de_prueba, RolEmpresa.VIEWER)
    respuesta = await async_client.get(
        "/api/v1/reports/cierre-mensual",
        headers={"X-Company-ID": str(test_company.id)},
        params={"company_id": str(test_company.id), "periodo": "2026-10"},
    )
    assert respuesta.status_code == 200, respuesta.text


@pytest.mark.asyncio
async def test_header_de_empresa_basura_no_salta_la_guardia(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
) -> None:
    """Un `X-Company-ID` invalido da 400, nunca 200 por no saber que empresa es."""
    await _miembro(db_session, test_company, usuario_de_prueba, RolEmpresa.VIEWER)
    respuesta = await async_client.post(
        "/api/v1/expenses/upload-batch",
        headers={"X-Company-ID": "no-es-un-uuid"},
        data={"company_id": str(test_company.id)},
        files={"files": ("ticket.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
    )
    assert respuesta.status_code == 400, respuesta.text


@pytest.mark.asyncio
async def test_viewer_no_puede_aprobar(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
) -> None:
    """Firmar un veredicto no es editar: es `aprobar`, y el token no basta."""
    await _miembro(db_session, test_company, usuario_de_prueba, RolEmpresa.VIEWER)
    ticket_id = uuid4()
    respuesta = await async_client.patch(
        f"/api/v1/tickets/{ticket_id}/review",
        headers={"X-Company-ID": str(test_company.id)},
        json={"aprobado": True},
    )
    assert respuesta.status_code == 403, respuesta.text
    assert "aprobar" in respuesta.json()["detail"]


@pytest.mark.asyncio
async def test_buscar_duplicados_por_post_es_consulta(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
) -> None:
    """Un POST que solo consulta no exige permiso de crear.

    La asercion es 200, no "distinto de 400": un 403 por una via equivocada
    tambien seria verde con una comprobacion debil, y es exactamente lo que
    dejo viva la mutacion que reclasifico esta ruta como escritura.
    """
    await _miembro(db_session, test_company, usuario_de_prueba, RolEmpresa.VIEWER)
    respuesta = await async_client.post(
        "/api/v1/duplicados/buscar",
        headers={"X-Company-ID": str(test_company.id)},
        json={
            "company_id": str(test_company.id),
            "provider_tax_id": "ABC010101ABC",
            "expense_date": "2026-10-01",
            "total_amount": "100.00",
        },
    )
    assert respuesta.status_code == 200, respuesta.text


@pytest.mark.asyncio
async def test_una_lectura_no_puede_nombrar_otra_empresa(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
) -> None:
    """El GET salia de la guardia sin mirar membersias: `?company_id=B` pasaba.

    No hace falta que la empresa B exista: la decision es de autoridad y se toma
    antes de que el endpoint consulte la base.
    """
    await _miembro(db_session, test_company, usuario_de_prueba, RolEmpresa.VIEWER)
    respuesta = await async_client.get(
        "/api/v1/tickets/",
        headers={"X-Company-ID": str(test_company.id)},
        params={"company_id": str(uuid4())},
    )
    assert respuesta.status_code == 409, respuesta.text


@pytest.mark.asyncio
async def test_la_misma_empresa_en_la_lectura_si_pasa(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
) -> None:
    """Contraprueba: el 409 anterior no puede venir de la guardia de rol."""
    await _miembro(db_session, test_company, usuario_de_prueba, RolEmpresa.VIEWER)
    respuesta = await async_client.get(
        "/api/v1/tickets/",
        headers={"X-Company-ID": str(test_company.id)},
        params={"company_id": str(test_company.id)},
    )
    assert respuesta.status_code == 200, respuesta.text


@pytest.mark.asyncio
async def test_una_escritura_no_puede_nombrar_otra_empresa(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
) -> None:
    """Antes el guardia miraba el rol y el endpoint creia el `company_id`."""
    await _miembro(db_session, test_company, usuario_de_prueba, RolEmpresa.ADMIN)
    respuesta = await async_client.post(
        "/api/v1/tickets/",
        headers={"X-Company-ID": str(test_company.id)},
        json={
            "company_id": str(uuid4()),
            "provider_name": "Proveedor de prueba",
            "provider_tax_id": "ABC010101ABC",
            "total_amount": "100.00",
            "subtotal": "100.00",
            "tax_amount": "0.00",
            "expense_date": "2026-10-01",
        },
    )
    assert respuesta.status_code == 409, respuesta.text


@pytest.mark.asyncio
async def test_un_formulario_no_puede_cambiar_la_empresa(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
) -> None:
    """La subida por lote trae `company_id` como campo de formulario, no JSON."""
    await _miembro(db_session, test_company, usuario_de_prueba, RolEmpresa.ADMIN)
    respuesta = await async_client.post(
        "/api/v1/expenses/upload-batch",
        headers={"X-Company-ID": str(test_company.id)},
        data={"company_id": str(uuid4())},
        files={"files": ("ticket.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
    )
    assert respuesta.status_code == 409, respuesta.text


@pytest.mark.asyncio
async def test_sin_header_y_varias_empresas_no_se_adivina(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
) -> None:
    """Con dos membresias, elegir por el `company_id` del cliente es adivinar."""
    await _miembro(db_session, test_company, usuario_de_prueba, RolEmpresa.ADMIN)
    otra = CompanyModel(name="Otra empresa", tax_id=f"OTRA{uuid4().hex[:7].upper()}")
    db_session.add(otra)
    await db_session.commit()
    await _miembro(db_session, otra, usuario_de_prueba, RolEmpresa.ADMIN)

    respuesta = await async_client.get(
        "/api/v1/tickets/", params={"company_id": str(otra.id)}
    )
    assert respuesta.status_code == 409, respuesta.text
