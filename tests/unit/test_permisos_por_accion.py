"""Permisos por accion, medidos en la peticion HTTP y no solo en el mapa.

`viewer` no basta con que no aparezca en el menu: si alguien llama a la ruta con
curl, la unica respuesta aceptable es 403. Y un `operator` no puede aprobar ni
conciliar aunque esos verbos sean POST: el verbo no decide la accion, la ruta si.

Los tests de mapa y los de HTTP se complementan. El mapa dice que la traduccion
es correcta; la peticion dice que alguien la usa.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.deps import _permiso_de_la_peticion
from app.core.enums import RolEmpresa
from app.core.permisos import (
    Permiso,
    permisos_de_rol,
    roles_validos,
    tiene_permiso,
)
from app.models.usuario_empresa import UsuarioEmpresaModel

RAIZ = Path(__file__).resolve().parents[2]
PREFIJO = "/api/v1"


def _peticion(metodo: str, ruta: str):
    """Solo lo que `_permiso_de_la_peticion` lee, sin montar un ASGI completo."""
    return SimpleNamespace(method=metodo, url=SimpleNamespace(path=ruta))


async def _membresia(db_session, usuario, empresa, rol):
    db_session.add(
        UsuarioEmpresaModel(
            user_id=usuario.id,
            company_id=empresa.id,
            rol=rol,
            activo=True,
        )
    )
    await db_session.commit()


async def _cambiar_rol(db_session, usuario, empresa, rol):
    membresia = (
        await db_session.execute(
            select(UsuarioEmpresaModel).where(
                UsuarioEmpresaModel.user_id == usuario.id,
                UsuarioEmpresaModel.company_id == empresa.id,
            )
        )
    ).scalar_one()
    membresia.rol = rol
    await db_session.commit()


@pytest.fixture
async def empresa_con_viewer(db_session, test_company, usuario_de_prueba):
    await _membresia(db_session, usuario_de_prueba, test_company, RolEmpresa.VIEWER)
    return test_company


class TestElMapaDePermisos:
    def test_viewer_solo_lee(self):
        assert permisos_de_rol(RolEmpresa.VIEWER) == ("leer",)
        assert set(permisos_de_rol("viewer")) == {"leer"}

    @pytest.mark.parametrize(
        "permiso",
        [p for p in Permiso if p is not Permiso.LEER],
    )
    def test_viewer_no_tiene_ninguna_otra_accion(self, permiso):
        assert not tiene_permiso("viewer", permiso)

    def test_operator_no_aprueba_ni_concilia_ni_exporta(self):
        for permiso in (
            Permiso.APROBAR,
            Permiso.CONCILIAR,
            Permiso.EXPORTAR,
            Permiso.ADMINISTRAR,
            Permiso.AUDITAR,
        ):
            assert not tiene_permiso(RolEmpresa.OPERATOR, permiso)
        assert tiene_permiso(RolEmpresa.OPERATOR, Permiso.CREAR)
        assert tiene_permiso(RolEmpresa.OPERATOR, Permiso.CERRAR)

    def test_un_rol_inexistente_no_hereda_admin(self):
        assert permisos_de_rol("administrador-del-universo") == ("leer",)
        assert roles_validos(["owner", "inexistente", "viewer"]) == (
            RolEmpresa.OWNER, RolEmpresa.VIEWER,
        )
        assert not tiene_permiso("inexistente", Permiso.CREAR)


class TestLaBaseAceptaElRol:
    @pytest.mark.parametrize(
        "archivo",
        [
            "db/migrations/0024_rol_viewer_y_permisos.sql",
            "db/init.sql",
        ],
    )
    def test_el_check_de_la_base_incluye_viewer(self, archivo):
        contenido = (RAIZ / archivo).read_text(encoding="utf-8")
        assert "ck_usuarios_empresas_rol" in contenido
        assert "'viewer'" in contenido

    def test_una_base_nueva_no_nace_sin_la_migracion(self):
        contenido = (RAIZ / "db/init.sql").read_text(encoding="utf-8")
        assert "0024_rol_viewer_y_permisos.sql" in contenido


class TestLaAccionNoLaDaElVerbo:
    @pytest.mark.parametrize(
        "metodo,ruta,esperado",
        [
            ("POST", f"{PREFIJO}/reconciliations/run", Permiso.CONCILIAR),
            ("PATCH", f"{PREFIJO}/tickets/{uuid4()}/review", Permiso.APROBAR),
            ("POST", f"{PREFIJO}/tickets/{uuid4()}/spot-check", Permiso.APROBAR),
            ("POST", f"{PREFIJO}/tipos-gasto/tickets/{uuid4()}/autorizar", Permiso.APROBAR),
            ("POST", f"{PREFIJO}/tipos-gasto/tickets/{uuid4()}/rechazar", Permiso.APROBAR),
            ("POST", f"{PREFIJO}/caja-chica/{uuid4()}/cerrar", Permiso.CERRAR),
            ("DELETE", f"{PREFIJO}/companies/{uuid4()}", Permiso.ADMINISTRAR),
            ("POST", f"{PREFIJO}/usuarios", Permiso.ADMINISTRAR),
            ("POST", f"{PREFIJO}/auditoria/evento", Permiso.AUDITAR),
            ("GET", f"{PREFIJO}/reports/cierre-mensual.pdf", Permiso.EXPORTAR),
            ("GET", f"{PREFIJO}/reconciliations/export/excel", Permiso.EXPORTAR),
            ("GET", f"{PREFIJO}/sistema/backup", Permiso.EXPORTAR),
            ("POST", f"{PREFIJO}/duplicados/buscar", None),
            ("GET", f"{PREFIJO}/tickets/", None),
            ("POST", f"{PREFIJO}/tickets/", Permiso.CREAR),
        ],
    )
    def test_clasificacion_de_la_ruta(self, metodo, ruta, esperado):
        assert _permiso_de_la_peticion(_peticion(metodo, ruta)) is esperado


class TestLaGuardiaEnLaApi:
    async def test_viewer_recibe_los_permisos_del_servidor(
        self, async_client, empresa_con_viewer
    ):
        respuesta = await async_client.get("/api/v1/auth/empresas")
        assert respuesta.status_code == 200
        fila = next(m for m in respuesta.json() if m["company_id"] == str(empresa_con_viewer.id))
        assert fila["rol"] == "viewer"
        assert fila["permisos"] == ["leer"]

    async def test_viewer_puede_leer(self, async_client, empresa_con_viewer):
        respuesta = await async_client.get(
            "/api/v1/tickets/", headers={"X-Company-ID": str(empresa_con_viewer.id)}
        )
        assert respuesta.status_code == 200

    async def test_una_cabecera_basura_no_salta_la_guardia(
        self, async_client, empresa_con_viewer
    ):
        respuesta = await async_client.post(
            "/api/v1/auditoria/evento",
            json={"accion": "navegar", "modulo": "frontend"},
            headers={"X-Company-ID": "no-es-un-uuid"},
        )
        assert respuesta.status_code == 400

    async def test_viewer_no_puede_borrar(self, async_client, empresa_con_viewer):
        respuesta = await async_client.delete(
            f"/api/v1/companies/{empresa_con_viewer.id}",
            headers={"X-Company-ID": str(empresa_con_viewer.id)},
        )
        assert respuesta.status_code == 403
        assert "administrar" in respuesta.json()["detail"]

    async def test_viewer_no_puede_exportar(self, async_client, empresa_con_viewer):
        respuesta = await async_client.get(
            f"/api/v1/reports/cierre-mensual.pdf?company_id={empresa_con_viewer.id}&periodo=2026-10",
            headers={"X-Company-ID": str(empresa_con_viewer.id)},
        )
        assert respuesta.status_code == 403
        assert "exportar" in respuesta.json()["detail"]

    async def test_viewer_no_puede_aprobar(self, async_client, empresa_con_viewer):
        respuesta = await async_client.patch(
            f"/api/v1/tickets/{uuid4()}/review",
            json={"action": "approve"},
            headers={"X-Company-ID": str(empresa_con_viewer.id)},
        )
        assert respuesta.status_code == 403
        assert "aprobar" in respuesta.json()["detail"]

    async def test_viewer_no_puede_conciliar(self, async_client, empresa_con_viewer):
        respuesta = await async_client.post(
            "/api/v1/reconciliations/run",
            json={"company_id": str(empresa_con_viewer.id)},
            headers={"X-Company-ID": str(empresa_con_viewer.id)},
        )
        assert respuesta.status_code == 403
        assert "conciliar" in respuesta.json()["detail"]

    async def test_operator_tampoco_puede_aprobar_ni_exportar(
        self, async_client, db_session, usuario_de_prueba, empresa_con_viewer
    ):
        await _cambiar_rol(db_session, usuario_de_prueba, empresa_con_viewer, RolEmpresa.OPERATOR)
        cabecera = {"X-Company-ID": str(empresa_con_viewer.id)}
        aprobacion = await async_client.patch(
            f"/api/v1/tickets/{uuid4()}/review", json={"action": "approve"}, headers=cabecera
        )
        exportacion = await async_client.get(
            f"/api/v1/reports/cierre-mensual.pdf?company_id={empresa_con_viewer.id}&periodo=2026-10",
            headers=cabecera,
        )
        assert aprobacion.status_code == 403
        assert exportacion.status_code == 403

    async def test_un_rol_no_reconocido_da_403_y_no_500(
        self, async_client, db_session, usuario_de_prueba, empresa_con_viewer
    ):
        # SQLite no aplica el CHECK de Postgres, asi que aqui se puede escribir
        # el dato imposible que una base restaurada podria traer.
        await _cambiar_rol(db_session, usuario_de_prueba, empresa_con_viewer, "rol_inexistente")
        respuesta = await async_client.post(
            "/api/v1/auditoria/evento",
            json={"accion": "navegar", "modulo": "frontend"},
            headers={"X-Company-ID": str(empresa_con_viewer.id)},
        )
        assert respuesta.status_code == 403

    async def test_admin_pasa_la_guardia(
        self, async_client, db_session, usuario_de_prueba, empresa_con_viewer
    ):
        await _cambiar_rol(db_session, usuario_de_prueba, empresa_con_viewer, RolEmpresa.ADMIN)
        # Una empresa que no existe da 404, no 403: la guardia dejo pasar y fue
        # el endpoint quien decidio. Es la prueba de que no es un denegador
        # global disfrazado de permisos.
        respuesta = await async_client.delete(
            f"/api/v1/companies/{uuid4()}",
            headers={"X-Company-ID": str(empresa_con_viewer.id)},
        )
        assert respuesta.status_code == 404
