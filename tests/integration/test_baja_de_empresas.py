"""Desactivar y eliminar empresas por HTTP, y parar el trabajo sin activa.

Tres premisas del producto, cada una con su contraprueba:

1. Una empresa con datos NO se elimina: se desactiva, conservando la evidencia.
2. El sistema no se queda sin empresa activa mientras haya datos de trabajo.
3. Una empresa ANULADA no procesa, pero SI se consulta: el front lo dice con
   `AvisoSinEmpresa` y el servidor responde 403 con `SIN_EMPRESA_ANULADA` a
   cualquier escritura. Las lecturas siguen sirviendo la evidencia.
   Seguir listando, creando y activando empresas es la via de salida.
"""

import json
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select
from starlette.requests import Request

from app.core.deps import (
    SIN_EMPRESA_ACTIVA,
    SIN_EMPRESA_ANULADA,
    empresa_activa,
    guardia_de_permisos,
)
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
    # `POST /companies/` ya da membresia al creador, asi que la fila suele
    # existir. Reutilizarla es el mismo criterio que `asignar_membresia`: la PK
    # compuesta es la identidad de la relacion, no un INSERT nuevo.
    fila = await db_session.get(
        UsuarioEmpresaModel, (usuario.id, UUID(company_id))
    )
    if fila is None:
        fila = UsuarioEmpresaModel(
            user_id=usuario.id, company_id=UUID(company_id)
        )
        db_session.add(fila)
    fila.rol = rol
    fila.activo = True
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
    async def test_el_boton_anular_sale_del_servicio_no_de_un_conteo_del_front(
        self, async_client, db_session, usuario_de_prueba
    ):
        con_datos = await _crear_empresa(async_client, "Con datos", "BAJA001C")
        vacia = await _crear_empresa(async_client, "Vacia", "BAJA001V")
        await _membresia(db_session, usuario_de_prueba, con_datos)
        await _membresia(db_session, usuario_de_prueba, vacia)
        await _movimiento(db_session, con_datos)

        respuesta = await async_client.get(
            "/api/v1/companies/", params={"incluir_inactivas": True}
        )
        assert respuesta.status_code == 200
        banderas = {c["id"]: c["puede_eliminarse"] for c in respuesta.json()}
        # Con datos: Anular. Sin datos: Eliminar (siempre que quede otra activa).
        assert banderas[con_datos] is False
        assert banderas[vacia] is True

        # El cerrojo sigue en el servicio aunque el cliente reenvie un body viejo.
        assert (await async_client.post(
            f"/api/v1/companies/{con_datos}/eliminar"
        )).status_code == 409

    @pytest.mark.asyncio
    async def test_anular_guarda_el_motivo_y_activar_lo_limpia(
        self, async_client, db_session, usuario_de_prueba
    ):
        primera = await _crear_empresa(async_client, "Con nota", "BAJA001N")
        segunda = await _crear_empresa(async_client, "Otra", "BAJA001O")
        await _membresia(db_session, usuario_de_prueba, primera)
        await _membresia(db_session, usuario_de_prueba, segunda)
        await _movimiento(db_session, primera)

        baja = await async_client.post(
            f"/api/v1/companies/{primera}/desactivar",
            json={"motivo": "Cierre del ejercicio"},
        )
        assert baja.status_code == 200
        assert baja.json()["motivo_baja"] == "Cierre del ejercicio"
        assert baja.json()["bajada_por"] == usuario_de_prueba.email
        assert baja.json()["bajada_en"] is not None

        empresa = await db_session.get(CompanyModel, UUID(primera))
        assert empresa.motivo_baja == "Cierre del ejercicio"
        await db_session.refresh(empresa)

        filas = (await db_session.scalars(
            select(Auditoria).where(
                Auditoria.registro_afectado == UUID(primera),
                Auditoria.accion == "update",
            )
        )).all()
        assert len(filas) == 1
        cambios = json.loads(filas[0].detalles)["cambios"]
        assert any(c["campo"] == "motivo_baja" and c["despues"] == "Cierre del ejercicio"
                   for c in cambios)

        alta = await async_client.post(f"/api/v1/companies/{primera}/activar")
        assert alta.status_code == 200
        assert alta.json()["motivo_baja"] is None
        assert alta.json()["bajada_en"] is None
        assert alta.json()["bajada_por"] is None

        await db_session.refresh(empresa)
        assert empresa.motivo_baja is None
        # La nota se limpia de la fila, pero NO se borra del rastro.
        filas = (await db_session.scalars(
            select(Auditoria).where(
                Auditoria.registro_afectado == UUID(primera),
                Auditoria.accion == "update",
            )
        )).all()
        assert len(filas) == 2

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
    async def test_la_anulada_sigue_en_la_lista_para_explicar_el_motivo(
        self, async_client, db_session, usuario_de_prueba
    ):
        primera = await _crear_empresa(async_client, "Primera", "BAJA004")
        segunda = await _crear_empresa(async_client, "Segunda", "BAJA005")
        await _membresia(db_session, usuario_de_prueba, primera)
        await _membresia(db_session, usuario_de_prueba, segunda)
        await _movimiento(db_session, primera)

        await async_client.post(
            f"/api/v1/companies/{primera}/desactivar",
            json={"motivo": "Cierre del ejercicio"},
        )
        respuesta = await async_client.get("/api/v1/auth/empresas")
        assert respuesta.status_code == 200
        por_id = {m["company_id"]: m for m in respuesta.json()}
        assert primera in por_id and segunda in por_id
        # Si desapareciera, el front no podria decir POR QUE no digitaliza.
        assert por_id[primera]["estado"] == "inactivo"
        assert por_id[primera]["motivo_baja"] == "Cierre del ejercicio"
        assert por_id[primera]["permisos"] == ["leer"]
        assert por_id[segunda]["estado"] == "activo"

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
        # Consultar la evidencia sigue permitido...
        consulta = await async_client.get(
            "/api/v1/caja-chica/", headers={"X-Company-ID": primera}
        )
        assert consulta.status_code == 200

        # ...pero abrir una caja NO. La misma dependencia decide segun la accion.
        respuesta = await async_client.post(
            "/api/v1/caja-chica/",
            json={"nombre": "Caja nueva"},
            headers={"X-Company-ID": primera},
        )
        assert respuesta.status_code == 403
        assert respuesta.json()["detail"] == SIN_EMPRESA_ANULADA

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
        # Anulada se ve (para explicar el motivo); eliminada desaparece.
        assert [m["estado"] for m in (await async_client.get("/api/v1/auth/empresas")).json()] == ["inactivo"]
        assert (await async_client.post(
            f"/api/v1/companies/{company_id}/eliminar"
        )).status_code == 200
        assert (await async_client.get("/api/v1/auth/empresas")).json() == []
        # Eliminada no es anulada: ni siquiera se consulta.
        consulta = await async_client.get(
            "/api/v1/dashboard/", headers={"X-Company-ID": company_id}
        )
        assert consulta.status_code == 403

    @pytest.mark.asyncio
    async def test_no_se_digitaliza_pero_si_se_consulta(
        self, async_client, db_session, usuario_de_prueba
    ):
        """Anulada: las escrituras paran con motivo; las lecturas siguen."""
        company_id = await _crear_empresa(async_client, "Unica", "BAJA015")
        await _membresia(db_session, usuario_de_prueba, company_id)
        assert (await async_client.post(
            f"/api/v1/companies/{company_id}/desactivar",
            json={"motivo": "Prueba de anulacion"},
        )).status_code == 200
        cabecera = {"X-Company-ID": company_id}

        # Escaneo de carpeta, sin company_id: no hay empresa activa que inventar.
        escanear = await async_client.post("/api/v1/scan/", json={})
        assert escanear.status_code == 403
        assert escanear.json()["detail"] == SIN_EMPRESA_ACTIVA

        # Las dos puertas explicitas de digitalizacion SI nombran la anulada:
        # el motivo tiene que ser el de la empresa elegida, no el generico.
        lote = await async_client.post(
            "/api/v1/expenses/upload-batch",
            files={"files": ("ticket.pdf", b"%PDF-1.4 prueba", "application/pdf")},
            data={"company_id": company_id},
            headers=cabecera,
        )
        assert lote.status_code == 403
        assert lote.json()["detail"] == SIN_EMPRESA_ANULADA

        directorio = await async_client.post(
            "/api/v1/expenses/digitalizar-directorio",
            data={"company_id": company_id},
            headers=cabecera,
        )
        assert directorio.status_code == 403
        assert directorio.json()["detail"] == SIN_EMPRESA_ANULADA

        # Conciliar tambien es procesar, aunque el cuerpo traiga la empresa.
        conciliar = await async_client.post(
            "/api/v1/reconciliations/run",
            json={"company_id": company_id},
            headers=cabecera,
        )
        assert conciliar.status_code == 403
        assert conciliar.json()["detail"] == SIN_EMPRESA_ANULADA

        # Un PDF es SALIDA de datos, no una consulta: tampoco se genera.
        pdf = await async_client.get(
            "/api/v1/reports/cierre-mensual.pdf",
            params={"company_id": company_id, "periodo": "2026-11"},
            headers=cabecera,
        )
        assert pdf.status_code == 403
        assert pdf.json()["detail"] == SIN_EMPRESA_ANULADA

        # La evidencia sigue consultable, incluida la caja que usa EmpresaActiva.
        for ruta, parametros in (
            ("/api/v1/dashboard/", None),
            ("/api/v1/tickets/", None),
            (
                "/api/v1/reports/cierre-mensual",
                {"company_id": company_id, "periodo": "2026-11"},
            ),
            ("/api/v1/caja-chica/", None),
        ):
            respuesta = await async_client.get(ruta, params=parametros, headers=cabecera)
            assert respuesta.status_code == 200, (ruta, respuesta.text)

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

        assert [m["estado"] for m in (await async_client.get("/api/v1/auth/empresas")).json()] == ["inactivo"]
        assert (await async_client.get("/api/v1/companies/")).status_code == 200
        assert (await async_client.get(f"/api/v1/companies/{company_id}")).status_code == 200

        activar = await async_client.post(f"/api/v1/companies/{company_id}/activar")
        assert activar.status_code == 200
        assert activar.json()["estado"] == "activo"
        assert (await async_client.get("/api/v1/dashboard/")).status_code == 200


    @pytest.mark.asyncio
    async def test_la_recuperacion_sigue_abierta_con_la_unica_eliminada(
        self, async_client, db_session, usuario_de_prueba
    ):
        """`_es_recuperacion` se prueba con la empresa ELIMINADA, no con la anulada.

        Con una anulada hay membresia consultable y la guardia ni entra en la
        rama de recuperacion: un test ahi daba verde con el cerrojo quitado.
        """
        company_id = await _crear_empresa(async_client, "Unica", "BAJA019")
        await _membresia(db_session, usuario_de_prueba, company_id)
        assert (await async_client.post(
            f"/api/v1/companies/{company_id}/eliminar"
        )).status_code == 200

        # Listar y ver el detalle son la via de salida; tienen que responder.
        assert (await async_client.get("/api/v1/companies/")).status_code == 200
        assert (await async_client.get(
            f"/api/v1/companies/{company_id}"
        )).status_code == 200
        # Activar SI se alcanza: el 409 lo pone el servicio, no la guardia.
        assert (await async_client.post(
            f"/api/v1/companies/{company_id}/activar"
        )).status_code == 409


class TestLasDosPuertasSePruebanAisladas:
    """La guardia y `empresa_activa` se tapan entre si por HTTP.

    Si solo se prueban con `async_client`, quitar una defensa no cambia el
    veredicto: la otra sigue devolviendo el 403. Estas dos llamadas directas
    son las que hacen que cada mutacion muera por su motivo.
    """

    @pytest.mark.asyncio
    async def test_la_guardia_para_a_quien_solo_tiene_empresas_eliminadas(
        self, async_client, db_session, usuario_de_prueba
    ):
        company_id = await _crear_empresa(async_client, "Eliminada", "BAJA017")
        await _membresia(db_session, usuario_de_prueba, company_id)
        assert (await async_client.post(
            f"/api/v1/companies/{company_id}/eliminar"
        )).status_code == 200

        peticion = Request({
            "type": "http", "method": "GET", "path": "/api/v1/dashboard/",
            "headers": [], "query_string": b"",
        })
        with pytest.raises(HTTPException) as error:
            await guardia_de_permisos(peticion, usuario_de_prueba, db_session)
        assert error.value.status_code == 403

    @pytest.mark.asyncio
    async def test_empresa_activa_rechaza_una_anulada_para_escribir(
        self, async_client, db_session, usuario_de_prueba
    ):
        company_id = await _crear_empresa(async_client, "Anulada", "BAJA018")
        otra = await _crear_empresa(async_client, "Otra", "BAJA018B")
        await _membresia(db_session, usuario_de_prueba, company_id)
        await _membresia(db_session, usuario_de_prueba, otra)
        await _movimiento(db_session, company_id)
        assert (await async_client.post(
            f"/api/v1/companies/{company_id}/desactivar",
            json={"motivo": "Prueba de la dependencia"},
        )).status_code == 200

        peticion = Request({
            "type": "http", "method": "POST", "path": "/api/v1/caja-chica/",
            "headers": [(b"x-company-id", company_id.encode())],
            "query_string": b"",
        })
        with pytest.raises(HTTPException) as error:
            await empresa_activa(
                peticion, usuario_de_prueba, db_session, x_company_id=company_id
            )
        assert error.value.status_code == 403
        assert error.value.detail == SIN_EMPRESA_ANULADA
