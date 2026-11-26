"""El catalogo de cuentas, medido por HTTP y no solo en el servicio.

El defecto que cerraba esta incidencia no era de datos sino de cableado: la
migracion, el modelo y el servicio existian, pero el router nunca se monto y
`Tickets.tsx` seguia escribiendo solo `tickets.category`. Un ticket podia tener
una etiqueta visible y ninguna cuenta asociada. Aqui se mide el recorrido: la
ruta responde, la siembra es idempotente y la cuenta se escribe con su texto en
la MISMA fila.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import select

from app.core.categorias import CATEGORIAS
from app.core.enums import RolEmpresa
from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.models.tipo_gasto import TipoGastoModel
from app.models.usuario_empresa import UsuarioEmpresaModel

BASE = "/api/v1/tipos-gasto"


async def _membership(db_session, usuario, empresa, rol: RolEmpresa):
    db_session.add(
        UsuarioEmpresaModel(
            user_id=usuario.id,
            company_id=empresa.id,
            rol=rol.value,
            activo=True,
        )
    )
    await db_session.commit()


async def _ticket(db_session, company_id) -> TicketModel:
    ticket = TicketModel(
        company_id=company_id,
        provider_name="COMERCIO DE PRUEBA",
        total_amount=Decimal("100.00"),
        tax_amount=Decimal("0.00"),
        expense_date=date(2025, 1, 15),
        extraction_status="AUTO_APROBADO",
        source_type="image",
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)
    return ticket


@pytest.fixture
async def cliente_con_admin(async_client, db_session, usuario_de_prueba, test_company):
    await _membership(db_session, usuario_de_prueba, test_company, RolEmpresa.ADMIN)
    return async_client


class TestElCatalogoEstaMontado:
    async def test_la_ruta_ya_no_da_404(self, cliente_con_admin):
        respuesta = await cliente_con_admin.get(BASE)
        assert respuesta.status_code == 200
        assert respuesta.json() == []

    async def test_sembrar_es_idempotente_y_no_es_get(
        self, cliente_con_admin, db_session, test_company
    ):
        primera = await cliente_con_admin.post(f"{BASE}/sembrar")
        assert primera.status_code == 200
        assert primera.json()["anadidas"] == len(CATEGORIAS)

        segunda = await cliente_con_admin.post(f"{BASE}/sembrar")
        assert segunda.status_code == 200
        assert segunda.json()["anadidas"] == 0
        assert "ya estaba completo" in segunda.json()["mensaje"]

        filas = (await db_session.execute(select(TipoGastoModel))).scalars().all()
        assert len(filas) == len(CATEGORIAS)
        assert all(f.company_id == test_company.id for f in filas)

        # Un GET no puede sembrar: la guardia lo clasificaria como lectura y un
        # viewer escribiria el catalogo solo con abrir la pantalla.
        assert (await cliente_con_admin.get(f"{BASE}/sembrar")).status_code == 405

    async def test_la_lista_es_solo_de_la_empresa_activa(
        self, cliente_con_admin, db_session, test_company
    ):
        await cliente_con_admin.post(f"{BASE}/sembrar")
        ajena = CompanyModel(name="Otra", tax_id=f"OTRA{uuid.uuid4().hex[:9].upper()}")
        db_session.add(ajena)
        await db_session.commit()
        await db_session.refresh(ajena)
        db_session.add(
            TipoGastoModel(
                id=uuid.uuid4(),
                company_id=ajena.id,
                clave="AJENA",
                nombre="Cuenta ajena",
                activo=True,
            )
        )
        await db_session.commit()

        respuesta = await cliente_con_admin.get(BASE)
        assert respuesta.status_code == 200
        claves = {f["clave"] for f in respuesta.json()}
        assert "AJENA" not in claves
        assert all(f["company_id"] == str(test_company.id) for f in respuesta.json())


class TestLaCuentaDelGasto:
    async def test_asignar_escribe_id_y_texto_en_la_misma_operacion(
        self, cliente_con_admin, db_session, test_company
    ):
        await cliente_con_admin.post(f"{BASE}/sembrar")
        cuenta = (
            await db_session.execute(
                select(TipoGastoModel).where(TipoGastoModel.clave == "ALIMENTACION")
            )
        ).scalar_one()
        ticket = await _ticket(db_session, test_company.id)

        respuesta = await cliente_con_admin.put(
            f"{BASE}/tickets/{ticket.id}/cuenta",
            json={"tipo_gasto_id": str(cuenta.id)},
        )
        assert respuesta.status_code == 204

        await db_session.refresh(ticket)
        assert ticket.tipo_gasto_id == cuenta.id
        assert ticket.category == cuenta.clave

        # Quitar la cuenta tambien limpia el texto. Dejar la etiqueta de una
        # cuenta que ya no esta es el peor de los dos mundos.
        respuesta = await cliente_con_admin.put(
            f"{BASE}/tickets/{ticket.id}/cuenta",
            json={"tipo_gasto_id": None},
        )
        assert respuesta.status_code == 204
        await db_session.refresh(ticket)
        assert ticket.tipo_gasto_id is None
        assert ticket.category is None

    async def test_una_cuenta_de_otra_empresa_no_se_puede_asignar(
        self, cliente_con_admin, db_session, test_company
    ):
        await cliente_con_admin.post(f"{BASE}/sembrar")
        ajena = CompanyModel(name="Otra", tax_id=f"OTRA{uuid.uuid4().hex[:9].upper()}")
        db_session.add(ajena)
        await db_session.commit()
        await db_session.refresh(ajena)
        cuenta_ajena = TipoGastoModel(
            id=uuid.uuid4(),
            company_id=ajena.id,
            clave="AJENA",
            nombre="Cuenta ajena",
            activo=True,
        )
        db_session.add(cuenta_ajena)
        ticket = await _ticket(db_session, test_company.id)

        respuesta = await cliente_con_admin.put(
            f"{BASE}/tickets/{ticket.id}/cuenta",
            json={"tipo_gasto_id": str(cuenta_ajena.id)},
        )
        assert respuesta.status_code == 422
        await db_session.refresh(ticket)
        assert ticket.tipo_gasto_id is None
        assert ticket.category is None

    async def test_un_ticket_de_otra_empresa_no_se_puede_clasificar(
        self, cliente_con_admin, db_session, test_company
    ):
        await cliente_con_admin.post(f"{BASE}/sembrar")
        cuenta = (await db_session.execute(select(TipoGastoModel))).scalars().first()
        ajena = CompanyModel(name="Otra", tax_id=f"OTRA{uuid.uuid4().hex[:9].upper()}")
        db_session.add(ajena)
        await db_session.commit()
        await db_session.refresh(ajena)
        ticket_ajeno = await _ticket(db_session, ajena.id)

        respuesta = await cliente_con_admin.put(
            f"{BASE}/tickets/{ticket_ajeno.id}/cuenta",
            json={"tipo_gasto_id": str(cuenta.id)},
        )
        assert respuesta.status_code == 422
        await db_session.refresh(ticket_ajeno)
        assert ticket_ajeno.tipo_gasto_id is None


class TestLaAutorizacionNoEsCrear:
    async def test_operator_no_puede_autorizar(
        self, async_client, db_session, usuario_de_prueba, test_company
    ):
        await _membership(db_session, usuario_de_prueba, test_company, RolEmpresa.OPERATOR)
        ticket = await _ticket(db_session, test_company.id)

        respuesta = await async_client.post(
            f"{BASE}/tickets/{ticket.id}/autorizar", json={}
        )
        assert respuesta.status_code == 403

    async def test_viewer_no_puede_sembrar(
        self, async_client, db_session, usuario_de_prueba, test_company
    ):
        await _membership(db_session, usuario_de_prueba, test_company, RolEmpresa.VIEWER)

        respuesta = await async_client.post(f"{BASE}/sembrar")
        assert respuesta.status_code == 403

    async def test_rechazar_sin_motivo_no_toca_la_base(
        self, cliente_con_admin, db_session, test_company
    ):
        ticket = await _ticket(db_session, test_company.id)

        respuesta = await cliente_con_admin.post(
            f"{BASE}/tickets/{ticket.id}/rechazar", json={}
        )
        assert respuesta.status_code == 422
        await db_session.refresh(ticket)
        assert ticket.estado_autorizacion == "PENDIENTE"


class TestLaSiembraAlCrearEmpresa:
    async def test_una_empresa_nueva_nace_con_catalogo(
        self, cliente_con_admin, db_session
    ):
        respuesta = await cliente_con_admin.post(
            "/api/v1/companies/",
            json={"name": "Empresa nueva", "tax_id": f"NVA{uuid.uuid4().hex[:9].upper()}"},
        )
        assert respuesta.status_code == 201
        company_id = uuid.UUID(respuesta.json()["id"])

        filas = (
            await db_session.execute(
                select(TipoGastoModel).where(TipoGastoModel.company_id == company_id)
            )
        ).scalars().all()
        assert len(filas) == len(CATEGORIAS)
        assert {f.clave for f in filas} == {c.clave for c in CATEGORIAS}


class TestLaClasificacionPorLote:
    async def test_el_lote_escribe_id_y_texto_en_la_misma_operacion(
        self, cliente_con_admin, db_session, test_company
    ):
        await cliente_con_admin.post(f"{BASE}/sembrar")
        cuenta = (
            await db_session.execute(
                select(TipoGastoModel).where(TipoGastoModel.clave == "ALIMENTACION")
            )
        ).scalar_one()
        primero = await _ticket(db_session, test_company.id)
        segundo = await _ticket(db_session, test_company.id)

        respuesta = await cliente_con_admin.put(
            f"{BASE}/tickets/cuenta",
            json={
                "ticket_ids": [str(primero.id), str(segundo.id)],
                "tipo_gasto_id": str(cuenta.id),
            },
        )
        assert respuesta.status_code == 200
        assert respuesta.json() == {"actualizados": 2, "pedidos": 2}

        for ticket in (primero, segundo):
            await db_session.refresh(ticket)
            assert ticket.tipo_gasto_id == cuenta.id
            assert ticket.category == cuenta.clave

    async def test_un_id_de_otra_empresa_no_cuenta_como_clasificado(
        self, cliente_con_admin, db_session, test_company
    ):
        await cliente_con_admin.post(f"{BASE}/sembrar")
        cuenta = (await db_session.execute(select(TipoGastoModel))).scalars().first()
        propio = await _ticket(db_session, test_company.id)
        ajena = CompanyModel(name="Otra", tax_id=f"OTRA{uuid.uuid4().hex[:9].upper()}")
        db_session.add(ajena)
        await db_session.commit()
        await db_session.refresh(ajena)
        ajeno = await _ticket(db_session, ajena.id)

        respuesta = await cliente_con_admin.put(
            f"{BASE}/tickets/cuenta",
            json={
                "ticket_ids": [str(propio.id), str(ajeno.id)],
                "tipo_gasto_id": str(cuenta.id),
            },
        )
        assert respuesta.status_code == 200
        assert respuesta.json() == {"actualizados": 1, "pedidos": 2}
        await db_session.refresh(ajeno)
        assert ajeno.tipo_gasto_id is None
        assert ajeno.category is None

    async def test_quitar_la_cuenta_del_lote_limpia_el_texto(
        self, cliente_con_admin, db_session, test_company
    ):
        await cliente_con_admin.post(f"{BASE}/sembrar")
        cuenta = (await db_session.execute(select(TipoGastoModel))).scalars().first()
        ticket = await _ticket(db_session, test_company.id)
        await cliente_con_admin.put(
            f"{BASE}/tickets/{ticket.id}/cuenta",
            json={"tipo_gasto_id": str(cuenta.id)},
        )

        respuesta = await cliente_con_admin.put(
            f"{BASE}/tickets/cuenta",
            json={"ticket_ids": [str(ticket.id)], "tipo_gasto_id": None},
        )
        assert respuesta.status_code == 200
        await db_session.refresh(ticket)
        assert ticket.tipo_gasto_id is None
        assert ticket.category is None

    async def test_viewer_no_puede_clasificar_por_lote(
        self, async_client, db_session, usuario_de_prueba, test_company
    ):
        await _membership(db_session, usuario_de_prueba, test_company, RolEmpresa.VIEWER)
        ticket = await _ticket(db_session, test_company.id)

        respuesta = await async_client.put(
            f"{BASE}/tickets/cuenta",
            json={"ticket_ids": [str(ticket.id)], "tipo_gasto_id": None},
        )
        assert respuesta.status_code == 403
