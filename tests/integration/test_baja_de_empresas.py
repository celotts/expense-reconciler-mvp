"""Desactivar y eliminar empresas por HTTP, y parar el trabajo sin activa.

Tres premisas del producto, cada una con su contraprueba:

1. Una empresa con datos NO se elimina: se desactiva, conservando la evidencia.
2. El sistema no se queda sin empresa activa mientras haya datos de trabajo.
3. Sin empresa activa NO se procesa nada (ni se consulta): el front lo dice con
   `AvisoSinEmpresa`, y el servidor responde 403 con `SIN_EMPRESA_ACTIVA`.
   Seguir listando, creando y activando empresas es la via de salida.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.deps import SIN_EMPRESA_ACTIVA
from app.core.enums import EstadoEmpresa
from app.models.auditoria import Auditoria
from app.models.bank_transaction import BankTransactionModel
from app.models.company import CompanyModel
from app.models.usuario_empresa import UsuarioEmpresaModel


async def _crear_empresa(async_client: AsyncClient, nombre: str, rfc: str) -> str:
    respuesta = await async_client.post(
        "/api/v1/companies/", json={"name": nombre, "tax_id": rfc}
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()["id"]


async def _membresia(db_session, usuario, company_id: str, rol: str = "admin") -> None:
    db_session.add(
        UsuarioEmpresaModel(
            user_id=usuario.id, company_id=UUID(company_id), rol=rol, activo=True
        )
    )
    await db_session.commit()


async def _movimiento(db_session, company_id: str) -> None:
    db_session.add(
        BankTransactionModel(
            company_id=UUID(company_id),
            transaction_date=date(2026, 1, 15),
            amount=Decimal("-125.50"),
            description="Pago de prueba",
        )
    )
    await db_session.commit()


class TestDesactivarYEliminar:
    @pytest.mark.asyncio
    async def test_con_movimiento_no_se_elimina_y_se_desactiva(
        self, async_client, db_session, usuario_de_prueba
    ):
        company_id = await _crear_empresa(async_client, "Con datos", "BAJA001")
        otra = await _crear_empresa(async_client, "Otra activa", "BAJA001B")
        await _membresia(db_session, usuario_de_prueba, company_id)
        await _membresia(db_session, usuario_de_prueba, otra)
        await _movimiento(db_session, company_id)

        borrar = await async_client.delete(f"/api/v1/companies/{company_id}")
        assert borrar.status_code == 409
        assert "no se puede eliminar" in borrar.json()["detail"]

        eliminar = await async_client.post(f"/api/v1/companies/{company_id}/eliminar")
        assert eliminar.status_code == 409

        baja = await async_client.post(f"/api/v1/companies/{company_id}/desactivar")
        assert baja.status_code == 200
        assert baja.json()["estado"] == "inactivo"

        # La fila y su movimiento siguen en la base: la baja no borra evidencia.
        empresa = await db_session.get(CompanyModel, UUID(company_id))
        assert empresa is not None and empresa.estado == EstadoEmpresa.INACTIVO.value
        movimientos = await db_session.scalar(
            select(BankTransactionModel.company_id).where(
                BankTransactionModel.company_id == UUID(company_id)
            ).limit(1)
        )
        assert movimientos is not None

    @pytest.mark.asyncio
    async def test_soft_delete_no_borra_la_fila(
        self, async_client, db_session, usuario_de_prueba
    ):
        company_id = await _crear_empresa(async_client, "Vacia", "BAJA002")
        otra = await _crear_empresa(async_client, "Otra", "BAJA002B")
        await _membresia(db_session, usuario_de_prueba, company_id)
        await _membresia(db_session, usuario_de_prueba, otra)

        respuesta = await async_client.post(f"/api/v1/companies/{company_id}/eliminar")
        assert respuesta.status_code == 200
        assert respuesta.json()["estado"] == "eliminado"
        empresa = await db_session.get(CompanyModel, UUID(company_id))
        assert empresa is not None and empresa.estado == EstadoEmpresa.ELIMINADO.value

        # Un borrado logico no es una pausa: no se reactiva.
        reactivar = await async_client.post(f"/api/v1/companies/{company_id}/activar")
        assert reactivar.status_code == 409

    @pytest.mark.asyncio
    async def test_una_desactivada_si_se_reactiva(
        self, async_client, db_session, usuario_de_prueba
    ):
        primera = await _crear_empresa(async_client, "Primera", "BAJA003")
        segunda = await _crear_empresa(async_client, "Segunda", "BAJA003B")
        await _membresia(db_session, usuario_de_prueba, primera)
        await _membresia(db_session, usuario_de_prueba, segunda)

        assert (await async_client.post(
            f"/api/v1/companies/{primera}/desactivar"
        )).status_code == 200
        respuesta = await async_client.post(f"/api/v1/companies/{primera}/activar")
        assert respuesta.status_code == 200
        assert respuesta.json()["estado"] == "activo"

    @pytest.mark.asyncio
    async def test_la_baja_sale_de_la_lista_de_empresas_del_usuario(
        self, async_client, db_session, usuario_de_prueba
    ):
        primera = await _crear_empresa(async_client, "Primera", "BAJA004")
        segunda = await _crear_empresa(async_client, "Segunda", "BAJA005")
        await _membresia(db_session, usuario_de_prueba, primera)
        await _membresia(db_session, usuario_de_prueba, segunda)
        await _movimiento(db_session, primera)

        await async_client.post(f"/api/v1/companies/{primera}/desactivar")
        respuesta = await async_client.get("/api/v1/auth/empresas")
        assert respuesta.status_code == 200
        ids = [m["company_id"] for m in respuesta.json()]
        assert primera not in ids
        assert segunda in ids
        assert all(m["estado"] == "activo" for m in respuesta.json())

    @pytest.mark.asyncio
    async def test_el_listado_solo_muestra_activas_por_omision(
        self, async_client, db_session, usuario_de_prueba
    ):
        primera = await _crear_empresa(async_client, "Primera", "BAJA006")
        segunda = await _crear_empresa(async_client, "Segunda", "BAJA006B")
        await _membresia(db_session, usuario_de_prueba, primera)
        await _membresia(db_session, usuario_de_prueba, segunda)
        await async_client.post(f"/api/v1/companies/{primera}/desactivar")

        visibles = await async_client.get("/api/v1/companies/")
        assert [c["id"] for c in visibles.json()] == [segunda]

        todas = await async_client.get(
            "/api/v1/companies/", params={"incluir_inactivas": True}
        )
        assert {c["id"] for c in todas.json()} == {primera, segunda}
        estados = {c["id"]: c["estado"] for c in todas.json()}
        assert estados[primera] == "inactivo"

    @pytest.mark.asyncio
    async def test_una_empresa_bajada_no_autoriza_escrituras(
        self, async_client, db_session, usuario_de_prueba
    ):
        primera = await _crear_empresa(async_client, "Primera", "BAJA007")
        segunda = await _crear_empresa(async_client, "Segunda", "BAJA008")
        await _membresia(db_session, usuario_de_prueba, primera)
        await _membresia(db_session, usuario_de_prueba, segunda)
        await _movimiento(db_session, primera)

        await async_client.post(f"/api/v1/companies/{primera}/desactivar")
        respuesta = await async_client.get(
            "/api/v1/caja-chica/", headers={"X-Company-ID": primera}
        )
        assert respuesta.status_code == 403

    @pytest.mark.asyncio
    async def test_ultima_activa_con_datos_no_se_da_de_baja(
        self, async_client, db_session, usuario_de_prueba
    ):
        company_id = await _crear_empresa(async_client, "Unica", "BAJA009")
        await _membresia(db_session, usuario_de_prueba, company_id)
        await _movimiento(db_session, company_id)

        respuesta = await async_client.post(
            f"/api/v1/companies/{company_id}/desactivar"
        )
        assert respuesta.status_code == 409
        assert "ninguna empresa activa" in respuesta.json()["detail"]
        empresa = await db_session.get(CompanyModel, UUID(company_id))
        assert empresa.estado == EstadoEmpresa.ACTIVO.value

    @pytest.mark.asyncio
    async def test_la_baja_es_idempotente_y_se_audita_una_sola_vez(
        self, async_client, db_session, usuario_de_prueba
    ):
        primera = await _crear_empresa(async_client, "Primera", "BAJA010")
        segunda = await _crear_empresa(async_client, "Segunda", "BAJA011")
        await _membresia(db_session, usuario_de_prueba, primera)
        await _membresia(db_session, usuario_de_prueba, segunda)
        await _movimiento(db_session, primera)

        for _ in range(2):
            respuesta = await async_client.post(
                f"/api/v1/companies/{primera}/desactivar"
            )
            assert respuesta.status_code == 200

        filas = (await db_session.scalars(
            select(Auditoria).where(
                Auditoria.registro_afectado == UUID(primera),
                Auditoria.accion == "update",
            )
        )).all()
        assert len(filas) == 1
        assert filas[0].usuario_id == usuario_de_prueba.id

    @pytest.mark.asyncio
    async def test_viewer_no_puede_desactivar(
        self, async_client, db_session, usuario_de_prueba
    ):
        primera = await _crear_empresa(async_client, "Primera", "BAJA012")
        segunda = await _crear_empresa(async_client, "Segunda", "BAJA013")
        await _membresia(db_session, usuario_de_prueba, primera, rol="viewer")
        await _membresia(db_session, usuario_de_prueba, segunda, rol="viewer")

        respuesta = await async_client.post(
            f"/api/v1/companies/{primera}/desactivar"
        )
        assert respuesta.status_code == 403


class TestSinEmpresaActiva:
    @pytest.mark.asyncio
    async def test_sin_datos_si_se_puede_bajar_la_unica(
        self, async_client, db_session, usuario_de_prueba
    ):
        company_id = await _crear_empresa(async_client, "Unica vacia", "BAJA014")
        await _membresia(db_session, usuario_de_prueba, company_id)
        respuesta = await async_client.post(
            f"/api/v1/companies/{company_id}/desactivar"
        )
        assert respuesta.status_code == 200
        assert (await async_client.get("/api/v1/auth/empresas")).json() == []

    @pytest.mark.asyncio
    async def test_no_se_digitaliza_ni_se_consulta_nada(
        self, async_client, db_session, usuario_de_prueba
    ):
        """La unica empresa cerrada para TODAS las puertas de trabajo."""
        company_id = await _crear_empresa(async_client, "Unica", "BAJA015")
        await _membresia(db_session, usuario_de_prueba, company_id)
        assert (await async_client.post(
            f"/api/v1/companies/{company_id}/desactivar"
        )).status_code == 200

        # Escaneo de carpeta, sin company_id: tampoco inventaria archivos.
        escanear = await async_client.post("/api/v1/scan/", json={})
        assert escanear.status_code == 403
        assert escanear.json()["detail"] == SIN_EMPRESA_ACTIVA

        # Las dos puertas explicitas de digitalizacion.
        lote = await async_client.post(
            "/api/v1/expenses/upload-batch",
            files={"files": ("ticket.pdf", b"%PDF-1.4 prueba", "application/pdf")},
            data={"company_id": company_id},
        )
        assert lote.status_code == 403
        assert lote.json()["detail"] == SIN_EMPRESA_ACTIVA

        directorio = await async_client.post(
            "/api/v1/expenses/digitalizar-directorio",
            data={"company_id": company_id},
        )
        assert directorio.status_code == 403
        assert directorio.json()["detail"] == SIN_EMPRESA_ACTIVA

        # Y las demas opciones: tablero, tickets, informe, caja.
        for ruta in (
            "/api/v1/dashboard/",
            "/api/v1/tickets/",
            "/api/v1/reports/cierre-mensual",
            "/api/v1/caja-chica/",
        ):
            respuesta = await async_client.get(ruta)
            assert respuesta.status_code == 403, (ruta, respuesta.text)
            assert respuesta.json()["detail"] == SIN_EMPRESA_ACTIVA

    @pytest.mark.asyncio
    async def test_la_recuperacion_sigue_abierta(
        self, async_client, db_session, usuario_de_prueba
    ):
        """Cerrar la unica empresa no puede dejar sin forma de volver a abrirla."""
        company_id = await _crear_empresa(async_client, "Unica", "BAJA016")
        await _membresia(db_session, usuario_de_prueba, company_id)
        assert (await async_client.post(
            f"/api/v1/companies/{company_id}/desactivar"
        )).status_code == 200

        assert (await async_client.get("/api/v1/auth/empresas")).json() == []
        assert (await async_client.get("/api/v1/companies/")).status_code == 200
        assert (await async_client.get(f"/api/v1/companies/{company_id}")).status_code == 200

        activar = await async_client.post(f"/api/v1/companies/{company_id}/activar")
        assert activar.status_code == 200
        assert activar.json()["estado"] == "activo"
        assert (await async_client.get("/api/v1/dashboard/")).status_code == 200
