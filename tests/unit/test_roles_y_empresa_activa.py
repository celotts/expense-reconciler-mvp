"""La empresa activa y el rol se decid en BD, no en el token ni en `perfil`.

Cada test aqui tiene que MORIR si se quita la defensa correspondiente. Por eso
las peticiones pasan por un FastAPI real con las dependencias montadas: mirar
el retorno de `empresa_activa()` no demostraria que un endpoint la usa.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.deps import EmpresaActiva, get_current_user, get_db, requiere_rol
from app.core.enums import RolEmpresa
from app.models.company import CompanyModel
from app.models.usuario_empresa import UsuarioEmpresaModel

RAIZ = Path(__file__).resolve().parents[2]

prueba = FastAPI()


@prueba.get("/empresa")
async def _empresa(company_id: EmpresaActiva):
    return {"company_id": str(company_id)}


@prueba.get("/solo-admin")
async def _solo_admin(
    rol: Annotated[RolEmpresa, Depends(requiere_rol(RolEmpresa.ADMIN))],
):
    return {"rol": rol.value}


@pytest.fixture
async def cliente(db_session, usuario_de_prueba):
    async def override_get_db():
        yield db_session

    async def override_get_current_user():
        return usuario_de_prueba

    prueba.dependency_overrides[get_db] = override_get_db
    prueba.dependency_overrides[get_current_user] = override_get_current_user
    transport = ASGITransport(app=prueba)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    prueba.dependency_overrides.clear()


async def _membership(db_session, usuario, company, rol=RolEmpresa.ADMIN, activo=True):
    fila = UsuarioEmpresaModel(
        user_id=usuario.id,
        company_id=company.id,
        rol=rol.value,
        activo=activo,
    )
    db_session.add(fila)
    await db_session.commit()
    return fila


@pytest.fixture
async def segunda_empresa(db_session):
    company = CompanyModel(name="Otra", tax_id=f"OTRA{uuid4().hex[:9].upper()}")
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)
    return company


class TestEmpresaActiva:
    async def test_sin_membership_no_entra(self, cliente, db_session, usuario_de_prueba):
        r = await cliente.get("/empresa")
        assert r.status_code == 403
        assert "ninguna empresa" in r.json()["detail"]

    async def test_membership_inactiva_no_entra(
        self, cliente, db_session, usuario_de_prueba, test_company
    ):
        await _membership(
            db_session, usuario_de_prueba, test_company, activo=False
        )
        r = await cliente.get("/empresa")
        assert r.status_code == 403

    async def test_una_empresa_sin_header_funciona(
        self, cliente, db_session, usuario_de_prueba, test_company
    ):
        """Compatibilidad hacia atras: los clientes viejos no envian header."""
        await _membership(db_session, usuario_de_prueba, test_company)
        r = await cliente.get("/empresa")
        assert r.status_code == 200
        assert r.json()["company_id"] == str(test_company.id)

    async def test_dos_empresas_sin_header_piden_eleccion(
        self, cliente, db_session, usuario_de_prueba, test_company, segunda_empresa
    ):
        await _membership(db_session, usuario_de_prueba, test_company)
        await _membership(db_session, usuario_de_prueba, segunda_empresa)
        r = await cliente.get("/empresa")
        assert r.status_code == 409
        assert "X-Company-ID" in r.json()["detail"]

    async def test_header_elige_empresa(
        self, cliente, db_session, usuario_de_prueba, test_company, segunda_empresa
    ):
        await _membership(db_session, usuario_de_prueba, test_company)
        await _membership(db_session, usuario_de_prueba, segunda_empresa)
        r = await cliente.get(
            "/empresa", headers={"X-Company-ID": str(segunda_empresa.id)}
        )
        assert r.status_code == 200
        assert r.json()["company_id"] == str(segunda_empresa.id)

    async def test_empresa_ajena_se_rechaza(
        self, cliente, db_session, usuario_de_prueba, test_company, segunda_empresa
    ):
        await _membership(db_session, usuario_de_prueba, test_company)
        r = await cliente.get(
            "/empresa", headers={"X-Company-ID": str(segunda_empresa.id)}
        )
        assert r.status_code == 403

    async def test_header_basura_es_400(
        self, cliente, db_session, usuario_de_prueba, test_company
    ):
        await _membership(db_session, usuario_de_prueba, test_company)
        r = await cliente.get("/empresa", headers={"X-Company-ID": "no-es-un-uuid"})
        assert r.status_code == 400


class TestRolPorEmpresa:
    async def test_admin_pasa(
        self, cliente, db_session, usuario_de_prueba, test_company
    ):
        await _membership(db_session, usuario_de_prueba, test_company, RolEmpresa.ADMIN)
        r = await cliente.get("/solo-admin")
        assert r.status_code == 200
        assert r.json()["rol"] == "admin"

    async def test_operator_no_pasa(
        self, cliente, db_session, usuario_de_prueba, test_company
    ):
        await _membership(
            db_session, usuario_de_prueba, test_company, RolEmpresa.OPERATOR
        )
        r = await cliente.get("/solo-admin")
        assert r.status_code == 403

    async def test_perfil_no_es_autorizacion(
        self, cliente, db_session, usuario_de_prueba, test_company
    ):
        """`perfil='admin'` con membership `operator` NO abre la puerta."""
        usuario_de_prueba.perfil = "admin"
        db_session.add(usuario_de_prueba)
        await _membership(
            db_session, usuario_de_prueba, test_company, RolEmpresa.OPERATOR
        )
        r = await cliente.get("/solo-admin")
        assert r.status_code == 403

    async def test_cambio_de_rol_en_bd_surte_sin_nuevo_token(
        self, cliente, db_session, usuario_de_prueba, test_company
    ):
        fila = await _membership(
            db_session, usuario_de_prueba, test_company, RolEmpresa.OPERATOR
        )
        assert (await cliente.get("/solo-admin")).status_code == 403

        fila.rol = RolEmpresa.ADMIN.value
        db_session.add(fila)
        await db_session.commit()

        assert (await cliente.get("/solo-admin")).status_code == 200

    async def test_rol_es_por_empresa(
        self, cliente, db_session, usuario_de_prueba, test_company, segunda_empresa
    ):
        await _membership(db_session, usuario_de_prueba, test_company, RolEmpresa.ADMIN)
        await _membership(
            db_session, usuario_de_prueba, segunda_empresa, RolEmpresa.OPERATOR
        )
        assert (
            await cliente.get(
                "/solo-admin", headers={"X-Company-ID": str(test_company.id)}
            )
        ).status_code == 200
        assert (
            await cliente.get(
                "/solo-admin", headers={"X-Company-ID": str(segunda_empresa.id)}
            )
        ).status_code == 403


class TestElBackfillNoRompeLoExistente:
    """La migracion no puede dejar a una cuenta existente sin acceso."""

    def test_la_migracion_seed_admin_en_todas_las_empresas(self):
        sql = (RAIZ / "db" / "migrations" / "0022_roles_y_empresa_activa.sql").read_text(
            encoding="utf-8"
        )
        assert "CROSS JOIN companies" in sql
        assert "'admin'" in sql
        assert "ON CONFLICT (user_id, company_id) DO NOTHING" in sql
