"""Empresa activa y alcance de `operator` en los endpoints de caja.

Las pruebas de `test_roles_y_empresa_activa.py` demuestran que las dependencias
deciden bien. Estas demuestran que los endpoints de caja y duplicados LAS USAN,
que es una pregunta distinta: una dependencia perfecta que nadie invoca es una
defensa decorativa.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import hashear_contrasena
from app.models.company import CompanyModel
from app.models.usuario_empresa import UsuarioEmpresaModel
from app.models.user import UserModel


@pytest.fixture(autouse=True)
async def membresia_de_admin(db_session, test_company, usuario_de_prueba):
    db_session.add(
        UsuarioEmpresaModel(
            user_id=usuario_de_prueba.id,
            company_id=test_company.id,
            rol="admin",
            activo=True,
        )
    )
    await db_session.commit()


async def _crear_caja(cliente, company_id, responsable_id):
    r = await cliente.post(
        "/api/v1/caja-chica/",
        json={
            "company_id": str(company_id),
            "responsable_id": str(responsable_id),
            "nombre": "Caja de roles",
            "monto_inicial": "500.00",
            "umbral_alerta": "0.00",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _otra_empresa(db_session, usuario, rol="admin"):
    company = CompanyModel(name="Empresa ajena", tax_id=f"AJ{uuid4().hex[:10].upper()}")
    db_session.add(company)
    await db_session.flush()
    db_session.add(
        UsuarioEmpresaModel(
            user_id=usuario.id, company_id=company.id, rol=rol, activo=True
        )
    )
    await db_session.commit()
    await db_session.refresh(company)
    return company


async def _cambiar_rol(db_session, usuario, company, rol):
    fila = (
        await db_session.execute(
            select(UsuarioEmpresaModel).where(
                UsuarioEmpresaModel.user_id == usuario.id,
                UsuarioEmpresaModel.company_id == company.id,
            )
        )
    ).scalar_one()
    fila.rol = rol
    db_session.add(fila)
    await db_session.commit()


async def _otro_usuario(db_session):
    usuario = UserModel(
        email=f"otro{uuid4().hex[:8]}@example.com",
        nombre="Otro responsable",
        password_hash=hashear_contrasena("contrasena-de-prueba"),
    )
    db_session.add(usuario)
    await db_session.commit()
    await db_session.refresh(usuario)
    return usuario


class TestEmpresaEnCaja:
    async def test_caja_de_otra_empresa_da_404(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id
        )
        ajena = await _otra_empresa(db_session, usuario_de_prueba)

        r = await async_client.get(
            f"/api/v1/caja-chica/{caja['id']}",
            headers={"X-Company-ID": str(ajena.id)},
        )
        # 404 y no 403: un 403 confirmaria que la caja existe en la otra empresa.
        assert r.status_code == 404

    async def test_listado_no_cruza_empresas(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        await _crear_caja(async_client, test_company.id, usuario_de_prueba.id)
        ajena = await _otra_empresa(db_session, usuario_de_prueba)

        propias = await async_client.get(
            "/api/v1/caja-chica/",
            headers={"X-Company-ID": str(test_company.id)},
        )
        assert propias.status_code == 200
        assert len(propias.json()) == 1

        de_la_ajena = await async_client.get(
            "/api/v1/caja-chica/", headers={"X-Company-ID": str(ajena.id)}
        )
        assert de_la_ajena.status_code == 200
        assert de_la_ajena.json() == []

    async def test_cuerpo_con_otra_empresa_da_409(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        r = await async_client.post(
            "/api/v1/caja-chica/",
            json={
                "company_id": str(uuid4()),
                "responsable_id": str(usuario_de_prueba.id),
                "nombre": "Caja ajena",
                "monto_inicial": "100.00",
                "umbral_alerta": "0.00",
            },
        )
        assert r.status_code == 409

    async def test_filtro_por_otra_empresa_da_409(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        r = await async_client.get(
            "/api/v1/caja-chica/", params={"company_id": str(uuid4())}
        )
        assert r.status_code == 409

    async def test_dos_empresas_sin_elegir_da_409_en_caja(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        await _otra_empresa(db_session, usuario_de_prueba)
        r = await async_client.get("/api/v1/caja-chica/")
        assert r.status_code == 409
        assert "X-Company-ID" in r.json()["detail"]

    async def test_duplicados_rechaza_empresa_del_cuerpo(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        r = await async_client.post(
            "/api/v1/duplicados/buscar",
            json={
                "company_id": str(uuid4()),
                "provider_tax_id": "AAA010101AAA",
                "folio": "F-1",
                "expense_date": "2026-11-20",
                "total_amount": "100.00",
            },
        )
        assert r.status_code == 409

    async def test_resumen_rechaza_filtro_ajeno(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        r = await async_client.get(
            "/api/v1/duplicados/resumen", params={"company_id": str(uuid4())}
        )
        assert r.status_code == 409


class TestAlcanceDelOperador:
    async def test_operator_no_ve_la_caja_de_otro(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        otro = await _otro_usuario(db_session)
        caja = await _crear_caja(async_client, test_company.id, otro.id)
        await _cambiar_rol(db_session, usuario_de_prueba, test_company, "operator")

        assert (
            await async_client.get(f"/api/v1/caja-chica/{caja['id']}")
        ).status_code == 403
        assert (
            await async_client.get(f"/api/v1/caja-chica/{caja['id']}/movimientos")
        ).status_code == 403
        assert (
            await async_client.post(
                f"/api/v1/caja-chica/{caja['id']}/movimientos",
                json={
                    "tipo": "reposicion",
                    "monto": "10.00",
                    "origen_fondo": "efectivo",
                },
            )
        ).status_code == 403
        assert (
            await async_client.patch(
                f"/api/v1/caja-chica/{caja['id']}", json={"nombre": "Otro nombre"}
            )
        ).status_code == 403
        assert (
            await async_client.post(f"/api/v1/caja-chica/{caja['id']}/en-revision")
        ).status_code == 403

    async def test_operator_si_opera_su_caja(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id
        )
        await _cambiar_rol(db_session, usuario_de_prueba, test_company, "operator")

        assert (
            await async_client.get(f"/api/v1/caja-chica/{caja['id']}")
        ).status_code == 200
        movimiento = await async_client.post(
            f"/api/v1/caja-chica/{caja['id']}/movimientos",
            json={
                "tipo": "reposicion",
                "monto": "10.00",
                "origen_fondo": "efectivo",
            },
        )
        assert movimiento.status_code == 201, movimiento.text

    async def test_listado_del_operator_no_incluye_cajas_ajenas(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        otro = await _otro_usuario(db_session)
        await _crear_caja(async_client, test_company.id, otro.id)
        await _crear_caja(async_client, test_company.id, usuario_de_prueba.id)
        await _cambiar_rol(db_session, usuario_de_prueba, test_company, "operator")

        r = await async_client.get("/api/v1/caja-chica/")
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["responsable_id"] == str(usuario_de_prueba.id)

    async def test_operator_no_puede_abrir_caja_para_otro(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        otro = await _otro_usuario(db_session)
        await _cambiar_rol(db_session, usuario_de_prueba, test_company, "operator")
        r = await async_client.post(
            "/api/v1/caja-chica/",
            json={
                "company_id": str(test_company.id),
                "responsable_id": str(otro.id),
                "nombre": "Caja ajena",
                "monto_inicial": "100.00",
                "umbral_alerta": "0.00",
            },
        )
        assert r.status_code == 403

    async def test_operator_no_puede_traspasar_su_caja(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        otro = await _otro_usuario(db_session)
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id
        )
        await _cambiar_rol(db_session, usuario_de_prueba, test_company, "operator")
        r = await async_client.patch(
            f"/api/v1/caja-chica/{caja['id']}",
            json={"responsable_id": str(otro.id)},
        )
        assert r.status_code == 403

    async def test_perfil_admin_no_burla_el_rol(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        otro = await _otro_usuario(db_session)
        caja = await _crear_caja(async_client, test_company.id, otro.id)
        await _cambiar_rol(db_session, usuario_de_prueba, test_company, "operator")
        usuario_de_prueba.perfil = "admin"
        db_session.add(usuario_de_prueba)
        await db_session.commit()

        assert (
            await async_client.get(f"/api/v1/caja-chica/{caja['id']}")
        ).status_code == 403

    async def test_membresia_inactiva_cierra_la_caja(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id
        )
        fila = (
            await db_session.execute(
                select(UsuarioEmpresaModel).where(
                    UsuarioEmpresaModel.user_id == usuario_de_prueba.id
                )
            )
        ).scalar_one()
        fila.activo = False
        db_session.add(fila)
        await db_session.commit()

        assert (
            await async_client.get(f"/api/v1/caja-chica/{caja['id']}")
        ).status_code == 403
