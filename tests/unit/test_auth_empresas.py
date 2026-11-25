"""La lista de empresas del cliente viene de las membresias, no de `companies`.

Sin este endpoint el front tenia dos opciones malas: llamar a `GET /companies`
(y ofrecer empresas ajenas) o leer el `perfil` del token (que es texto libre y
no autoriza nada en una empresa concreta). Aqui se comprueba que la lista
devuelta es exactamente la que la BD dice que la cuenta puede usar.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from app.models.company import CompanyModel
from app.models.usuario_empresa import UsuarioEmpresaModel


async def _empresa(db_session, nombre):
    empresa = CompanyModel(name=nombre, tax_id=f"EMP{uuid4().hex[:10].upper()}")
    db_session.add(empresa)
    await db_session.flush()
    await db_session.refresh(empresa)
    return empresa


async def _membership(db_session, usuario, empresa, rol="admin", activo=True):
    db_session.add(
        UsuarioEmpresaModel(
            user_id=usuario.id,
            company_id=empresa.id,
            rol=rol,
            activo=activo,
        )
    )
    await db_session.commit()


class TestMisEmpresas:
    async def test_solo_devuelve_membresias_activas(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        await _membership(db_session, usuario_de_prueba, test_company, "admin")
        inactiva = await _empresa(db_session, "Empresa dada de baja")
        await _membership(
            db_session, usuario_de_prueba, inactiva, "admin", activo=False
        )

        r = await async_client.get("/api/v1/auth/empresas")
        assert r.status_code == 200, r.text
        assert [m["company_id"] for m in r.json()] == [str(test_company.id)]
        assert r.json()[0]["rol"] == "admin"

    async def test_el_rol_es_el_de_la_empresa(
        self, async_client, db_session, usuario_de_prueba
    ):
        primera = await _empresa(db_session, "Primera")
        segunda = await _empresa(db_session, "Segunda")
        await _membership(db_session, usuario_de_prueba, primera, "admin")
        await _membership(db_session, usuario_de_prueba, segunda, "operator")

        r = await async_client.get("/api/v1/auth/empresas")
        assert r.status_code == 200, r.text
        roles = {m["company_id"]: m["rol"] for m in r.json()}
        assert roles[str(primera.id)] == "admin"
        assert roles[str(segunda.id)] == "operator"

    async def test_un_cambio_de_rol_se_ve_sin_nuevo_token(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        await _membership(db_session, usuario_de_prueba, test_company, "operator")
        assert (await async_client.get("/api/v1/auth/empresas")).json()[0]["rol"] == "operator"

        fila = (
            await db_session.execute(
                select(UsuarioEmpresaModel).where(
                    UsuarioEmpresaModel.user_id == usuario_de_prueba.id,
                    UsuarioEmpresaModel.company_id == test_company.id,
                )
            )
        ).scalar_one()
        fila.rol = "accountant"
        db_session.add(fila)
        await db_session.commit()

        assert (await async_client.get("/api/v1/auth/empresas")).json()[0]["rol"] == "accountant"

    async def test_no_filtra_otras_cuentas(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        otra_empresa = await _empresa(db_session, "Ajena")
        # CompanyModel no tiene user_id: la unica forma de que esta empresa
        # aparezca seria una membresia. No la hay, asi que no debe salir.
        r = await async_client.get("/api/v1/auth/empresas")
        assert r.status_code == 200, r.text
        assert [m["company_id"] for m in r.json()] == []
        assert str(otra_empresa.id) not in str(r.json())
