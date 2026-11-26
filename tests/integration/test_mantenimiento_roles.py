"""Mantenimiento: roles por empresa y reseteo firmado por un administrador.

Este archivo comprueba la recorrida HTTP, no el servicio aislado. La autoridad
tiene que vivir en la peticion: un rol leido del token o una etiqueta `perfil`
no sirven, porque el primero se queda viejo y el segundo no sabe en que empresa
vale.

Las pruebas usan `async_client`, que sobrescribe `get_current_user` pero NO
`get_current_user_verificado`: la contrasena del header se comprueba de verdad,
que es justo lo que aqui se quiere medir.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.enums import RolEmpresa
from app.core.security import hashear_contrasena, verificar_contrasena
from app.models.auditoria import Auditoria
from app.models.company import CompanyModel
from app.models.user import UserModel
from app.models.usuario_empresa import UsuarioEmpresaModel

CONTRASENA = "contrasena-de-prueba"
NUEVA = "nueva-clave-de-admin-123"


def _headers(company_id, contrasena=CONTRASENA):
    return {
        "X-Company-ID": str(company_id),
        "X-Contrasena-Actual": contrasena,
    }


async def _nueva_cuenta(db, nombre="Cuenta objetivo"):
    cuenta = UserModel(
        email=f"mantenimiento-{uuid4().hex[:10]}@example.com",
        nombre=nombre,
        password_hash=hashear_contrasena("clave-inicial-123"),
    )
    db.add(cuenta)
    await db.commit()
    await db.refresh(cuenta)
    return cuenta


async def _membresia(db, user_id, company_id, rol=RolEmpresa.OPERATOR, activo=True):
    fila = UsuarioEmpresaModel(
        user_id=user_id, company_id=company_id, rol=rol.value, activo=activo
    )
    db.add(fila)
    await db.commit()
    return fila


async def _auditorias(db, accion):
    filas = (
        await db.execute(select(Auditoria).where(Auditoria.accion == accion))
    ).scalars().all()
    return filas


@pytest.fixture
async def admin_contexto(db_session, usuario_de_prueba, test_company):
    """El actor es `admin` en la empresa de prueba, con contrasena real."""
    await _membresia(
        db_session, usuario_de_prueba.id, test_company.id, RolEmpresa.ADMIN
    )
    return test_company


class TestRolesPorEmpresa:
    async def test_un_operator_no_puede_listar_roles(
        self, async_client, db_session, usuario_de_prueba, test_company
    ):
        await _membresia(
            db_session, usuario_de_prueba.id, test_company.id, RolEmpresa.OPERATOR
        )
        r = await async_client.get(
            "/api/v1/usuarios/membresias", headers=_headers(test_company.id)
        )
        assert r.status_code == 403

    async def test_el_admin_ve_las_cuentas_y_sus_roles(
        self, async_client, db_session, admin_contexto, usuario_de_prueba
    ):
        objetivo = await _nueva_cuenta(db_session)
        await _membresia(
            db_session, objetivo.id, admin_contexto.id, RolEmpresa.ACCOUNTANT
        )

        r = await async_client.get(
            "/api/v1/usuarios/membresias", headers=_headers(admin_contexto.id)
        )
        assert r.status_code == 200, r.text
        filas = {f["user_id"]: f for f in r.json()}
        assert filas[str(usuario_de_prueba.id)]["rol"] == "admin"
        assert filas[str(objetivo.id)]["rol"] == "accountant"
        assert filas[str(objetivo.id)]["email"] == objetivo.email

    async def test_asignar_rol_y_auditar_la_firma(
        self, async_client, db_session, admin_contexto, usuario_de_prueba
    ):
        objetivo = await _nueva_cuenta(db_session)
        r = await async_client.post(
            "/api/v1/usuarios/membresias",
            json={"user_id": str(objetivo.id), "rol": "operator"},
            headers=_headers(admin_contexto.id),
        )
        assert r.status_code == 201, r.text
        assert r.json()["rol"] == "operator"

        fila = await db_session.get(
            UsuarioEmpresaModel, (objetivo.id, admin_contexto.id)
        )
        assert fila is not None

        auditadas = await _auditorias(db_session, "asignar_rol_empresa")
        assert len(auditadas) == 1
        assert auditadas[0].usuario_id == usuario_de_prueba.id
        assert auditadas[0].rol == "admin"
        assert str(objetivo.id) == str(auditadas[0].registro_afectado)

    async def test_la_empresa_no_se_toma_del_cuerpo(
        self, async_client, db_session, admin_contexto
    ):
        objetivo = await _nueva_cuenta(db_session)
        otra_empresa = uuid4()
        r = await async_client.post(
            "/api/v1/usuarios/membresias",
            json={
                "user_id": str(objetivo.id),
                "rol": "operator",
                "company_id": str(otra_empresa),
            },
            headers=_headers(admin_contexto.id),
        )
        assert r.status_code == 422
        assert await db_session.get(
            UsuarioEmpresaModel, (objetivo.id, otra_empresa)
        ) is None

    async def test_un_rol_activo_duplicado_da_409(
        self, async_client, db_session, admin_contexto, usuario_de_prueba
    ):
        r = await async_client.post(
            "/api/v1/usuarios/membresias",
            json={"user_id": str(usuario_de_prueba.id), "rol": "owner"},
            headers=_headers(admin_contexto.id),
        )
        assert r.status_code == 409

    async def test_cambiar_el_rol_deja_el_anterior_en_la_auditoria(
        self, async_client, db_session, admin_contexto
    ):
        objetivo = await _nueva_cuenta(db_session)
        await _membresia(
            db_session, objetivo.id, admin_contexto.id, RolEmpresa.OPERATOR
        )

        r = await async_client.patch(
            f"/api/v1/usuarios/membresias/{objetivo.id}",
            json={"rol": "accountant"},
            headers=_headers(admin_contexto.id),
        )
        assert r.status_code == 200, r.text
        assert r.json()["rol"] == "accountant"

        auditadas = await _auditorias(db_session, "cambiar_rol_empresa")
        assert len(auditadas) == 1
        assert "operator" in auditadas[0].detalles
        assert "accountant" in auditadas[0].detalles

    async def test_dar_de_baja_una_membresia_no_borra_la_fila(
        self, async_client, db_session, admin_contexto
    ):
        objetivo = await _nueva_cuenta(db_session)
        await _membresia(
            db_session, objetivo.id, admin_contexto.id, RolEmpresa.OPERATOR
        )

        r = await async_client.patch(
            f"/api/v1/usuarios/membresias/{objetivo.id}",
            json={"activo": False},
            headers=_headers(admin_contexto.id),
        )
        assert r.status_code == 200, r.text
        assert r.json()["activo"] is False
        fila = await db_session.get(
            UsuarioEmpresaModel, (objetivo.id, admin_contexto.id)
        )
        assert fila is not None
        assert fila.activo is False

    async def test_el_ultimo_admin_no_se_puede_degradar(
        self, async_client, admin_contexto, usuario_de_prueba
    ):
        r = await async_client.patch(
            f"/api/v1/usuarios/membresias/{usuario_de_prueba.id}",
            json={"rol": "operator"},
            headers=_headers(admin_contexto.id),
        )
        assert r.status_code == 409
        assert "ultima cuenta" in r.json()["detail"]

    async def test_el_ultimo_admin_no_se_puede_dar_de_baja(
        self, async_client, admin_contexto, usuario_de_prueba
    ):
        r = await async_client.patch(
            f"/api/v1/usuarios/membresias/{usuario_de_prueba.id}",
            json={"activo": False},
            headers=_headers(admin_contexto.id),
        )
        assert r.status_code == 409

    async def test_con_dos_admins_si_se_puede_dejar_sin_acceso_a_uno(
        self, async_client, db_session, admin_contexto, usuario_de_prueba
    ):
        segundo = await _nueva_cuenta(db_session, "Segundo admin")
        await _membresia(
            db_session, segundo.id, admin_contexto.id, RolEmpresa.OWNER
        )

        r = await async_client.patch(
            f"/api/v1/usuarios/membresias/{usuario_de_prueba.id}",
            json={"activo": False},
            headers=_headers(admin_contexto.id),
        )
        assert r.status_code == 200, r.text

    async def test_una_membresia_de_otra_empresa_nose_toca(
        self, async_client, db_session, admin_contexto, usuario_de_prueba
    ):
        objetivo = await _nueva_cuenta(db_session)
        otra = CompanyModel(
            name="Otra empresa", tax_id=f"OTRA{uuid4().hex[:9].upper()}"
        )
        db_session.add(otra)
        await db_session.commit()
        await db_session.refresh(otra)
        await _membresia(
            db_session, objetivo.id, otra.id, RolEmpresa.OPERATOR
        )

        r = await async_client.patch(
            f"/api/v1/usuarios/membresias/{objetivo.id}",
            json={"rol": "owner"},
            headers=_headers(admin_contexto.id),
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "Esa cuenta no tiene membresia en la empresa activa"


class TestResetAdministrativo:
    async def test_un_operator_no_puede_resetear(
        self, async_client, db_session, usuario_de_prueba, test_company
    ):
        await _membresia(
            db_session, usuario_de_prueba.id, test_company.id, RolEmpresa.OPERATOR
        )
        objetivo = await _nueva_cuenta(db_session)
        r = await async_client.post(
            f"/api/v1/usuarios/{objetivo.id}/contrasena-admin",
            json={"contrasena_nueva": NUEVA},
            headers=_headers(test_company.id),
        )
        assert r.status_code == 403

    async def test_sin_la_contrasena_del_admin_no_puede_resetear(
        self, async_client, db_session, admin_contexto
    ):
        objetivo = await _nueva_cuenta(db_session)
        r = await async_client.post(
            f"/api/v1/usuarios/{objetivo.id}/contrasena-admin",
            json={"contrasena_nueva": NUEVA},
            headers={"X-Company-ID": str(admin_contexto.id)},
        )
        assert r.status_code == 401

    async def test_la_contrasena_del_admin_equivocada_tampoco(
        self, async_client, db_session, admin_contexto
    ):
        objetivo = await _nueva_cuenta(db_session)
        r = await async_client.post(
            f"/api/v1/usuarios/{objetivo.id}/contrasena-admin",
            json={"contrasena_nueva": NUEVA},
            headers=_headers(admin_contexto.id, "no-es-la-clave"),
        )
        assert r.status_code == 401

    async def test_el_admin_reseta_y_la_clave_ajena_cambia(
        self, async_client, db_session, admin_contexto, usuario_de_prueba
    ):
        objetivo = await _nueva_cuenta(db_session)
        r = await async_client.post(
            f"/api/v1/usuarios/{objetivo.id}/contrasena-admin",
            json={"contrasena_nueva": NUEVA},
            headers=_headers(admin_contexto.id),
        )
        assert r.status_code == 204, r.text

        await db_session.refresh(objetivo)
        assert verificar_contrasena(NUEVA, objetivo.password_hash)
        assert not verificar_contrasena("clave-inicial-123", objetivo.password_hash)

        auditadas = await _auditorias(db_session, "reset_contrasena_admin")
        assert len(auditadas) == 1
        assert auditadas[0].usuario_id == usuario_de_prueba.id
        assert auditadas[0].rol == "admin"
        # La clave nueva NUNCA entra en el rastro, ni en texto ni como parte de
        # un dato que se pueda reconstruir.
        assert NUEVA not in (auditadas[0].detalles or "")

    async def test_no_es_el_cambio_propio(
        self, async_client, db_session, admin_contexto, usuario_de_prueba
    ):
        """El endpoint antiguo sigue pidiendo la clave de la cuenta objetivo.

        Si el reset administrativo se hubiera fusionado con el cambio propio,
        esta prueba daria 204 y la distincion entre "se quien eres" y "se de
        quien es esta cuenta" desapareceria.
        """
        objetivo = await _nueva_cuenta(db_session)
        r = await async_client.post(
            f"/api/v1/usuarios/{objetivo.id}/contrasena",
            json={"contrasena_actual": CONTRASENA, "contrasena_nueva": NUEVA},
            headers=_headers(admin_contexto.id),
        )
        assert r.status_code == 400
